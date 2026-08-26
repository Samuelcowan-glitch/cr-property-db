import os
import re
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta

app = Flask(__name__)
# Use DATABASE_URL from environment (Railway/Postgres) or fall back to local SQLite
_db_url = os.environ.get('DATABASE_URL', 'sqlite:///property.db')
# Railway gives postgres:// but SQLAlchemy needs postgresql://
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url

# Session signing key. There is deliberately no hard-coded fallback: this
# repository is public, so a known key would let anyone forge an admin session
# cookie and read the whole client database without a password. Without
# SECRET_KEY set, a random one is generated per boot — safe, but everyone is
# signed out on each deploy, which is the nudge to set the variable.
_secret = os.environ.get('SECRET_KEY')
if not _secret:
    import secrets as _secrets
    _secret = _secrets.token_hex(32)
    print('WARNING: SECRET_KEY is not set. Using a random key — logins will be '
          'dropped on every restart. Set SECRET_KEY in the Railway variables.')
app.config['SECRET_KEY'] = _secret

# Cookies carry access to client personal data: keep them off JavaScript, off
# plain HTTP, and out of cross-site requests.
_local = _db_url.startswith('sqlite')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=not _local,
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE='Lax',
    REMEMBER_COOKIE_SECURE=not _local,
    REMEMBER_COOKIE_DURATION=timedelta(days=14),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=30 * 1024 * 1024,      # 30 MB cap on any upload
)

db = SQLAlchemy(app)

# The public API is only ever called by the website. Anything else has no
# business reading it from a browser.
CORS(app, resources={r'/api/*': {'origins': [
    'https://cowanandrutter.com',
    'https://www.cowanandrutter.com',
    'https://samuelcowan-glitch.github.io',
]}})

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access the database.'
login_manager.login_message_category = 'warning'

DEFAULT_PASSWORD = 'changeme'


# ── Cross-site request forgery ───────────────────────────────────────────────
# Every state-changing request must carry a token tied to the session. Without
# this, a page on another site could make your browser POST to the CRM using
# your logged-in session — deleting records or changing data silently.

CSRF_FIELD = '_csrf'
CSRF_EXEMPT = {'api_enquiry'}          # public website endpoint, rate-limited instead


def csrf_token():
    """The token for this session, created on first use."""
    import secrets as _s
    if CSRF_FIELD not in session:
        session[CSRF_FIELD] = _s.token_urlsafe(32)
    return session[CSRF_FIELD]


app.jinja_env.globals['csrf_token'] = csrf_token
app.jinja_env.globals['timedelta'] = timedelta


@app.before_request
def _csrf_protect():
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return
    if request.endpoint in CSRF_EXEMPT:
        return
    # app.config['TESTING'] can only be set inside the process, never by a
    # request, so this cannot be used to get around the check in production.
    if app.config.get('TESTING'):
        return
    import hmac
    sent = (request.form.get(CSRF_FIELD)
            or request.headers.get('X-CSRF-Token')
            or (request.get_json(silent=True) or {}).get(CSRF_FIELD) or '')
    expected = session.get(CSRF_FIELD, '')
    if not expected or not hmac.compare_digest(str(sent), str(expected)):
        app.logger.warning('CSRF check failed for %s from %s', request.endpoint, _login_key())
        abort(400, description='Your session expired or the form was not submitted from this site. '
                               'Please reload the page and try again.')


_FORM_TAG = re.compile(r'<form\b[^>]*>', re.I)


@app.after_request
def _csrf_inject(resp):
    """Put the token in every form and in a meta tag, on the way out.

    Doing it here rather than in each template means no form can be added later
    without protection, and nothing needs a token pasted into it by hand.
    """
    ctype = (resp.headers.get('Content-Type') or '')
    if not ctype.startswith('text/html') or resp.direct_passthrough:
        return resp
    try:
        html_body = resp.get_data(as_text=True)
    except (UnicodeDecodeError, RuntimeError):
        return resp
    if '<form' not in html_body and '</head>' not in html_body:
        return resp

    token = csrf_token()
    field = f'<input type="hidden" name="{CSRF_FIELD}" value="{token}">'

    def add(match):
        tag = match.group(0)
        if re.search(r'method\s*=\s*["\']?post', tag, re.I) is None:
            return tag                      # GET forms need no token
        return tag + field

    html_body = _FORM_TAG.sub(add, html_body)
    # so fetch() calls can send the token as a header
    html_body = html_body.replace('</head>',
        f'<meta name="csrf-token" content="{token}"></head>', 1)
    resp.set_data(html_body)
    return resp


@app.after_request
def _security_headers(resp):
    """Standard hardening headers.

    nosniff matters most here: uploaded files are served back from this origin,
    so a file that claims to be an image but contains HTML must never be
    rendered as a page.
    """
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
    # The CRM serves its own scripts and styles. 'unsafe-inline' is still needed
    # for the inline handlers and style attributes throughout the templates;
    # removing those is the next step to a stricter policy.
    resp.headers.setdefault('Content-Security-Policy',
        "default-src 'self'; "
        "img-src 'self' data: https://web-production-3d01.up.railway.app https://images.unsplash.com; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'")
    if not _local:
        resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return resp


# ── Login throttling ─────────────────────────────────────────────────────────
# Small in-memory counter — enough to stop password guessing against a single
# admin account on a single web process. A managed WAF would do this properly.
_LOGIN_FAILURES = {}
LOGIN_MAX_ATTEMPTS = 8
LOGIN_LOCKOUT_MINUTES = 15


def _login_key():
    fwd = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    return fwd or request.remote_addr or 'unknown'


def _login_blocked():
    rec = _LOGIN_FAILURES.get(_login_key())
    if not rec:
        return 0
    count, until = rec
    if count >= LOGIN_MAX_ATTEMPTS and until > datetime.utcnow():
        return int((until - datetime.utcnow()).total_seconds() // 60) + 1
    return 0


def _login_failed():
    key = _login_key()
    count = _LOGIN_FAILURES.get(key, (0, None))[0] + 1
    _LOGIN_FAILURES[key] = (count, datetime.utcnow() + timedelta(minutes=LOGIN_LOCKOUT_MINUTES))


def _login_succeeded():
    _LOGIN_FAILURES.pop(_login_key(), None)


# ── Models ─────────────────────────────────────────────────────────────────

class Property(db.Model):
    __tablename__ = 'properties'
    id = db.Column(db.Integer, primary_key=True)
    address = db.Column(db.String(255), nullable=False)
    postcode = db.Column(db.String(20), nullable=False)
    property_type = db.Column(db.String(50))          # Office, Retail, Industrial, etc.
    size = db.Column(db.Float)
    measurement_type = db.Column(db.String(10))        # GIA, NIA, GEA
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ── Residential use type ──
    residential_use   = db.Column(db.String(30))  # Owner Occupied, HMO, Residential Investment, Vacant

    # ── Website listing fields ──
    website_listed    = db.Column(db.Boolean, default=False)
    website_category  = db.Column(db.String(20))   # commercial / residential
    listing_status    = db.Column(db.String(20))   # available / under-offer / let-agreed / sold
    featured          = db.Column(db.Boolean, default=False)
    area              = db.Column(db.String(100))  # e.g. Chelsea, Fulham
    use_class         = db.Column(db.String(30))   # office / retail / industrial (commercial)
    listing_price     = db.Column(db.Float)
    listing_price_unit= db.Column(db.String(10))   # pa / pcm / sale / poa
    price_display     = db.Column(db.String(100))  # optional custom price string
    beds              = db.Column(db.Integer)
    baths             = db.Column(db.Integer)
    lat               = db.Column(db.Float)
    lng               = db.Column(db.Float)
    photo_id          = db.Column(db.String(100))
    blurb             = db.Column(db.Text)
    listing_size      = db.Column(db.Float)
    listing_size_unit = db.Column(db.String(20))
    # ── Attachments ──
    brochure_data     = db.Column(db.LargeBinary)
    brochure_filename = db.Column(db.String(255))
    brochure_size     = db.Column(db.Integer)
    floor_plan_data   = db.Column(db.LargeBinary)
    floor_plan_filename = db.Column(db.String(255))
    floor_plan_size   = db.Column(db.Integer)

    transactions = db.relationship('Transaction', backref='property', lazy=True, cascade='all, delete-orphan')
    projects = db.relationship('Project', backref='property', lazy=True, cascade='all, delete-orphan')

    @property
    def tenures(self):
        return [t for t in self.transactions if t.transaction_type == 'Leasehold']

    @property
    def display_size(self):
        if self.size and self.measurement_type:
            return f"{self.size:,.0f} sq ft ({self.measurement_type})"
        return '—'


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False)
    transaction_type = db.Column(db.String(20), nullable=False)   # Capital, Leasehold
    tenure_type = db.Column(db.String(30))                         # Freehold, Leasehold, Sub-lease
    transaction_date = db.Column(db.Date)
    value = db.Column(db.Float)
    vendor = db.Column(db.String(255))
    purchaser = db.Column(db.String(255))
    landlord = db.Column(db.String(255))
    tenant = db.Column(db.String(255))
    lease_start = db.Column(db.Date)
    lease_end = db.Column(db.Date)
    rent_pa = db.Column(db.Float)
    break_clause = db.Column(db.String(100))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ── Who handled the transaction ──
    done_by           = db.Column(db.String(20))    # CR / Third Party
    third_party_name  = db.Column(db.String(255))   # name if third party

    # ── Leasehold detail fields ──
    part_or_floor     = db.Column(db.String(100))
    source            = db.Column(db.String(50))    # Agent / Direct / Auction etc.
    source_contact    = db.Column(db.String(255))
    nda               = db.Column(db.Boolean, default=False)
    size_units        = db.Column(db.String(20))    # sq ft / sq m / acres
    size_basis        = db.Column(db.String(20))    # GIA / NIA / GEA / IPMS
    demise_description= db.Column(db.Text)
    incentive_years   = db.Column(db.Float)         # rent-free period (years)
    headline_rate     = db.Column(db.Float)         # headline rent £
    headline_rate_unit= db.Column(db.String(10))    # pa / pcm / psf
    net_rate          = db.Column(db.Float)         # net effective rent £
    next_break_date   = db.Column(db.Date)
    no_break          = db.Column(db.Boolean, default=False)
    next_review_date  = db.Column(db.Date)
    no_review         = db.Column(db.Boolean, default=False)
    review_type       = db.Column(db.String(30))    # Open Market / RPI / Fixed / Stepped
    repair            = db.Column(db.String(20))    # FRI / IRI / Internal
    alienation        = db.Column(db.String(50))
    primary_use_class = db.Column(db.String(20))    # E / B2 / B8 / C1 etc.
    lt_act            = db.Column(db.String(30))    # Inside / Outside 1954 Act
    epc_rating        = db.Column(db.String(5))     # A-G
    fitted            = db.Column(db.String(20))    # Shell & Core / Cat A / Cat A+ / Cat B

    # ── Capital analysis fields ──
    description       = db.Column(db.Text)         # property description (for comps)
    niy               = db.Column(db.Float)         # Net Initial Yield %  (commercial)
    giy               = db.Column(db.Float)         # Gross Initial Yield % (residential investment)
    capital_rate_psf  = db.Column(db.Float)         # £ per sq ft
    wault             = db.Column(db.Float)         # Weighted Avg Unexpired Lease Term (years)
    passing_income    = db.Column(db.Float)         # £ pa
    income_pct        = db.Column(db.Float)         # % income producing
    erv               = db.Column(db.Float)         # Estimated Rental Value £ pa
    tenant_covenant   = db.Column(db.String(50))    # Strong / Satisfactory / Weak
    written_analysis  = db.Column(db.Text)          # free-text analysis

    @property
    def display_value(self):
        if self.value:
            return f"£{self.value:,.0f}"
        return '—'

    @property
    def display_rent(self):
        if self.rent_pa:
            return f"£{self.rent_pa:,.0f} p.a."
        return '—'

    @property
    def parties(self):
        if self.transaction_type == 'Capital':
            parts = []
            if self.vendor:
                parts.append(f"Vendor: {self.vendor}")
            if self.purchaser:
                parts.append(f"Purchaser: {self.purchaser}")
            return ' | '.join(parts) if parts else '—'
        else:
            parts = []
            if self.landlord:
                parts.append(f"Landlord: {self.landlord}")
            if self.tenant:
                parts.append(f"Tenant: {self.tenant}")
            return ' | '.join(parts) if parts else '—'


# ── CRM Models ─────────────────────────────────────────────────────────────

# CRM applicant/contact + organisation lifecycle statuses (one shared vocabulary).
CONTACT_STATUSES = [
    'New Enquiry', 'Active Requirement', 'Prospect', 'Under Offer',
    'Current Tenant', 'Requirement Satisfied', 'Inactive', 'Archived',
]
# Statuses hidden from the default "active" list views.
ARCHIVED_STATUSES = ['Archived']


class Organisation(db.Model):
    __tablename__ = 'organisations'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    org_type = db.Column(db.String(50))   # Client, Agent, Solicitor, Developer, Investor, Other
    status = db.Column(db.String(30), default='Prospect')
    address = db.Column(db.String(255))
    postcode = db.Column(db.String(20))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    website = db.Column(db.String(200))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    contacts = db.relationship('Contact', backref='organisation', lazy=True)


class Contact(db.Model):
    __tablename__ = 'contacts'
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    job_title = db.Column(db.String(100))
    organisation_id = db.Column(db.Integer, db.ForeignKey('organisations.id'), nullable=True)
    phone = db.Column(db.String(50))
    mobile = db.Column(db.String(50))
    email = db.Column(db.String(120))
    contact_type = db.Column(db.String(50))
    notes        = db.Column(db.Text)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    # Applicant requirements (for matching)
    req_category      = db.Column(db.String(20))   # commercial / residential
    req_property_type = db.Column(db.String(100))  # e.g. Office Suite, Apartment
    req_use_class     = db.Column(db.String(30))   # office / retail / industrial
    req_area          = db.Column(db.String(200))  # preferred area(s), comma separated
    req_size_min      = db.Column(db.Float)
    req_size_max      = db.Column(db.Float)
    req_budget_min    = db.Column(db.Float)
    req_budget_max    = db.Column(db.Float)
    req_budget_unit   = db.Column(db.String(10))   # pa / pcm / sale
    req_notes         = db.Column(db.Text)          # special requirements
    # Lifecycle status + follow-up / requirement extras
    status            = db.Column(db.String(30), default='Prospect')
    preferred_move_in = db.Column(db.Date)
    lease_length      = db.Column(db.String(50))
    assigned_agent    = db.Column(db.String(100))
    last_contact_date = db.Column(db.Date)
    next_follow_up    = db.Column(db.Date)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def follow_up_overdue(self):
        return bool(self.status not in ARCHIVED_STATUSES
                    and self.next_follow_up and self.next_follow_up < date.today())

    @property
    def last_activity(self):
        return self.activities[0] if self.activities else None


class ContactActivity(db.Model):
    """Activity/history log for a contact OR an organisation: status changes, notes,
    and logged interactions. Status changes record old->new with the date/time."""
    __tablename__ = 'contact_activities'
    id = db.Column(db.Integer, primary_key=True)
    contact_id      = db.Column(db.Integer, db.ForeignKey('contacts.id'), nullable=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey('organisations.id'), nullable=True)
    kind       = db.Column(db.String(30), default='note')  # status_change / note / interaction / enquiry
    body       = db.Column(db.Text)
    old_status = db.Column(db.String(30))
    new_status = db.Column(db.String(30))
    author     = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    contact = db.relationship('Contact', backref=db.backref(
        'activities', lazy=True, cascade='all, delete-orphan',
        order_by='ContactActivity.created_at.desc()'))
    organisation = db.relationship('Organisation', backref=db.backref(
        'activities', lazy=True, cascade='all, delete-orphan',
        order_by='ContactActivity.created_at.desc()'))


class Enquiry(db.Model):
    __tablename__ = 'enquiries'
    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(255), nullable=False)
    enquiry_type = db.Column(db.String(50))
    status = db.Column(db.String(20), default='Open')  # Open, Won, Lost, On Hold
    source = db.Column(db.String(30))  # Website / Zoopla / Rightmove / Email / Manual
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contacts.id'), nullable=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey('organisations.id'), nullable=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    fee_earner = db.Column(db.String(100))
    received_date = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Applicant requirements
    req_size_min    = db.Column(db.Float)
    req_size_max    = db.Column(db.Float)
    req_budget_min  = db.Column(db.Float)
    req_budget_max  = db.Column(db.Float)
    req_budget_unit = db.Column(db.String(10))  # pa / pcm / sale
    req_use_class   = db.Column(db.String(30))
    req_category    = db.Column(db.String(20))  # commercial / residential
    req_area          = db.Column(db.String(120))  # preferred location
    req_property_type = db.Column(db.String(60))
    req_tenure        = db.Column(db.String(20))   # lease / purchase / either
    req_occupation_date = db.Column(db.Date)       # when they want to be in
    req_notes         = db.Column(db.Text)         # anything else they have asked for
    # How the applicant prefers to be reached, and how hard this is being pushed
    preferred_contact = db.Column(db.String(20))   # phone / email / mobile / post
    priority          = db.Column(db.String(10))   # High / Medium / Low
    # Follow-up tracking
    last_contact_date = db.Column(db.Date)
    next_follow_up    = db.Column(db.Date)
    next_action       = db.Column(db.String(255))
    next_call_date    = db.Column(db.Date)
    # Kept out of the working list without being deleted.
    archived          = db.Column(db.Boolean, default=False)
    # Where this enquiry has got to, from first contact through to signed
    # heads of terms. See ENQUIRY_STAGES.
    stage             = db.Column(db.String(40), default='Enquiry Received')
    stage_changed_on  = db.Column(db.Date)

    linked_property = db.relationship('Property', backref='enquiries')
    contact = db.relationship('Contact', backref='enquiries')
    organisation = db.relationship('Organisation', backref='enquiries')
    linked_project = db.relationship('Project', backref='enquiries')

    @property
    def traffic_light(self):
        today = date.today()
        if self.status != 'Open':
            return 'grey'
        ref = self.last_contact_date or self.received_date or self.created_at.date()
        days = (today - ref).days
        if self.next_follow_up and today > self.next_follow_up:
            return 'red'
        if days >= 7:
            return 'red'
        if days >= 3:
            return 'amber'
        return 'green'

    @property
    def traffic_emoji(self):
        return {'red': '🔴', 'amber': '🟡', 'green': '🟢', 'grey': '⚪'}.get(self.traffic_light, '⚪')


# The road from an enquiry landing to heads of terms being signed. Order is the
# pipeline order — "next stage" buttons walk down this list.
ENQUIRY_STAGES = [
    'Enquiry Received',
    'Qualified',
    'Viewing Arranged',
    'Viewing Completed',
    'Offer Received',
    'Terms Agreed',
    'Heads of Terms Issued',
    'Heads of Terms Signed',
]
# Off-pipeline outcomes — reachable at any point, and they close the enquiry.
ENQUIRY_STAGES_CLOSED = ['Lost', 'Withdrawn']
ENQUIRY_ALL_STAGES = ENQUIRY_STAGES + ENQUIRY_STAGES_CLOSED


def next_enquiry_stage(stage):
    """The stage after this one, or None at the end of the pipeline."""
    try:
        i = ENQUIRY_STAGES.index(stage or ENQUIRY_STAGES[0])
    except ValueError:
        return None                      # Lost / Withdrawn have no next step
    return ENQUIRY_STAGES[i + 1] if i + 1 < len(ENQUIRY_STAGES) else None


class EnquiryStageEvent(db.Model):
    """One step in an enquiry's progress, kept so the trail can be shown and
    the time between steps measured."""
    __tablename__ = 'enquiry_stage_events'
    id         = db.Column(db.Integer, primary_key=True)
    enquiry_id = db.Column(db.Integer, db.ForeignKey('enquiries.id'), nullable=False)
    stage      = db.Column(db.String(40), nullable=False)
    from_stage = db.Column(db.String(40))
    occurred_on = db.Column(db.Date, default=date.today)
    author     = db.Column(db.String(100))
    note       = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    enquiry = db.relationship('Enquiry', backref=db.backref(
        'stage_events', lazy=True, cascade='all, delete-orphan',
        order_by='EnquiryStageEvent.occurred_on'))


class EnquiryNote(db.Model):
    __tablename__ = 'enquiry_notes'
    id = db.Column(db.Integer, primary_key=True)
    enquiry_id = db.Column(db.Integer, db.ForeignKey('enquiries.id'), nullable=False)
    direction     = db.Column(db.String(20))  # inbound / outbound / note
    subject       = db.Column(db.String(255))
    body          = db.Column(db.Text, nullable=False)
    author        = db.Column(db.String(100))
    ms_message_id = db.Column(db.String(500), unique=True, nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    enquiry = db.relationship('Enquiry', backref=db.backref('notes_chain', lazy=True,
                              cascade='all, delete-orphan',
                              order_by='EnquiryNote.created_at'))


FOLDER_LABELS = {
    'instructions':          'Instructions / Basis of Appointment',
    'correspondence':        'Correspondence',
    'documentation':         'Documentation',
    'reports':               'Reports',
    'key_documents':         'Key Documents',
    'financial_calculations':'Financial & Calculations',
    'photographs_images':    'Photographs & Images',
}


class Project(db.Model):
    __tablename__ = 'projects'
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True)
    name = db.Column(db.String(255), nullable=False)
    project_ref = db.Column(db.String(50))
    status = db.Column(db.String(20), default='Active')   # Active, Complete, On Hold
    fee_earner = db.Column(db.String(100))
    client = db.Column(db.String(255))
    instruction_date = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Extra CRM/Reapit-style fields
    instruction_type  = db.Column(db.String(50))
    fee_percent       = db.Column(db.Float)
    fee_fixed         = db.Column(db.Float)
    available_from    = db.Column(db.Date)
    next_call         = db.Column(db.Date)
    client_phone      = db.Column(db.String(50))
    client_mobile     = db.Column(db.String(50))
    client_email      = db.Column(db.String(120))
    key_contact          = db.Column(db.String(100))
    landlord_name        = db.Column(db.String(255))
    agent_assigned       = db.Column(db.String(100))
    location_description = db.Column(db.Text)

    documents = db.relationship('ProjectDocument', backref='project', lazy=True, cascade='all, delete-orphan')

    def docs_in_folder(self, folder):
        return [d for d in self.documents if d.folder == folder]


class Listing(db.Model):
    """Website listing for a unit/floor/whole building — managed via a Project instruction."""
    __tablename__ = 'listings'
    id                 = db.Column(db.Integer, primary_key=True)
    project_id         = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    property_id        = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True)
    unit_name          = db.Column(db.String(100))    # "Unit 3", "Ground Floor", blank=whole building
    website_listed     = db.Column(db.Boolean, default=True)
    zoopla_listed      = db.Column(db.Boolean, default=False)   # publish to Zoopla feed (separate switch)
    listing_status     = db.Column(db.String(20), default='available')
    featured           = db.Column(db.Boolean, default=False)
    website_category   = db.Column(db.String(20))     # commercial / residential
    use_class          = db.Column(db.String(30))
    residential_use    = db.Column(db.String(30))     # Owner Occupied / HMO / Investment / Vacant
    area               = db.Column(db.String(100))
    listing_price      = db.Column(db.Float)
    listing_price_unit = db.Column(db.String(10))     # pa / pcm / sale / poa
    price_display      = db.Column(db.String(100))
    size               = db.Column(db.Float)
    measurement_type   = db.Column(db.String(10))     # NIA / GIA / GEA
    beds               = db.Column(db.Integer)
    baths              = db.Column(db.Integer)
    photo_id           = db.Column(db.String(100))
    blurb              = db.Column(db.Text)
    lat                = db.Column(db.Float)
    lng                = db.Column(db.Float)
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at           = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    website_published_at = db.Column(db.DateTime)
    zoopla_published_at  = db.Column(db.DateTime)

    # ── Commercial: Define the Space ──
    min_size           = db.Column(db.Float)
    max_size           = db.Column(db.Float)
    measurement_std    = db.Column(db.String(20))     # NIA / GIA / GEA / IPMS 2 / IPMS 3
    total_size         = db.Column(db.Float)
    self_contained     = db.Column(db.Boolean, default=False)
    add_on_factor      = db.Column(db.Float)          # %
    build_status       = db.Column(db.String(50))     # Ready / Spec / Shell / Fitted

    # ── Commercial: Lease Information ──
    set_as_to_let      = db.Column(db.Boolean, default=True)
    lease_type         = db.Column(db.String(50))     # New / Assignment / Sub-lease
    rent_qualifier     = db.Column(db.String(30))     # Quoting / Guideline / Asking
    rent_inclusive     = db.Column(db.String(20))     # Exclusive / Inclusive / N/A
    rent_from          = db.Column(db.Float)          # £ psf
    rent_to            = db.Column(db.Float)          # £ psf
    rent_comment       = db.Column(db.Text)
    rent_on_application= db.Column(db.Boolean, default=False)
    possession_now     = db.Column(db.Boolean, default=False)
    possession_quarter = db.Column(db.String(10))     # Q1 / Q2 / Q3 / Q4
    possession_year    = db.Column(db.Integer)
    possession_comment = db.Column(db.Text)
    lease_length_months= db.Column(db.Integer)
    lease_length_years = db.Column(db.Integer)
    lease_length_comment=db.Column(db.Text)
    inside_1954_act    = db.Column(db.Text)           # Inside / Outside / Contracted Out
    repair_insuring    = db.Column(db.Text)           # FRI / IRI / Internal (long labels)

    # ── Commercial: Sale Information ──
    set_as_for_sale    = db.Column(db.Boolean, default=False)
    sale_price         = db.Column(db.Float)
    sale_price_display = db.Column(db.String(100))

    # ── Rates & Charges ──
    service_charge     = db.Column(db.Float)          # £ psf
    service_charge_na  = db.Column(db.Boolean, default=False)
    service_charge_comment = db.Column(db.Text)
    rateable_value     = db.Column(db.Float)
    rateable_value_na  = db.Column(db.Boolean, default=False)
    rates_multiplier   = db.Column(db.Float)
    rates_payable      = db.Column(db.Float)
    epc_band           = db.Column(db.Text)           # A-G, or "Exempt" / "Not Required"
    epc_band_potential = db.Column(db.Text)
    vat_comment        = db.Column(db.Text)
    legal_fees         = db.Column(db.String(30))     # Each Party / Ingoing / N/A
    parking_ratio      = db.Column(db.String(50))
    parking_rent       = db.Column(db.Float)
    parking_rent_na    = db.Column(db.Boolean, default=False)
    parking_spaces     = db.Column(db.Integer)

    # ── Marketing ──
    summary_text       = db.Column(db.String(140))    # 140-char public summary
    key_points         = db.Column(db.Text)            # JSON list of bullet points
    amenities          = db.Column(db.Text)            # comma-separated tags
    availability_reason= db.Column(db.String(100))

    # ── Website listing criteria (shown on public listing) ──
    key_terms          = db.Column(db.Text)            # short key terms (all types)
    location_description = db.Column(db.Text)          # small location paragraph (all types)
    initial_yield      = db.Column(db.Float)           # % yield, OR cap rate £/sqft when Vacant Possession
    investment_vacant  = db.Column(db.String(20))      # Investment / Vacant Possession — sale
    tenure             = db.Column(db.String(30))      # Freehold / Long Leasehold — sale
    lease_years_remaining = db.Column(db.Integer)      # years left if Long Leasehold

    # ── Brochure & Floor Plan ──
    brochure_data      = db.Column(db.LargeBinary)
    brochure_filename  = db.Column(db.String(255))
    brochure_size      = db.Column(db.Integer)
    floor_plan_data    = db.Column(db.LargeBinary)
    floor_plan_filename= db.Column(db.String(255))
    floor_plan_size    = db.Column(db.Integer)
    epc_data           = db.Column(db.LargeBinary)
    epc_filename       = db.Column(db.String(255))
    epc_size           = db.Column(db.Integer)

    project  = db.relationship('Project',  backref=db.backref('project_listings', lazy=True, cascade='all, delete-orphan'))
    prop     = db.relationship('Property', backref=db.backref(
        'unit_listings', lazy=True, cascade='all, delete-orphan'))

    @property
    def display_title(self):
        addr = ''
        if self.project and self.project.property:
            addr = self.project.property.address
        elif self.prop:
            addr = self.prop.address
        if not addr:
            return self.unit_name or 'Listing'
        return _normalise_address(addr, self.unit_name)

    @property
    def display_price(self):
        if self.price_display: return self.price_display
        if not self.listing_price or self.listing_price_unit == 'poa': return 'Price on application'
        n = chr(163) + '{:,.0f}'.format(self.listing_price)
        if self.listing_price_unit == 'pa':  return n + ' per annum'
        if self.listing_price_unit == 'pcm': return n + ' pcm'
        return n


class ListingPhoto(db.Model):
    """Individual photo attached to a listing."""
    __tablename__ = 'listing_photos'
    id         = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=False)
    file_data  = db.Column(db.LargeBinary, nullable=False)
    filename   = db.Column(db.String(255))
    file_mime  = db.Column(db.String(100))
    file_size  = db.Column(db.Integer)
    caption    = db.Column(db.String(255))
    sort_order = db.Column(db.Integer, default=0)
    uploaded_at= db.Column(db.DateTime, default=datetime.utcnow)

    listing = db.relationship('Listing', backref=db.backref('photos', lazy=True,
                              cascade='all, delete-orphan', order_by='ListingPhoto.sort_order'))


class ProjectDocument(db.Model):
    __tablename__ = 'project_documents'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    folder = db.Column(db.String(50), nullable=False)
    document_name = db.Column(db.String(255), nullable=False)
    notes = db.Column(db.Text)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    file_data = db.Column(db.LargeBinary)
    file_mime = db.Column(db.String(100))
    file_size = db.Column(db.Integer)


class ProjectTask(db.Model):
    __tablename__ = 'project_tasks'
    id         = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    title      = db.Column(db.String(255), nullable=False)
    due_date   = db.Column(db.Date)
    completed  = db.Column(db.Boolean, default=False)
    created_by = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship('Project', backref=db.backref('tasks', lazy=True, cascade='all, delete-orphan', order_by='ProjectTask.due_date'))


class ProjectApplicant(db.Model):
    __tablename__ = 'project_applicants'
    id          = db.Column(db.Integer, primary_key=True)
    project_id  = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    contact_id  = db.Column(db.Integer, db.ForeignKey('contacts.id'), nullable=False)
    status      = db.Column(db.String(30), default='Active Applicant')
    match_score = db.Column(db.Integer)
    notes       = db.Column(db.Text)
    added_at    = db.Column(db.DateTime, default=datetime.utcnow)
    auto_linked = db.Column(db.Boolean, default=False)

    project = db.relationship('Project', backref=db.backref('applicants', lazy=True, cascade='all, delete-orphan'))
    # An applicant row only means something with its contact, and its contact_id
    # is NOT NULL — without delete-orphan, deleting a contact tried to set that
    # column to NULL and the delete failed with a 500.
    contact = db.relationship('Contact', backref=db.backref(
        'project_links', lazy=True, cascade='all, delete-orphan'))

    __table_args__ = (db.UniqueConstraint('project_id', 'contact_id', name='uq_proj_contact'),)


class ProjectService(db.Model):
    __tablename__ = 'project_services'
    id           = db.Column(db.Integer, primary_key=True)
    project_id   = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    service_type = db.Column(db.String(50), nullable=False)  # Sale, Letting, Rent Review, etc.
    status       = db.Column(db.String(20), default='Active')
    fee_earner   = db.Column(db.String(100))
    fee_percent  = db.Column(db.Float)   # % fee
    fee_fixed    = db.Column(db.Float)   # £ fixed fee
    notes        = db.Column(db.Text)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship('Project', backref=db.backref('services', lazy=True, cascade='all, delete-orphan'))


class ProjectNote(db.Model):
    __tablename__ = 'project_notes'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    author = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship('Project', backref=db.backref('project_notes', lazy=True, cascade='all, delete-orphan'))


class ProjectPhoto(db.Model):
    __tablename__ = 'project_photos'
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    caption = db.Column(db.String(255))
    url = db.Column(db.String(500))        # external URL or relative path
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship('Project', backref=db.backref('photos', lazy=True, cascade='all, delete-orphan'))


# ── Auth ───────────────────────────────────────────────────────────────────

# What each role may do. Checked on the server for every request — the browser
# is never trusted to decide.
ROLES = {
    'admin':  {'view', 'create', 'edit', 'delete', 'export', 'publish', 'admin'},
    'agent':  {'view', 'create', 'edit', 'export', 'publish'},
    'viewer': {'view'},
}
DEFAULT_ROLE = 'admin'          # the existing single account keeps full access
IDLE_TIMEOUT_MINUTES = int(os.environ.get('IDLE_TIMEOUT_MINUTES', '30'))


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role          = db.Column(db.String(20), default=DEFAULT_ROLE, nullable=False)
    totp_secret   = db.Column(db.String(64))       # set once MFA is enrolled
    mfa_enabled   = db.Column(db.Boolean, default=False)
    last_login_at = db.Column(db.DateTime)

    def can(self, action):
        return action in ROLES.get(self.role or DEFAULT_ROLE, set())

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


# ── Diary ────────────────────────────────────────────────────────────────────
# Timed appointments. The rest of the CRM only holds dates (a task's due date, a
# project's next call), so those still appear in the diary as all-day entries —
# this table is what makes an 11:00–11:30 viewing possible.
#
# Times are stored in UTC and shown in Europe/London, so the hour on screen
# stays right either side of the clocks changing.

EVENT_TYPES = {
    'viewing':     ('Viewing',     '#1a73e8'),
    'call':        ('Call',        '#12805c'),
    'meeting':     ('Meeting',     '#7b3fb5'),
    'inspection':  ('Inspection',  '#b8860b'),
    'reminder':    ('Reminder',    '#c2410c'),
    'appointment': ('Appointment', '#5b6675'),
}
LONDON = 'Europe/London'


class DiaryEvent(db.Model):
    __tablename__ = 'diary_events'
    id         = db.Column(db.Integer, primary_key=True)
    title      = db.Column(db.String(255), nullable=False)
    start_at   = db.Column(db.DateTime, nullable=False, index=True)   # UTC
    end_at     = db.Column(db.DateTime, nullable=False)               # UTC
    all_day    = db.Column(db.Boolean, default=False)
    event_type = db.Column(db.String(20), default='appointment')
    owner      = db.Column(db.String(100))
    # Kept in step with the linked property on save, so older records and any
    # external copy still read sensibly; the property link is what counts.
    location   = db.Column(db.String(255))
    notes      = db.Column(db.Text)

    # What the appointment is about — any of these may be set.
    contact_id     = db.Column(db.Integer, db.ForeignKey('contacts.id'), nullable=True)
    property_id    = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True)
    project_id     = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    enquiry_id     = db.Column(db.Integer, db.ForeignKey('enquiries.id'), nullable=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id'), nullable=True)

    # Kept for the Outlook sync that comes next; unused until then.
    ms_event_id = db.Column(db.String(255), unique=True, nullable=True)
    ms_etag     = db.Column(db.String(255))
    synced_at   = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(100))

    contact     = db.relationship('Contact', backref=db.backref('diary_events', lazy=True))
    linked_prop = db.relationship('Property', backref=db.backref('diary_events', lazy=True))
    project     = db.relationship('Project', backref=db.backref('diary_events', lazy=True))

    @property
    def type_label(self):
        return EVENT_TYPES.get(self.event_type or 'appointment', ('Appointment', '#5b6675'))[0]

    @property
    def colour(self):
        return EVENT_TYPES.get(self.event_type or 'appointment', ('Appointment', '#5b6675'))[1]

    @property
    def from_outlook(self):
        return bool(self.ms_event_id)

    @property
    def place(self):
        """Where the appointment is: the linked property's current address.

        Read through the relationship rather than from the stored text, so
        correcting an address on the property record corrects every appointment
        at it without touching them.
        """
        if self.linked_prop:
            return property_address(self.linked_prop)
        return self.location or ''


def property_address(prop):
    """A property's address and postcode as one line."""
    if prop is None:
        return ''
    return ', '.join(b for b in (prop.address, prop.postcode) if b)


def outlook_location(ev):
    """What the Outlook sync will send as the event location."""
    return ev.place


def london_tz():
    from zoneinfo import ZoneInfo
    return ZoneInfo(LONDON)


def to_london(dt):
    """A stored UTC time as London wall-clock time."""
    from datetime import timezone
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(london_tz())


def from_london(dt_naive):
    """A London wall-clock time as UTC, for storing."""
    from datetime import timezone
    if dt_naive is None:
        return None
    return dt_naive.replace(tzinfo=london_tz()).astimezone(timezone.utc).replace(tzinfo=None)


def _migrate_diary_tables():
    """diary_events is created by create_all; nothing existing is altered."""
    with app.app_context():
        db.create_all()


class AuditLog(db.Model):
    """Who did what, to which record, and when.

    Written on the server for every create, edit, delete, export, publish and
    confidential-file download, so the trail cannot be avoided by editing a
    request in the browser.
    """
    __tablename__ = 'audit_log'
    id          = db.Column(db.Integer, primary_key=True)
    at          = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    username    = db.Column(db.String(80))
    action      = db.Column(db.String(30))          # view / create / edit / delete / export / publish / login…
    entity      = db.Column(db.String(60))          # Contact, Project, Document…
    entity_id   = db.Column(db.String(40))
    detail      = db.Column(db.Text)
    ip          = db.Column(db.String(60))
    endpoint    = db.Column(db.String(120))


def audit(action, entity=None, entity_id=None, detail=None):
    """Record an action. Never records field values — only what was touched."""
    try:
        db.session.add(AuditLog(
            username=getattr(current_user, 'username', None) or 'anonymous',
            action=action, entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            detail=(detail or '')[:500] or None,
            ip=_login_key(), endpoint=request.endpoint,
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception('Could not write the audit log')


def requires(action):
    """Refuse the request unless the signed-in user's role allows this action."""
    from functools import wraps

    def wrapper(fn):
        @wraps(fn)
        def guarded(*a, **kw):
            if not current_user.is_authenticated or not current_user.can(action):
                audit('denied', entity=request.endpoint, detail=f'role={getattr(current_user, "role", None)}')
                abort(403, description='Your account does not have permission to do that.')
            return fn(*a, **kw)
        return guarded
    return wrapper


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Public endpoints: website API + login page itself
# listing_photo_image must be public so the website can display gallery photos
# (the <img> requests are unauthenticated, just like /api/listings).
_PUBLIC_ENDPOINTS = {'login', 'login_verify', 'logout', 'static', 'api_enquiry', 'api_listings', 'listing_photo_image',
                     'listing_brochure_download', 'listing_floorplan_download'}




# ── Address normalisation ─────────────────────────────────────────────────────
def _normalise_address(addr, unit=None):
    addr = ' '.join((addr or '').split()).strip()
    if unit:
        unit = unit.strip()
        if unit and not addr.lower().startswith(unit.lower()):
            addr = f"{unit}, {addr}"
    return addr


def _find_or_create_property(form):
    addr = _normalise_address((form.get('address') or '').strip())
    pc   = (form.get('postcode') or '').strip().upper()
    existing = Property.query.filter(
        db.func.lower(Property.address) == addr.lower(),
        db.func.upper(Property.postcode) == pc
    ).first()
    if existing:
        return existing
    p = Property(
        address=addr, postcode=pc,
        property_type=form.get('property_type') or None,
        size=float(form['size']) if form.get('size') else None,
        measurement_type=form.get('measurement_type') or None,
        residential_use=form.get('residential_use') or None,
    )
    db.session.add(p)
    db.session.flush()
    return p

@app.before_request
def require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return
    if not current_user.is_authenticated:
        return redirect(url_for('login', next=request.full_path if request.query_string else request.path))

    # Idle timeout: a session left open is closed by the server, not by the
    # browser, so it cannot be kept alive by editing anything client-side.
    # time.time() throughout: datetime.utcnow().timestamp() re-reads a naive UTC
    # value as local time, which on a machine that is not on UTC makes the gap
    # come out an hour wrong and the timeout never fire.
    import time as _time
    now = _time.time()
    last = session.get('_seen')
    if last and (now - last) > IDLE_TIMEOUT_MINUTES * 60:
        audit('session-expired', entity='User', entity_id=getattr(current_user, 'id', None))
        logout_user()
        session.clear()
        flash('You were signed out after a period of inactivity.', 'info')
        return redirect(url_for('login'))
    session['_seen'] = now
    session.permanent = True
    # The starting password is published in this repository, so nothing else in
    # the database opens until it has been replaced.
    if request.endpoint not in ('change_password', 'logout') and \
            current_user.check_password(DEFAULT_PASSWORD):
        return redirect(url_for('change_password'))
    # When the deployment requires it, nothing opens until two-step sign-in is on.
    if MFA_REQUIRED and not current_user.mfa_enabled and \
            request.endpoint not in ('account_mfa', 'change_password', 'logout'):
        return redirect(url_for('account_mfa'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        wait = _login_blocked()
        if wait:
            flash(f'Too many failed attempts. Try again in {wait} minute(s).', 'danger')
            return render_template('login.html'), 429
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if user.mfa_enabled and user.totp_secret:
                # Password alone is not enough: park the id and ask for a code.
                session['_mfa_user'] = user.id
                return redirect(url_for('login_verify'))
            _login_succeeded()
            login_user(user, remember=True)
            user.last_login_at = datetime.utcnow()
            db.session.commit()
            audit('login', entity='User', entity_id=user.id)
            next_page = request.args.get('next', '')
            # Only ever bounce to a path on this site — never an absolute or
            # protocol-relative URL supplied by whoever sent the link.
            if next_page.startswith('/') and not next_page.startswith('//'):
                return redirect(next_page)
            return redirect(url_for('dashboard'))
        _login_failed()
        audit('login-failed', entity='User', detail=username[:60])
        flash('Incorrect username or password.', 'danger')
    return render_template('login.html')


# ── Multi-factor authentication ──────────────────────────────────────────────
# Time-based one-time codes (the standard used by Google Authenticator, Authy,
# 1Password and so on), implemented on the standard library so no extra
# dependency is introduced. The secret is stored per user; codes are checked on
# the server and a used code cannot be replayed within its window.

MFA_REQUIRED = os.environ.get('MFA_REQUIRED', '').lower() in ('1', 'true', 'yes')


def _b32_secret():
    import base64, secrets as _s
    return base64.b32encode(_s.token_bytes(20)).decode().rstrip('=')


def totp_code(secret, when=None, step=30):
    """The expected code for a secret at a point in time."""
    import base64, hmac, hashlib, struct, time
    key = base64.b32decode(secret + '=' * (-len(secret) % 8), casefold=True)
    counter = int((when or time.time()) // step)
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f'{value % 1000000:06d}'


def totp_valid(secret, code, drift=1):
    """True if the code matches, allowing for a little clock drift."""
    import hmac as _h, time
    code = (code or '').strip().replace(' ', '')
    if not secret or not code.isdigit():
        return False
    now = time.time()
    return any(_h.compare_digest(totp_code(secret, now + offset * 30), code)
               for offset in range(-drift, drift + 1))


def totp_uri(user, secret):
    """The otpauth:// string an authenticator app scans or accepts as text."""
    from urllib.parse import quote
    return (f'otpauth://totp/Cowan%20%26%20Rutter:{quote(user.username)}'
            f'?secret={secret}&issuer=Cowan%20%26%20Rutter&digits=6&period=30')


@app.route('/account/mfa', methods=['GET', 'POST'])
def account_mfa():
    """Turn on two-step sign-in for the signed-in account."""
    user = current_user
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'disable' and user.mfa_enabled:
            if not user.check_password(request.form.get('password', '')):
                flash('Password not correct — two-step sign-in is unchanged.', 'danger')
            else:
                user.mfa_enabled = False
                user.totp_secret = None
                db.session.commit()
                audit('mfa-disabled', entity='User', entity_id=user.id)
                flash('Two-step sign-in turned off.', 'info')
            return redirect(url_for('account_mfa'))

        secret = session.get('_mfa_pending')
        if secret and totp_valid(secret, request.form.get('code')):
            user.totp_secret = secret
            user.mfa_enabled = True
            session.pop('_mfa_pending', None)
            db.session.commit()
            audit('mfa-enabled', entity='User', entity_id=user.id)
            flash('Two-step sign-in is on. You will be asked for a code at each sign-in.', 'success')
            return redirect(url_for('dashboard'))
        flash('That code was not right. Check the app and try again.', 'danger')

    secret = None
    if not user.mfa_enabled:
        secret = session.get('_mfa_pending') or _b32_secret()
        session['_mfa_pending'] = secret
    return render_template('account_mfa.html', secret=secret,
                           uri=totp_uri(user, secret) if secret else None,
                           mfa_required=MFA_REQUIRED)


@app.route('/login/verify', methods=['GET', 'POST'])
def login_verify():
    """Second step of signing in: the six-digit code."""
    pending = session.get('_mfa_user')
    if not pending:
        return redirect(url_for('login'))
    user = db.session.get(User, pending)
    if not user:
        session.pop('_mfa_user', None)
        return redirect(url_for('login'))
    if request.method == 'POST':
        if _login_blocked():
            flash('Too many attempts. Try again shortly.', 'danger')
            return render_template('login_verify.html'), 429
        if totp_valid(user.totp_secret, request.form.get('code')):
            session.pop('_mfa_user', None)
            _login_succeeded()
            login_user(user, remember=True)
            user.last_login_at = datetime.utcnow()
            db.session.commit()
            audit('login', entity='User', entity_id=user.id, detail='with two-step code')
            return redirect(url_for('dashboard'))
        _login_failed()
        audit('mfa-failed', entity='User', entity_id=user.id)
        flash('That code was not right.', 'danger')
    return render_template('login_verify.html')


@app.route('/account/password', methods=['GET', 'POST'])
def change_password():
    """Set a new password for the signed-in user."""
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        if not current_user.check_password(current):
            flash('Your current password is not correct.', 'danger')
        elif len(new) < 12:
            flash('Please choose a password of at least 12 characters.', 'danger')
        elif new != confirm:
            flash('The two new passwords do not match.', 'danger')
        elif new == DEFAULT_PASSWORD:
            flash('That is the published default password. Please choose another.', 'danger')
        else:
            current_user.password_hash = generate_password_hash(new)
            db.session.commit()
            flash('Password changed. Use the new password from now on.', 'success')
            return redirect(url_for('dashboard'))
    return render_template('change_password.html',
                           using_default=current_user.check_password(DEFAULT_PASSWORD))


@app.route('/logout', methods=['POST'])
def logout():
    logout_user()
    return redirect(url_for('login'))


# ── Properties ─────────────────────────────────────────────────────────────

def _available_listings(instruction_type):
    """Live listings for an instruction type that are still available.

    Drives the To Let / For Sale panels on the organiser. Reads the existing
    fields — no new columns — so what is shown always matches the project and
    listing records themselves.
    """
    return (Listing.query
            .join(Project, Listing.project_id == Project.id)
            .filter(Project.instruction_type == instruction_type,
                    Project.status == 'Active',
                    db.func.lower(db.func.coalesce(Listing.listing_status, 'available')) == 'available')
            .order_by(Listing.id.desc())
            .all())


def _diary_items(limit_days=60):
    """Everything with a date attached, from the records that already hold them."""
    today = date.today()
    items = []
    for t in ProjectTask.query.filter(ProjectTask.due_date.isnot(None),
                                      ProjectTask.completed.is_(False)).all():
        items.append({'on': t.due_date, 'kind': 'Task', 'what': t.title,
                      'who': t.created_by, 'url': url_for('project_detail', id=t.project_id)})
    for p in Project.query.filter(Project.next_call.isnot(None)).all():
        items.append({'on': p.next_call, 'kind': 'Call', 'what': f'Call on {p.name}',
                      'who': p.fee_earner, 'url': url_for('project_detail', id=p.id)})
    for c in Contact.query.filter(Contact.next_follow_up.isnot(None)).all():
        items.append({'on': c.next_follow_up, 'kind': 'Follow-up', 'what': c.full_name,
                      'who': c.assigned_agent, 'url': url_for('contact_detail', id=c.id)})
    for e in Enquiry.query.filter(Enquiry.next_follow_up.isnot(None),
                                  Enquiry.status == 'Open').all():
        items.append({'on': e.next_follow_up, 'kind': 'Enquiry', 'what': e.subject,
                      'who': e.fee_earner, 'url': url_for('enquiry_detail', id=e.id)})
    items.sort(key=lambda i: i['on'])
    return items


# ── Diary: views and editing ─────────────────────────────────────────────────

def _range_for(view, anchor):
    """The window a view covers, as London dates."""
    from datetime import timedelta as _td
    if view == 'day':
        return anchor, anchor
    if view == 'month':
        import calendar as _cal
        first = anchor.replace(day=1)
        start = first - _td(days=first.weekday())               # grid starts Monday
        last = anchor.replace(day=_cal.monthrange(anchor.year, anchor.month)[1])
        end = last + _td(days=(6 - last.weekday()))
        return start, end
    start = anchor - _td(days=anchor.weekday())                 # week: Mon–Sun
    return start, start + _td(days=6)


def _events_between(start_date, end_date, types=None, owner=None):
    """Timed appointments in the window, plus the CRM's own date-only items."""
    from datetime import datetime as _dt, time as _time
    lo = from_london(_dt.combine(start_date, _time.min))
    hi = from_london(_dt.combine(end_date, _time.max))
    q = DiaryEvent.query.filter(DiaryEvent.start_at <= hi, DiaryEvent.end_at >= lo)
    if types:
        q = q.filter(DiaryEvent.event_type.in_(types))
    if owner:
        q = q.filter(DiaryEvent.owner == owner)
    events = []
    for e in q.order_by(DiaryEvent.start_at).all():
        s, t = to_london(e.start_at), to_london(e.end_at)
        events.append({
            'id': e.id, 'title': e.title, 'type': e.event_type or 'appointment',
            'type_label': e.type_label, 'colour': e.colour, 'owner': e.owner,
            'location': e.place, 'all_day': bool(e.all_day),
            'property_id': e.property_id,
            'date': s.date().isoformat(), 'start': s.strftime('%H:%M'), 'end': t.strftime('%H:%M'),
            'start_min': s.hour * 60 + s.minute, 'end_min': t.hour * 60 + t.minute,
            'outlook': e.from_outlook, 'url': url_for('diary_event', id=e.id),
        })
    # The date-only reminders the CRM already held, shown across the top.
    for i in _diary_items():
        if start_date <= i['on'] <= end_date:
            events.append({
                'id': None, 'title': i['what'], 'type': 'reminder', 'type_label': i['kind'],
                'colour': EVENT_TYPES['reminder'][1], 'owner': i['who'], 'location': None,
                'all_day': True, 'date': i['on'].isoformat(), 'start': '', 'end': '',
                'start_min': 0, 'end_min': 0, 'outlook': False, 'url': i['url'],
            })
    return events


@app.route('/diary')
def diary():
    from datetime import datetime as _dt
    view = request.args.get('view', 'week')
    if view not in ('day', 'week', 'month'):
        view = 'week'
    try:
        anchor = _dt.strptime(request.args.get('date', ''), '%Y-%m-%d').date()
    except ValueError:
        anchor = to_london(datetime.utcnow()).date()

    types = [t for t in request.args.getlist('type') if t in EVENT_TYPES]
    owner = request.args.get('owner') or None
    start, end = _range_for(view, anchor)
    events = _events_between(start, end, types or None, owner)

    now_london = to_london(datetime.utcnow())
    owners = sorted({o[0] for o in db.session.query(DiaryEvent.owner).distinct() if o[0]})
    properties = Property.query.order_by(Property.address).all()
    return render_template('diary.html', properties=properties,
                           view=view, anchor=anchor, start=start, end=end,
                           events=events, event_types=EVENT_TYPES,
                           selected_types=types, owner=owner, owners=owners,
                           today=now_london.date(),
                           now_minutes=now_london.hour * 60 + now_london.minute,
                           outlook_connected=False)


@app.route('/diary/event/new', methods=['POST'])
@requires('create')
def diary_event_new():
    from datetime import datetime as _dt
    try:
        start = _dt.strptime(request.form['start'], '%Y-%m-%dT%H:%M')
        end = _dt.strptime(request.form['end'], '%Y-%m-%dT%H:%M')
    except (KeyError, ValueError):
        flash('That appointment needs a start and end time.', 'warning')
        return _back_to('diary')
    if end <= start:
        flash('The end time must be after the start time.', 'warning')
        return _back_to('diary')
    # Every appointment belongs to a property. Checked here, not only in the
    # form, so it cannot be skipped by editing the request.
    prop = Property.query.get(_fint(request.form.get('property_id')) or 0)
    if prop is None:
        flash('Choose the property this appointment is at.', 'warning')
        return _back_to('diary')

    ev = DiaryEvent(
        title=(request.form.get('title') or 'Appointment').strip(),
        start_at=from_london(start), end_at=from_london(end),
        event_type=request.form.get('event_type') if request.form.get('event_type') in EVENT_TYPES else 'appointment',
        owner=(request.form.get('owner') or getattr(current_user, 'username', None)),
        notes=_ftext(request.form.get('notes')),
        contact_id=_fint(request.form.get('contact_id')),
        property_id=prop.id,
        project_id=_fint(request.form.get('project_id')),
        enquiry_id=_fint(request.form.get('enquiry_id')),
        created_by=getattr(current_user, 'username', None),
    )
    ev.location = property_address(prop)   # a readable copy for older views and Outlook
    db.session.add(ev)
    db.session.commit()
    audit('create', entity='DiaryEvent', entity_id=ev.id, detail=ev.event_type)
    flash('Appointment added.', 'success')
    return _back_to('diary')


@app.route('/diary/event/<int:id>', methods=['GET', 'POST'])
def diary_event(id):
    from datetime import datetime as _dt
    ev = DiaryEvent.query.get_or_404(id)
    if request.method == 'POST':
        if not current_user.can('edit'):
            abort(403)
        if 'title' in request.form:
            ev.title = request.form.get('title') or ev.title
        for field in ('notes', 'owner'):
            if field in request.form:
                setattr(ev, field, _ftext(request.form.get(field)))
        # The property is required on save, including for appointments made
        # before this rule existed.
        if 'property_id' in request.form:
            prop = Property.query.get(_fint(request.form.get('property_id')) or 0)
            if prop is None:
                flash('Choose the property this appointment is at.', 'warning')
                return redirect(url_for('diary_event', id=ev.id))
            ev.property_id = prop.id
        elif ev.property_id is None:
            flash('Choose the property this appointment is at.', 'warning')
            return redirect(url_for('diary_event', id=ev.id))
        if request.form.get('event_type') in EVENT_TYPES:
            ev.event_type = request.form['event_type']
        for field in ('contact_id', 'project_id', 'enquiry_id'):
            if field in request.form:
                setattr(ev, field, _fint(request.form.get(field)))
        try:
            if request.form.get('start'):
                ev.start_at = from_london(_dt.strptime(request.form['start'], '%Y-%m-%dT%H:%M'))
            if request.form.get('end'):
                ev.end_at = from_london(_dt.strptime(request.form['end'], '%Y-%m-%dT%H:%M'))
        except ValueError:
            flash('That start or end time was not understood.', 'warning')
            return redirect(url_for('diary_event', id=ev.id))
        if ev.end_at <= ev.start_at:
            flash('The end time must be after the start time.', 'warning')
            return redirect(url_for('diary_event', id=ev.id))
        ev.location = property_address(Property.query.get(ev.property_id) if ev.property_id else None)
        db.session.commit()
        audit('edit', entity='DiaryEvent', entity_id=ev.id)
        flash('Appointment updated.', 'success')
        return _back_to('diary')
    return render_template('diary_event.html', ev=ev, event_types=EVENT_TYPES,
                           start_local=to_london(ev.start_at), end_local=to_london(ev.end_at),
                           contacts=Contact.query.order_by(Contact.last_name).all(),
                           properties=Property.query.order_by(Property.address).all(),
                           projects=Project.query.order_by(Project.name).all())


@app.route('/diary/event/<int:id>/move', methods=['POST'])
@requires('edit')
def diary_event_move(id):
    """Dragged to a new time, or resized. Sent by the calendar as JSON."""
    from datetime import datetime as _dt
    ev = DiaryEvent.query.get_or_404(id)
    data = request.get_json(silent=True) or request.form
    try:
        start = _dt.strptime(data['start'], '%Y-%m-%dT%H:%M')
        end = _dt.strptime(data['end'], '%Y-%m-%dT%H:%M')
    except (KeyError, TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'A start and end time are needed.'}), 400
    if end <= start:
        return jsonify({'ok': False, 'error': 'The end must be after the start.'}), 400
    ev.start_at, ev.end_at = from_london(start), from_london(end)
    db.session.commit()
    audit('edit', entity='DiaryEvent', entity_id=ev.id, detail='moved or resized')
    return jsonify({'ok': True, 'start': data['start'], 'end': data['end']})


@app.route('/diary/event/<int:id>/delete', methods=['POST'])
@requires('delete')
def diary_event_delete(id):
    ev = DiaryEvent.query.get_or_404(id)
    return delete_record(ev, 'Appointment', 'diary')


@app.route('/')
def dashboard():
    prop_count = Property.query.count()
    trans_count = Transaction.query.count()
    proj_count = Project.query.count()
    # Active listings = properties that have at least one active project
    active_listings = (Property.query
                       .join(Project, Property.id == Project.property_id)
                       .filter(Project.status == 'Active')
                       .order_by(Property.created_at.desc())
                       .limit(20).all())
    # Fall back to all properties if none have active projects
    if not active_listings:
        active_listings = Property.query.order_by(Property.created_at.desc()).limit(20).all()

    contacts = Contact.query.order_by(Contact.created_at.desc()).limit(20).all()
    today = date.today()

    # All open enquiries sorted by priority (overdue first, then by follow-up date)
    open_enquiries = Enquiry.query.filter(Enquiry.status == 'Open').all()
    open_enquiries.sort(key=lambda e: (
        0 if (e.next_follow_up and e.next_follow_up <= today) else
        1 if (e.last_contact_date and (today - e.last_contact_date).days >= 7) else
        1 if (not e.last_contact_date and e.received_date and (today - e.received_date).days >= 7) else
        2,
        e.next_follow_up or today
    ))

    due_today = []  # follow-up banner removed from dashboard

    # New enquiries that have NOT been contacted yet (no logged contact date).
    new_enquiries = [e for e in open_enquiries if not e.last_contact_date]
    # Applicants (contacts) with an overdue follow-up — flag for chasing.
    overdue_contacts = (Contact.query
                        .filter(Contact.next_follow_up.isnot(None),
                                Contact.next_follow_up < today,
                                Contact.status.notin_(ARCHIVED_STATUSES + ['Inactive']))
                        .order_by(Contact.next_follow_up).all())

    to_let = _available_listings('Letting')
    for_sale = _available_listings('Sale')
    # Landlords and clients with a call due — the third column of the organiser.
    landlords_to_call = (Contact.query
                         .filter(Contact.contact_type.in_(['Landlord', 'Client']),
                                 Contact.status.notin_(ARCHIVED_STATUSES))
                         .order_by(Contact.next_follow_up.is_(None), Contact.next_follow_up)
                         .limit(25).all())
    diary_items = _diary_items()[:12]

    recent_transactions = Transaction.query.order_by(Transaction.created_at.desc()).limit(10).all()
    enq_count = Enquiry.query.filter(Enquiry.status == 'Open').count()
    contact_count = Contact.query.count()

    return render_template('dashboard.html',
                           to_let=to_let, for_sale=for_sale,
                           landlords_to_call=landlords_to_call, diary_items=diary_items,
                           prop_count=prop_count,
                           trans_count=trans_count,
                           proj_count=proj_count,
                           contact_count=contact_count,
                           enq_count=enq_count,
                           active_listings=active_listings,
                           contacts=contacts,
                           open_enquiries=open_enquiries,
                           new_enquiries=new_enquiries,
                           overdue_contacts=overdue_contacts,
                           due_today=due_today,
                           recent_transactions=recent_transactions,
                           today=today)


@app.route('/properties')
def properties_list():
    q = request.args.get('q', '')
    query = Property.query
    if q:
        query = query.filter(
            db.or_(Property.address.ilike(f'%{q}%'), Property.postcode.ilike(f'%{q}%'))
        )
    properties = query.order_by(Property.address).all()
    return render_template('properties/list.html', properties=properties, q=q)


@app.route('/properties/new', methods=['GET', 'POST'])
def property_new():
    if request.method == 'POST':
        size_raw = request.form.get('size', '').strip()
        prop = Property(
            address=request.form['address'],
            postcode=request.form['postcode'].upper(),
            property_type=request.form.get('property_type'),
            size=float(size_raw) if size_raw else None,
            measurement_type=request.form.get('measurement_type'),
            description=request.form.get('description'),
            residential_use=request.form.get('residential_use') or None,
        )
        db.session.add(prop)
        db.session.commit()
        flash('Property added successfully.', 'success')
        return redirect(url_for('property_detail', id=prop.id))
    return render_template('properties/form.html', prop=None)


@app.route('/properties/<int:id>')
def property_detail(id):
    prop = Property.query.get_or_404(id)
    folder_labels = FOLDER_LABELS
    return render_template('properties/detail.html', prop=prop, folder_labels=folder_labels)


@app.route('/properties/<int:id>/edit', methods=['GET', 'POST'])
def property_edit(id):
    prop = Property.query.get_or_404(id)
    if request.method == 'POST':
        # Presence-guarded so the property record page, which shows part of the
        # record, cannot blank what it does not display.
        # Website listing details (category/price/photos/brochure) are managed per
        # instruction on the project's Website Listing tab — not on the Property.
        apply_form_fields(prop, request.form, PROPERTY_FIELDS)
        if 'postcode' in request.form:
            prop.postcode = (request.form.get('postcode') or '').upper()
        db.session.commit()
        flash('Property updated.', 'success')
        return _back_to('property_detail', id=prop.id)
    return render_template('properties/form.html', prop=prop)


@app.route('/properties/<int:id>/delete', methods=['POST'])
@requires('delete')
@requires('delete')
def property_delete(id):
    prop = Property.query.get_or_404(id)
    # Unlink enquiries pointing at this property (keep the enquiry history).
    for enq in prop.enquiries:
        enq.property_id = None
    # Deleting the property cascade-deletes its projects, so unlink any
    # enquiries pointing at those projects too, or the cascade is blocked.
    for project in prop.projects:
        for enq in project.enquiries:
            enq.project_id = None
    # Listings tied to the property by property_id go with it via the
    # relationship's cascade.
    return delete_record(prop, 'Property', 'properties_list')


@app.route('/properties/<int:id>/brochure/upload', methods=['POST'])
def brochure_upload(id):
    prop = Property.query.get_or_404(id)
    f = request.files.get('brochure')
    if f and f.filename:
        prop.brochure_data     = f.read()
        prop.brochure_filename = f.filename
        prop.brochure_size     = len(prop.brochure_data)
        db.session.commit()
        flash('Brochure uploaded.', 'success')
    return redirect(url_for('property_edit', id=id))


@app.route('/properties/<int:id>/brochure/download')
def brochure_download(id):
    from flask import send_file
    import io
    prop = Property.query.get_or_404(id)
    if not prop.brochure_data:
        flash('No brochure attached.', 'warning')
        return redirect(url_for('property_detail', id=id))
    return send_file(io.BytesIO(prop.brochure_data),
                     mimetype='application/pdf',
                     as_attachment=True,
                     download_name=prop.brochure_filename or 'brochure.pdf')


@app.route('/properties/<int:id>/brochure/delete', methods=['POST'])
def brochure_delete(id):
    prop = Property.query.get_or_404(id)
    prop.brochure_data = prop.brochure_filename = prop.brochure_size = None
    db.session.commit()
    flash('Brochure removed.', 'info')
    return redirect(url_for('property_edit', id=id))


@app.route('/properties/<int:id>/floorplan/upload', methods=['POST'])
def floorplan_upload(id):
    prop = Property.query.get_or_404(id)
    f = request.files.get('floor_plan')
    if f and f.filename:
        prop.floor_plan_data     = f.read()
        prop.floor_plan_filename = f.filename
        prop.floor_plan_size     = len(prop.floor_plan_data)
        db.session.commit()
        flash('Floor plan uploaded.', 'success')
    return redirect(url_for('property_edit', id=id))


@app.route('/properties/<int:id>/floorplan/download')
def floorplan_download(id):
    from flask import send_file
    import io
    prop = Property.query.get_or_404(id)
    if not prop.floor_plan_data:
        flash('No floor plan attached.', 'warning')
        return redirect(url_for('property_detail', id=id))
    return send_file(io.BytesIO(prop.floor_plan_data),
                     mimetype='application/pdf',
                     as_attachment=True,
                     download_name=prop.floor_plan_filename or 'floorplan.pdf')


@app.route('/properties/<int:id>/floorplan/delete', methods=['POST'])
def floorplan_delete(id):
    prop = Property.query.get_or_404(id)
    prop.floor_plan_data = prop.floor_plan_filename = prop.floor_plan_size = None
    db.session.commit()
    flash('Floor plan removed.', 'info')
    return redirect(url_for('property_edit', id=id))


# ── Transactions ────────────────────────────────────────────────────────────

@app.route('/transactions')
def transactions_list():
    q = request.args.get('q', '')
    ttype = request.args.get('type', '')
    query = Transaction.query.join(Property)
    if q:
        query = query.filter(
            db.or_(Property.address.ilike(f'%{q}%'), Property.postcode.ilike(f'%{q}%'))
        )
    if ttype:
        query = query.filter(Transaction.transaction_type == ttype)
    transactions = query.order_by(Transaction.transaction_date.desc()).all()
    return render_template('transactions/list.html', transactions=transactions, q=q, ttype=ttype)


@app.route('/transactions/new', methods=['GET', 'POST'])
def transaction_new():
    properties = Property.query.order_by(Property.address).all()
    if request.method == 'POST':
        def parse_date(val):
            return datetime.strptime(val, '%Y-%m-%d').date() if val else None
        def parse_float(val):
            return float(val.replace(',', '')) if val and val.strip() else None

        t = Transaction(
            property_id=request.form['property_id'],
            transaction_type=request.form['transaction_type'],
            tenure_type=request.form.get('tenure_type'),
            transaction_date=parse_date(request.form.get('transaction_date')),
            value=parse_float(request.form.get('value')),
            vendor=request.form.get('vendor'),
            purchaser=request.form.get('purchaser'),
            landlord=request.form.get('landlord'),
            tenant=request.form.get('tenant'),
            lease_start=parse_date(request.form.get('lease_start')),
            lease_end=parse_date(request.form.get('lease_end')),
            rent_pa=parse_float(request.form.get('rent_pa')),
            break_clause=request.form.get('break_clause'),
            notes=request.form.get('notes'),
            description=request.form.get('description') or None,
            niy=parse_float(request.form.get('niy')),
            giy=parse_float(request.form.get('giy')),
            capital_rate_psf=parse_float(request.form.get('capital_rate_psf')),
            wault=parse_float(request.form.get('wault')),
            passing_income=parse_float(request.form.get('passing_income')),
            income_pct=parse_float(request.form.get('income_pct')),
            erv=parse_float(request.form.get('erv')),
            tenant_covenant=request.form.get('tenant_covenant') or None,
            written_analysis=request.form.get('written_analysis') or None,
            done_by=request.form.get('done_by') or 'CR',
            third_party_name=request.form.get('third_party_name') or None,
            part_or_floor=request.form.get('part_or_floor') or None,
            source=request.form.get('source') or None,
            source_contact=request.form.get('source_contact') or None,
            nda=bool(request.form.get('nda')),
            size_units=request.form.get('size_units') or None,
            size_basis=request.form.get('size_basis') or None,
            demise_description=request.form.get('demise_description') or None,
            incentive_years=parse_float(request.form.get('incentive_years')),
            headline_rate=parse_float(request.form.get('headline_rate')),
            headline_rate_unit=request.form.get('headline_rate_unit') or 'pa',
            net_rate=parse_float(request.form.get('net_rate')),
            next_break_date=parse_date(request.form.get('next_break_date')),
            no_break=bool(request.form.get('no_break')),
            next_review_date=parse_date(request.form.get('next_review_date')),
            no_review=bool(request.form.get('no_review')),
            review_type=request.form.get('review_type') or None,
            repair=request.form.get('repair') or None,
            alienation=request.form.get('alienation') or None,
            primary_use_class=request.form.get('primary_use_class') or None,
            lt_act=request.form.get('lt_act') or None,
            epc_rating=request.form.get('epc_rating') or None,
            fitted=request.form.get('fitted') or None,
        )
        db.session.add(t)
        db.session.commit()
        flash('Transaction recorded. It now appears as a tenure on the property.', 'success')
        return redirect(url_for('property_detail', id=t.property_id))
    prop_id = request.args.get('property_id')
    return render_template('transactions/form.html', properties=properties, prop_id=prop_id, trans=None)


@app.route('/transactions/<int:id>/edit', methods=['GET', 'POST'])
def transaction_edit(id):
    t = Transaction.query.get_or_404(id)
    properties = Property.query.order_by(Property.address).all()
    if request.method == 'POST':
        def parse_date(val):
            return datetime.strptime(val, '%Y-%m-%d').date() if val else None
        def parse_float(val):
            return float(val.replace(',', '')) if val and val.strip() else None

        t.property_id = request.form['property_id']
        t.transaction_type = request.form['transaction_type']
        t.tenure_type = request.form.get('tenure_type')
        t.transaction_date = parse_date(request.form.get('transaction_date'))
        t.value = parse_float(request.form.get('value'))
        t.vendor = request.form.get('vendor')
        t.purchaser = request.form.get('purchaser')
        t.landlord = request.form.get('landlord')
        t.tenant = request.form.get('tenant')
        t.lease_start = parse_date(request.form.get('lease_start'))
        t.lease_end = parse_date(request.form.get('lease_end'))
        t.rent_pa = parse_float(request.form.get('rent_pa'))
        t.break_clause        = request.form.get('break_clause')
        t.notes               = request.form.get('notes')
        t.description         = request.form.get('description') or None
        t.niy                 = parse_float(request.form.get('niy'))
        t.giy                 = parse_float(request.form.get('giy'))
        t.capital_rate_psf    = parse_float(request.form.get('capital_rate_psf'))
        t.wault               = parse_float(request.form.get('wault'))
        t.passing_income      = parse_float(request.form.get('passing_income'))
        t.income_pct          = parse_float(request.form.get('income_pct'))
        t.erv                 = parse_float(request.form.get('erv'))
        t.tenant_covenant     = request.form.get('tenant_covenant') or None
        t.written_analysis    = request.form.get('written_analysis') or None
        t.done_by             = request.form.get('done_by') or 'CR'
        t.third_party_name    = request.form.get('third_party_name') or None
        t.part_or_floor       = request.form.get('part_or_floor') or None
        t.source              = request.form.get('source') or None
        t.source_contact      = request.form.get('source_contact') or None
        t.nda                 = bool(request.form.get('nda'))
        t.size_units          = request.form.get('size_units') or None
        t.size_basis          = request.form.get('size_basis') or None
        t.demise_description  = request.form.get('demise_description') or None
        t.incentive_years     = parse_float(request.form.get('incentive_years'))
        t.headline_rate       = parse_float(request.form.get('headline_rate'))
        t.headline_rate_unit  = request.form.get('headline_rate_unit') or 'pa'
        t.net_rate            = parse_float(request.form.get('net_rate'))
        t.next_break_date     = parse_date(request.form.get('next_break_date'))
        t.no_break            = bool(request.form.get('no_break'))
        t.next_review_date    = parse_date(request.form.get('next_review_date'))
        t.no_review           = bool(request.form.get('no_review'))
        t.review_type         = request.form.get('review_type') or None
        t.repair              = request.form.get('repair') or None
        t.alienation          = request.form.get('alienation') or None
        t.primary_use_class   = request.form.get('primary_use_class') or None
        t.lt_act              = request.form.get('lt_act') or None
        t.epc_rating          = request.form.get('epc_rating') or None
        t.fitted              = request.form.get('fitted') or None
        db.session.commit()
        flash('Transaction updated.', 'success')
        return redirect(url_for('property_detail', id=t.property_id))
    return render_template('transactions/form.html', properties=properties, prop_id=t.property_id, trans=t)


@app.route('/transactions/<int:id>/delete', methods=['POST'])
@requires('delete')
def transaction_delete(id):
    t = Transaction.query.get_or_404(id)
    prop_id = t.property_id
    db.session.delete(t)
    db.session.commit()
    flash('Transaction deleted.', 'info')
    return redirect(url_for('property_detail', id=prop_id))


# ── Projects ────────────────────────────────────────────────────────────────

def format_rent(price, unit=None):
    """An asking price written the same way everywhere: £18,500 per annum."""
    if not price:
        return 'Not provided'
    words = {'pa': ' per annum', 'pcm': ' per calendar month', 'sale': '', 'poa': ''}
    if unit == 'poa':
        return 'Price on application'
    return f"£{price:,.0f}{words.get(unit, ' per annum')}"


def format_size(size):
    """A floor area written the same way everywhere: 430 sq. ft."""
    if not size:
        return 'Not provided'
    return f'{size:,.0f} sq. ft.'


app.jinja_env.globals['format_rent'] = format_rent
app.jinja_env.globals['format_size'] = format_size


def project_row_summary(project):
    """What the Projects sidebar shows for one project.

    The asking price, size and photograph come from the project's website
    listing where there is one, and from the property itself otherwise.
    """
    listing = project.project_listings[0] if project.project_listings else None
    prop = project.property

    price = unit = size = None
    if listing:
        price, unit, size = listing.listing_price, listing.listing_price_unit, listing.size
    if not price and prop:
        price, unit = prop.listing_price, prop.listing_price_unit
    if not size and prop:
        size = prop.size

    photo = listing.photos[0] if (listing and listing.photos) else None
    address = ', '.join(b for b in ((prop.address if prop else None),
                                    (prop.postcode if prop else None)) if b)

    return {
        'project': project,
        'photo': photo,
        'ref': project.project_ref or project.name,
        'address': address or 'No property linked',
        'rent': format_rent(price, unit),
        'size': format_size(size),
    }


@app.route('/projects')
def projects_list():
    q = request.args.get('q', '')
    status = request.args.get('status', '')
    query = Project.query
    if q:
        query = query.filter(
            db.or_(Project.name.ilike(f'%{q}%'), Project.client.ilike(f'%{q}%'),
                   Project.project_ref.ilike(f'%{q}%'))
        )
    if status:
        query = query.filter(Project.status == status)
    projects = query.order_by(Project.created_at.desc()).all()
    rows = [project_row_summary(p) for p in projects]
    # Which project the details panel opens on: the one asked for, else the first.
    selected = _fint(request.args.get('selected'))
    if selected not in {p.id for p in projects}:
        selected = projects[0].id if projects else None
    return render_template('projects/list.html', projects=projects, rows=rows,
                           selected_id=selected, q=q, status=status)


def _upsert_client_contact(form):
    """Create/update a CRM contact from a project's client details so every
    client entered on a project automatically shows up in Contacts. Matches an
    existing contact by email, else by name, to avoid duplicates. Returns the
    Contact (not committed here — caller commits with the project)."""
    name = (form.get('client') or '').strip()
    if not name:
        return None
    email  = (form.get('client_email') or '').strip() or None
    phone  = (form.get('client_phone') or '').strip() or None
    mobile = (form.get('client_mobile') or '').strip() or None
    # A letting instruction's client is the landlord; otherwise a general client.
    target_type = 'Landlord' if (form.get('instruction_type') or '').strip() == 'Letting' else 'Client'
    parts = name.split(' ', 1)
    first_name = parts[0] or name
    last_name  = parts[1] if len(parts) > 1 else '.'
    contact = None
    if email:
        contact = Contact.query.filter_by(email=email).first()
    if not contact:
        contact = Contact.query.filter(
            db.func.lower(Contact.first_name) == first_name.lower(),
            db.func.lower(Contact.last_name)  == last_name.lower(),
        ).first()
    if contact:
        if email  and not contact.email:  contact.email  = email
        if phone  and not contact.phone:  contact.phone  = phone
        if mobile and not contact.mobile: contact.mobile = mobile
        # Fill in a generic/blank type, and promote a Client → Landlord on a
        # letting, but never downgrade an existing Landlord/specific type.
        if contact.contact_type in (None, '', 'Enquiry', 'Prospect', 'Other') \
           or (target_type == 'Landlord' and contact.contact_type == 'Client'):
            contact.contact_type = target_type
    else:
        contact = Contact(
            first_name=first_name, last_name=last_name,
            email=email, phone=phone, mobile=mobile,
            contact_type=target_type,
        )
        db.session.add(contact)
    return contact


# ── CRM status + activity helpers ────────────────────────────────────────────
def _current_author():
    try:
        return current_user.username if current_user.is_authenticated else None
    except Exception:
        return None


def _parse_date(val):
    """Parse a yyyy-mm-dd form value into a date, or None."""
    val = (val or '').strip()
    try:
        return datetime.strptime(val, '%Y-%m-%d').date() if val else None
    except ValueError:
        return None


def _log_activity(kind, body=None, contact=None, organisation=None,
                  old_status=None, new_status=None):
    """Append an entry to a contact's / organisation's activity history."""
    act = ContactActivity(
        contact_id=contact.id if contact else None,
        organisation_id=organisation.id if organisation else None,
        kind=kind, body=body, old_status=old_status, new_status=new_status,
        author=_current_author())
    db.session.add(act)
    return act


def _apply_status(new_status, contact=None, organisation=None):
    """Set status on a contact or organisation; if it actually changed, record it
    in the activity history with the date. Returns True if changed."""
    if new_status not in CONTACT_STATUSES:
        return False
    target = contact or organisation
    old = getattr(target, 'status', None)
    if old == new_status:
        return False
    target.status = new_status
    _log_activity('status_change', contact=contact, organisation=organisation,
                  old_status=old, new_status=new_status,
                  body=f'Status changed from {old or "—"} to {new_status}')
    return True


@app.context_processor
def _inject_crm_constants():
    return {'CONTACT_STATUSES': CONTACT_STATUSES, 'ARCHIVED_STATUSES': ARCHIVED_STATUSES}


@app.route('/projects/new', methods=['GET', 'POST'])
def project_new():
    properties = Property.query.order_by(Property.address).all()

    def render(v, errors=None):
        return render_template('projects/form.html', properties=properties,
                               project=None, v=v, errors=errors or {})

    if request.method == 'POST':
        form = request.form
        errors = _validate_project_form(form)
        if errors:
            # Nothing is written, and everything typed comes straight back.
            return render(form, errors)

        mode = form.get('property_mode', 'existing')
        if mode == 'existing':
            # A property chosen from the register is used as it stands — no
            # second copy is made of a property already on file.
            prop = Property.query.get(_fint(form.get('property_id')) or 0)
        else:
            prop = _find_or_create_property(form)   # reuses a matching address

        name = (form.get('name') or '').strip() or \
            f"{form.get('instruction_type') or 'Instruction'} — {prop.address if prop else ''}".strip(' —')

        p = Project(
            property_id=prop.id if prop else None,
            name=name,
            project_ref=_ftext(form.get('project_ref')),
            status=form.get('status') or 'Active',
            fee_earner=_ftext(form.get('fee_earner')),
            client=_ftext(form.get('client')),
            landlord_name=_ftext(form.get('landlord_name')),
            instruction_date=_parse_date(form.get('instruction_date')),
            instruction_type=_ftext(form.get('instruction_type')),
            fee_percent=_fnum(form.get('fee_percent')),
            fee_fixed=_fnum(form.get('fee_fixed')),
            available_from=_parse_date(form.get('available_from')),
            next_call=_parse_date(form.get('next_call')),
            client_phone=_ftext(form.get('client_phone')),
            client_mobile=_ftext(form.get('client_mobile')),
            client_email=_ftext(form.get('client_email')),
            key_contact=_ftext(form.get('key_contact')),
            location_description=_ftext(form.get('location_description')),
            notes=_ftext(form.get('notes')),
        )
        db.session.add(p)
        _upsert_client_contact(form)     # matches an existing contact first
        db.session.flush()

        _new_project_listing(p, prop, form)
        db.session.commit()
        audit('create', entity='Project', entity_id=p.id, detail=p.instruction_type)
        flash('Project created.', 'success')
        return redirect(url_for('project_detail', id=p.id))

    # A fresh form, or one opened from a property page.
    start = {'status': 'Active', 'property_mode': 'existing',
             'property_id': request.args.get('property_id') or ''}
    return render(start)


# What an instruction can be. Anything else on an older record is left alone
# and flagged on screen for someone to look at, never rewritten quietly.
INSTRUCTION_TYPES = ['Letting', 'Sale', 'Sale or Letting']
app.jinja_env.globals['INSTRUCTION_TYPES'] = INSTRUCTION_TYPES


def instruction_type_ok(value, existing=None):
    """Whether an instruction type may be saved.

    One of the three, or blank, or the value already on the record — so a
    legacy instruction can be saved without being forced onto the new list.
    """
    value = (value or '').strip()
    return (not value) or value in INSTRUCTION_TYPES or value == (existing or '')


def project_form_values(project):
    """A project's fields as plain form values.

    The record boxes are shared between the Project Overview and the New
    Project page, so both hand them the same shape — a project's values, or
    a rejected submission being sent back for correction.
    """
    if project is None:
        return {'status': 'Active', 'property_mode': 'existing'}

    def d(val):
        return val.isoformat() if val else ''

    return {
        'name': project.name or '', 'project_ref': project.project_ref or '',
        'client': project.client or '', 'landlord_name': project.landlord_name or '',
        'status': project.status or 'Active',
        'instruction_type': project.instruction_type or '',
        'instruction_date': d(project.instruction_date),
        'available_from': d(project.available_from),
        'next_call': d(project.next_call),
        'fee_earner': project.fee_earner or '',
        'fee_percent': project.fee_percent or '', 'fee_fixed': project.fee_fixed or '',
        'key_contact': project.key_contact or '',
        'client_phone': project.client_phone or '', 'client_mobile': project.client_mobile or '',
        'client_email': project.client_email or '',
        'location_description': project.location_description or '',
        'notes': project.notes or '',
        'property_id': project.property_id or '',
        'property_mode': 'existing',
    }


app.jinja_env.globals['project_form_values'] = project_form_values


def _validate_project_form(form):
    """What is missing or wrong on a new project, keyed by field."""
    errors = {}
    if not (form.get('instruction_type') or '').strip():
        errors['instruction_type'] = 'Choose what kind of instruction this is.'
    elif not instruction_type_ok(form.get('instruction_type')):
        errors['instruction_type'] = 'Choose Letting, Sale, or Sale or Letting.'

    if form.get('property_mode', 'existing') == 'existing':
        prop = Property.query.get(_fint(form.get('property_id')) or 0)
        if prop is None:
            errors['property_id'] = ('Choose a property from the register, or switch to '
                                     '“Add a new one”.')
    else:
        if not (form.get('address') or '').strip():
            errors['address'] = 'A new property needs an address.'
        if not (form.get('postcode') or '').strip():
            errors['postcode'] = 'A new property needs a postcode.'

    email = (form.get('client_email') or '').strip()
    if email and '@' not in email:
        errors['client_email'] = 'That does not look like an email address.'
    return errors


def _new_project_listing(project, prop, form):
    """Create the website listing for a brand new project, if one is wanted.

    Only made when an asking price is given or a publishing option is ticked —
    otherwise the project starts without one, exactly as before, and a listing
    can be added later from the Project Overview.
    """
    price = _fnum(form.get('listing_price'))
    to_web = form.get('publish_website') == '1'
    to_zoopla = form.get('publish_zoopla') == '1'
    photos = [f for f in request.files.getlist('photos') if f and f.filename]
    if not (price or to_web or to_zoopla or photos):
        return None

    listing = Listing(project_id=project.id, property_id=prop.id if prop else None)
    if price:
        listing.listing_price = price
        listing.listing_price_unit = form.get('listing_price_unit') or 'pa'
    # Publishing happens only where it was actually asked for.
    if to_web:
        listing.website_listed = True
        listing.website_published_at = datetime.utcnow()
    if to_zoopla:
        listing.zoopla_listed = True
        listing.zoopla_published_at = datetime.utcnow()
    db.session.add(listing)
    db.session.flush()

    for position, f in enumerate(photos):
        ok, why = _read_image_upload(f)
        if not ok:
            flash(f'{f.filename} {why}', 'warning')
            continue
        data, mime, name = ok
        db.session.add(ListingPhoto(
            listing_id=listing.id, file_data=data, filename=name, file_mime=mime,
            file_size=len(data), sort_order=position,   # kept in the order chosen
        ))
    if to_web or to_zoopla:
        audit('publish', entity='Listing', entity_id=listing.id,
              detail=','.join(t for t, on in (('website', to_web), ('zoopla', to_zoopla)) if on))
    return listing


@app.route('/projects/<int:id>')
def project_detail(id):
    project = Project.query.get_or_404(id)
    matches = match_contacts_to_property(project.property) if project.property else []
    registered_ids = {pa.contact_id: pa for pa in project.applicants} if hasattr(project, 'applicants') else {}
    enquiries = Enquiry.query.filter_by(project_id=id).order_by(Enquiry.created_at.desc()).all()
    activity = []
    for n in (project.project_notes or []):
        activity.append({'type':'note','date':n.created_at,'author':n.author,'body':n.content})
    for e in enquiries:
        activity.append({'type':'enquiry','date':e.created_at,
                         'author':e.contact.full_name if e.contact else 'Website',
                         'body':e.subject,'notes':e.notes or ''})
    activity.sort(key=lambda x: x['date'], reverse=True)

    # One timeline for the Notes panel: the project's notes and its to-do tasks
    # together, newest first. Both are read from their own tables — nothing is
    # copied, so editing or completing a task shows through here immediately.
    floor = datetime.min
    notes_timeline = [{'kind': 'note', 'at': n.created_at or floor, 'author': n.author,
                       'body': n.content, 'id': n.id} for n in (project.project_notes or [])]
    notes_timeline += [{'kind': 'task', 'at': t.created_at or floor, 'author': t.created_by,
                        'body': t.title, 'id': t.id, 'completed': t.completed,
                        'due': t.due_date} for t in (project.tasks or [])]
    notes_timeline.sort(key=lambda x: x['at'], reverse=True)

    listing = project.project_listings[0] if project.project_listings else None
    pub = listing_publish_state(listing) if listing else None

    # ?panel=1 returns the same overview without the sidebar and top bar, for
    # the Projects list to drop into its details panel.
    layout = '_bare.html' if request.args.get('panel') else 'base.html'

    return render_template('projects/detail.html', project=project, layout=layout,
                           folder_labels=FOLDER_LABELS, today=date.today(),
                           matches=matches, registered_ids=registered_ids,
                           activity=activity, enquiries=enquiries,
                           notes_timeline=notes_timeline, pub=pub)


@app.route('/projects/<int:id>/edit', methods=['GET', 'POST'])
def project_edit(id):
    project = Project.query.get_or_404(id)
    properties = Property.query.order_by(Property.address).all()
    if request.method == 'POST':
        # Presence-guarded: the Project Overview is editable in place and posts
        # only the fields on screen, so a save must not blank the others.
        was_named = project.name
        was_type = project.instruction_type
        if 'instruction_type' in request.form and not instruction_type_ok(
                request.form.get('instruction_type'), was_type):
            flash('Choose Letting, Sale, or Sale or Letting.', 'warning')
            return _back_to('project_detail', id=project.id)
        apply_form_fields(project, request.form, PROJECT_FIELDS)
        if not (project.name or '').strip():
            project.name = was_named          # a project is never left nameless
        if 'property_id' in request.form:
            raw = request.form.get('property_id')
            project.property_id = int(raw) if raw else None
        if any(k in request.form for k in ('client', 'client_email', 'client_phone', 'client_mobile')):
            _upsert_client_contact(request.form)   # keep CRM in sync with client details
        db.session.commit()
        if project.instruction_type != was_type:
            audit('edit', entity='Project', entity_id=project.id,
                  detail=f'instruction type {was_type or "none"} to {project.instruction_type or "none"}')
        flash('Project updated.', 'success')
        return _back_to('project_detail', id=project.id)
    return render_template('projects/form.html', properties=properties, project=project,
                           v=project_form_values(project), errors={})


@app.route('/projects/<int:id>/delete', methods=['POST'])
@requires('delete')
def project_delete(id):
    project = Project.query.get_or_404(id)
    for enq in project.enquiries:
        enq.project_id = None
    db.session.delete(project)
    db.session.commit()
    flash('Project deleted.', 'info')
    return redirect(url_for('projects_list'))


@app.route('/projects/<int:id>/documents/add', methods=['POST'])
def document_add(id):
    project = Project.query.get_or_404(id)
    file = request.files.get('file')
    file_data = file_mime = None
    file_size = 0
    doc_name = request.form.get('document_name', '').strip()
    if file and file.filename:
        file_data = file.read()
        file_mime = file.content_type or 'application/octet-stream'
        file_size = len(file_data)
        if not doc_name:
            doc_name = file.filename
    if not doc_name:
        flash('Please provide a document name or upload a file.', 'warning')
        return redirect(url_for('project_detail', id=id) + '#tab-documents')
    doc = ProjectDocument(
        project_id=id,
        folder=request.form['folder'],
        document_name=doc_name,
        notes=request.form.get('notes'),
        file_data=file_data,
        file_mime=file_mime,
        file_size=file_size,
    )
    db.session.add(doc)
    db.session.commit()
    flash('Document added.', 'success')
    return redirect(url_for('project_detail', id=id) + '#tab-documents')


@app.route('/documents/<int:id>/delete', methods=['POST'])
@requires('delete')
def document_delete(id):
    doc = ProjectDocument.query.get_or_404(id)
    project_id = doc.project_id
    db.session.delete(doc)
    db.session.commit()
    flash('Document removed.', 'info')
    return redirect(url_for('project_detail', id=project_id) + '#tab-documents')


@app.route('/documents/<int:id>/download')
def document_download(id):
    from flask import send_file
    import io
    doc = ProjectDocument.query.get_or_404(id)
    if not doc.file_data:
        flash('No file attached to this document.', 'warning')
        return redirect(url_for('project_detail', id=doc.project_id))
    return send_file(
        io.BytesIO(doc.file_data),
        mimetype=doc.file_mime or 'application/octet-stream',
        as_attachment=True,
        download_name=doc.document_name,
    )


# ── Organisations ────────────────────────────────────────────────────────────

@app.route('/organisations')
def organisations_list():
    q = request.args.get('q', '')
    query = Organisation.query
    statuses = [s for s in request.args.getlist('status') if s in CONTACT_STATUSES]
    if statuses:
        query = query.filter(Organisation.status.in_(statuses))
    else:
        query = query.filter(db.or_(Organisation.status.is_(None),
                                    Organisation.status.notin_(ARCHIVED_STATUSES)))
    if q:
        query = query.filter(
            db.or_(Organisation.name.ilike(f'%{q}%'), Organisation.org_type.ilike(f'%{q}%'))
        )
    orgs = query.order_by(Organisation.name).all()
    status_counts = {s: Organisation.query.filter(Organisation.status == s).count()
                     for s in CONTACT_STATUSES}
    return render_template('crm/organisations_list.html', orgs=orgs, q=q,
                           statuses=statuses, status_counts=status_counts)


@app.route('/organisations/new', methods=['GET', 'POST'])
def organisation_new():
    if request.method == 'POST':
        org = Organisation(
            name=request.form['name'],
            org_type=request.form.get('org_type'),
            address=request.form.get('address'),
            postcode=request.form.get('postcode', '').upper(),
            phone=request.form.get('phone'),
            email=request.form.get('email'),
            website=request.form.get('website'),
            notes=request.form.get('notes'),
            status=(request.form.get('status') if request.form.get('status') in CONTACT_STATUSES else 'Prospect'),
        )
        db.session.add(org)
        db.session.commit()
        _log_activity('status_change', organisation=org, new_status=org.status,
                      body=f'Organisation created (status: {org.status})')
        db.session.commit()
        flash('Organisation added.', 'success')
        return redirect(url_for('organisation_detail', id=org.id))
    return render_template('crm/organisation_form.html', org=None)


@app.route('/organisations/<int:id>')
def organisation_detail(id):
    org = Organisation.query.get_or_404(id)
    return render_template('crm/organisation_detail.html', org=org)


@app.route('/organisations/<int:id>/edit', methods=['GET', 'POST'])
def organisation_edit(id):
    org = Organisation.query.get_or_404(id)
    if request.method == 'POST':
        org.name = request.form['name']
        org.org_type = request.form.get('org_type')
        org.address = request.form.get('address')
        org.postcode = request.form.get('postcode', '').upper()
        org.phone = request.form.get('phone')
        org.email = request.form.get('email')
        org.website = request.form.get('website')
        org.notes = request.form.get('notes')
        _apply_status(request.form.get('status'), organisation=org)
        db.session.commit()
        flash('Organisation updated.', 'success')
        return redirect(url_for('organisation_detail', id=org.id))
    return render_template('crm/organisation_form.html', org=org)


# ── Deleting records ─────────────────────────────────────────────────────────

def delete_record(obj, label, redirect_endpoint, **kw):
    """Delete a record, rolling back and explaining if the database refuses.

    The cascades on the models decide what goes with a record. This wrapper is
    the backstop: if some future relationship is left without one, the person
    gets a clear message and an intact database instead of a 500 page.
    """
    from sqlalchemy.exc import SQLAlchemyError
    try:
        entity_id = getattr(obj, 'id', None)
        db.session.delete(obj)
        db.session.commit()
        audit('delete', entity=label, entity_id=entity_id)
        flash(f'{label} deleted.', 'info')
    except SQLAlchemyError as ex:
        db.session.rollback()
        app.logger.exception('Delete failed for %s', label)
        flash(f'{label} could not be deleted — it is still linked to other records. '
              f'({type(ex).__name__})', 'danger')
    return redirect(url_for(redirect_endpoint, **kw))


@app.route('/organisations/<int:id>/delete', methods=['POST'])
@requires('delete')
def organisation_delete(id):
    org = Organisation.query.get_or_404(id)
    return delete_record(org, 'Organisation', 'organisations_list')


# ── Contacts ─────────────────────────────────────────────────────────────────

@app.route('/contacts')
def contacts_list():
    q = request.args.get('q', '')
    ctype = request.args.get('type', '')   # Landlord / Tenant / Client section filter
    query = Contact.query
    # Section filters. Tenants include prospective tenants; landlords are exact.
    _type_groups = {
        'Landlord': ['Landlord'],
        'Tenant':   ['Tenant', 'Prospective Tenant'],
        'Client':   ['Client'],
    }
    if ctype in _type_groups:
        query = query.filter(Contact.contact_type.in_(_type_groups[ctype]))
    # Multi-select status filter. With none chosen, hide Archived from the default view.
    statuses = [s for s in request.args.getlist('status') if s in CONTACT_STATUSES]
    if statuses:
        query = query.filter(Contact.status.in_(statuses))
    else:
        query = query.filter(db.or_(Contact.status.is_(None),
                                    Contact.status.notin_(ARCHIVED_STATUSES)))
    if q:
        query = query.filter(
            db.or_(Contact.first_name.ilike(f'%{q}%'), Contact.last_name.ilike(f'%{q}%'),
                   Contact.email.ilike(f'%{q}%'), Contact.contact_type.ilike(f'%{q}%'))
        )
    contacts = query.order_by(Contact.last_name, Contact.first_name).all()
    # Counts for the section tabs
    counts = {k: Contact.query.filter(Contact.contact_type.in_(v)).count()
              for k, v in _type_groups.items()}
    counts['all'] = Contact.query.count()
    status_counts = {s: Contact.query.filter(Contact.status == s).count()
                     for s in CONTACT_STATUSES}
    return render_template('crm/contacts_list.html', contacts=contacts, q=q,
                           ctype=ctype, counts=counts, statuses=statuses,
                           status_counts=status_counts)


@app.route('/contacts/new', methods=['GET', 'POST'])
def contact_new():
    organisations = Organisation.query.order_by(Organisation.name).all()
    if request.method == 'POST':
        org_id_raw = request.form.get('organisation_id')
        c = Contact(
            first_name=request.form['first_name'],
            last_name=request.form['last_name'],
            job_title=request.form.get('job_title'),
            organisation_id=int(org_id_raw) if org_id_raw else None,
            phone=request.form.get('phone'),
            mobile=request.form.get('mobile'),
            email=request.form.get('email'),
            contact_type=request.form.get('contact_type'),
            notes=request.form.get('notes'),
            req_category=request.form.get('req_category') or None,
            req_property_type=request.form.get('req_property_type') or None,
            req_use_class=request.form.get('req_use_class') or None,
            req_area=request.form.get('req_area') or None,
            req_size_min=float(request.form.get('req_size_min')) if request.form.get('req_size_min','').strip() else None,
            req_size_max=float(request.form.get('req_size_max')) if request.form.get('req_size_max','').strip() else None,
            req_budget_min=float(request.form.get('req_budget_min')) if request.form.get('req_budget_min','').strip() else None,
            req_budget_max=float(request.form.get('req_budget_max')) if request.form.get('req_budget_max','').strip() else None,
            req_budget_unit=request.form.get('req_budget_unit') or 'pa',
            req_notes=request.form.get('req_notes') or None,
            status=(request.form.get('status') if request.form.get('status') in CONTACT_STATUSES else 'Prospect'),
            preferred_move_in=_parse_date(request.form.get('preferred_move_in')),
            lease_length=request.form.get('lease_length') or None,
            assigned_agent=request.form.get('assigned_agent') or None,
            last_contact_date=_parse_date(request.form.get('last_contact_date')),
            next_follow_up=_parse_date(request.form.get('next_follow_up')),
        )
        db.session.add(c)
        db.session.commit()
        _log_activity('status_change', contact=c, new_status=c.status,
                      body=f'Contact created (status: {c.status})')
        db.session.commit()
        flash('Contact added.', 'success')
        return redirect(url_for('contact_detail', id=c.id))
    return render_template('crm/contact_form.html', contact=None, organisations=organisations)


@app.route('/contacts/<int:id>')
def contact_detail(id):
    contact = Contact.query.get_or_404(id)
    return render_template('crm/contact_detail.html',
                           all_organisations=Organisation.query.order_by(Organisation.name).all(), contact=contact)


@app.route('/contacts/<int:id>/edit', methods=['GET', 'POST'])
def contact_edit(id):
    contact = Contact.query.get_or_404(id)
    organisations = Organisation.query.order_by(Organisation.name).all()
    if request.method == 'POST':
        _save_contact_from_form(contact, request.form)
        db.session.commit()
        flash('Contact updated.', 'success')
        return _back_to('contact_detail', id=contact.id)
    return render_template('crm/contact_form.html', contact=contact, organisations=organisations)


# ── Inline editing ───────────────────────────────────────────────────────────

def _fnum(v):
    v = (v or '').replace(',', '').strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


def _fint(v):
    v = (v or '').strip()
    try:
        return int(float(v)) if v else None
    except ValueError:
        return None


def _ftext(v):
    v = (v or '').strip()
    return v or None


def apply_form_fields(obj, form, fields):
    """Write form values onto a record, skipping anything the form did not send.

    Record pages are editable in place and post only the fields shown on that
    page, so a save must never blank a column simply because this page does not
    display it. Each entry is (attribute, form key, converter).
    """
    for attr, key, conv in fields:
        if key in form:
            setattr(obj, attr, conv(form.get(key)) if conv else form.get(key))


CONTACT_FIELDS = [
    ('first_name',        'first_name',        None),
    ('last_name',         'last_name',         None),
    ('job_title',         'job_title',         _ftext),
    ('phone',             'phone',             _ftext),
    ('mobile',            'mobile',            _ftext),
    ('email',             'email',             _ftext),
    ('contact_type',      'contact_type',      _ftext),
    ('notes',             'notes',             _ftext),
    ('req_category',      'req_category',      _ftext),
    ('req_property_type', 'req_property_type', _ftext),
    ('req_use_class',     'req_use_class',     _ftext),
    ('req_area',          'req_area',          _ftext),
    ('req_size_min',      'req_size_min',      _fnum),
    ('req_size_max',      'req_size_max',      _fnum),
    ('req_budget_min',    'req_budget_min',    _fnum),
    ('req_budget_max',    'req_budget_max',    _fnum),
    ('req_budget_unit',   'req_budget_unit',   _ftext),
    ('req_notes',         'req_notes',         _ftext),
    ('preferred_move_in', 'preferred_move_in', _parse_date),
    ('lease_length',      'lease_length',      _ftext),
    ('assigned_agent',    'assigned_agent',    _ftext),
    ('last_contact_date', 'last_contact_date', _parse_date),
    ('next_follow_up',    'next_follow_up',    _parse_date),
]


PROJECT_FIELDS = [
    ('name',                 'name',                 None),
    ('project_ref',          'project_ref',          _ftext),
    ('status',               'status',               _ftext),
    ('fee_earner',           'fee_earner',           _ftext),
    ('client',               'client',               _ftext),
    ('instruction_date',     'instruction_date',     _parse_date),
    ('notes',                'notes',                _ftext),
    ('instruction_type',     'instruction_type',     _ftext),
    ('available_from',       'available_from',       _parse_date),
    ('next_call',            'next_call',            _parse_date),
    ('client_phone',         'client_phone',         _ftext),
    ('client_mobile',        'client_mobile',        _ftext),
    ('client_email',         'client_email',         _ftext),
    ('key_contact',          'key_contact',          _ftext),
    ('landlord_name',        'landlord_name',        _ftext),
    ('agent_assigned',       'agent_assigned',       _ftext),
    ('location_description', 'location_description', _ftext),
    ('fee_percent',          'fee_percent',          _fnum),
    ('fee_fixed',            'fee_fixed',            _fnum),
]


PROPERTY_FIELDS = [
    ('address',          'address',          None),
    ('property_type',    'property_type',    _ftext),
    ('size',             'size',             _fnum),
    ('measurement_type', 'measurement_type', _ftext),
    ('description',      'description',      _ftext),
    ('residential_use',  'residential_use',  _ftext),
]


ENQUIRY_FIELDS = [
    ('subject',             'subject',             None),
    ('enquiry_type',        'enquiry_type',        _ftext),
    ('status',              'status',              _ftext),
    ('source',              'source',              _ftext),
    ('fee_earner',          'fee_earner',          _ftext),
    ('priority',            'priority',            _ftext),
    ('preferred_contact',   'preferred_contact',   _ftext),
    ('received_date',       'received_date',       _parse_date),
    ('notes',               'notes',               _ftext),
    ('req_category',        'req_category',        _ftext),
    ('req_property_type',   'req_property_type',   _ftext),
    ('req_use_class',       'req_use_class',       _ftext),
    ('req_area',            'req_area',            _ftext),
    ('req_tenure',          'req_tenure',          _ftext),
    ('req_occupation_date', 'req_occupation_date', _parse_date),
    ('req_size_min',        'req_size_min',        _fnum),
    ('req_size_max',        'req_size_max',        _fnum),
    ('req_budget_min',      'req_budget_min',      _fnum),
    ('req_budget_max',      'req_budget_max',      _fnum),
    ('req_budget_unit',     'req_budget_unit',     _ftext),
    ('req_notes',           'req_notes',           _ftext),
    ('next_action',         'next_action',         _ftext),
    ('next_call_date',      'next_call_date',      _parse_date),
    ('next_follow_up',      'next_follow_up',      _parse_date),
    ('last_contact_date',   'last_contact_date',   _parse_date),
]

# Which record each enquiry is linked to. Kept apart from the plain fields
# because an empty box means "no link", not "leave it alone".
ENQUIRY_LINKS = ['property_id', 'contact_id', 'organisation_id', 'project_id']


def enquiry_subject(enquiry_type, prop):
    """The subject line an enquiry gets when nobody has written one.

    Built from the enquiry type and the property, so "Agency — Letting" about
    57B New Kings Road reads as one line in the list.
    """
    etype = (enquiry_type or '').strip() or 'Enquiry'
    where = property_address(prop)
    return f'{etype} — {where}' if where else etype


def _save_enquiry_from_form(e, form):
    """Apply an inline edit to an enquiry, sending back only what was shown."""
    apply_form_fields(e, form, ENQUIRY_FIELDS)
    for key in ENQUIRY_LINKS:
        if key in form:
            setattr(e, key, _fint(form.get(key)))
    # An empty subject is filled in from the type and property, the same way
    # the new-enquiry form does it as you type.
    if not (e.subject or '').strip():
        e.subject = enquiry_subject(e.enquiry_type, e.linked_property)
    # The applicant's number and address are the contact's own, but they are
    # shown and corrected here, so write them back to the contact record.
    if e.contact:
        for attr in ('mobile', 'email'):
            key = f'contact_{attr}'
            if key in form:
                setattr(e.contact, attr, _ftext(form.get(key)))


def _save_contact_from_form(contact, form):
    apply_form_fields(contact, form, CONTACT_FIELDS)
    if 'organisation_id' in form:
        raw = form.get('organisation_id')
        contact.organisation_id = int(raw) if raw else None
    if 'status' in form:
        _apply_status(form.get('status'), contact=contact)


def _back_to(default_endpoint, **kw):
    """Return to the page the form was submitted from, when it says where."""
    nxt = request.form.get('next') or request.args.get('next') or ''
    if nxt.startswith('/') and not nxt.startswith('//'):
        return redirect(nxt)
    return redirect(url_for(default_endpoint, **kw))


@app.route('/contacts/<int:id>/delete', methods=['POST'])
@requires('delete')
def contact_delete(id):
    contact = Contact.query.get_or_404(id)
    return delete_record(contact, 'Contact', 'contacts_list')


@app.route('/contacts/<int:id>/status', methods=['POST'])
def contact_set_status(id):
    """Inline status change from the contact record header (no separate page)."""
    contact = Contact.query.get_or_404(id)
    if _apply_status(request.form.get('status'), contact=contact):
        db.session.commit()
        flash(f'Status updated to {contact.status}.', 'success')
    return redirect(request.referrer or url_for('contact_detail', id=contact.id))


@app.route('/contacts/<int:id>/activity', methods=['POST'])
def contact_add_activity(id):
    """Log an interaction / note to the contact's activity history."""
    contact = Contact.query.get_or_404(id)
    body = (request.form.get('body') or '').strip()
    if body:
        kind = request.form.get('kind') or 'note'
        _log_activity(kind, body=body, contact=contact)
        if kind == 'interaction':
            contact.last_contact_date = date.today()
        db.session.commit()
        flash('Activity logged.', 'success')
    return redirect(url_for('contact_detail', id=contact.id) + '#activity')


@app.route('/organisations/<int:id>/status', methods=['POST'])
def organisation_set_status(id):
    """Inline status change from the organisation record header."""
    org = Organisation.query.get_or_404(id)
    if _apply_status(request.form.get('status'), organisation=org):
        db.session.commit()
        flash(f'Status updated to {org.status}.', 'success')
    return redirect(request.referrer or url_for('organisation_detail', id=org.id))


# ── Enquiries ─────────────────────────────────────────────────────────────────

# The working states an enquiry passes through, as shown on the filter bar.
# These sit on top of the existing stage pipeline rather than replacing it.
ENQUIRY_BUCKETS = [
    ('new',       'New'),
    ('contacted', 'Contacted'),
    ('qualified', 'Qualified'),
    ('viewing',   'Viewing arranged'),
    ('offer',     'Offer made'),
    ('closed',    'Closed'),
    ('archived',  'Archived'),
]


def enquiry_bucket(e):
    """Which of the filter bar's states this enquiry is currently in."""
    if e.archived:
        return 'archived'
    stage = e.stage or 'Enquiry Received'
    if (stage in ENQUIRY_STAGES_CLOSED or stage == 'Heads of Terms Signed'
            or e.status in ('Won', 'Lost')):
        return 'closed'
    if stage in ('Offer Received', 'Terms Agreed', 'Heads of Terms Issued'):
        return 'offer'
    if stage in ('Viewing Arranged', 'Viewing Completed'):
        return 'viewing'
    if stage == 'Qualified':
        return 'qualified'
    if e.last_contact_date:
        return 'contacted'
    return 'new'


ENQUIRY_BUCKET_LABELS = dict(ENQUIRY_BUCKETS)
app.jinja_env.globals['enquiry_state'] = enquiry_bucket
app.jinja_env.globals['enquiry_state_label'] = lambda key: ENQUIRY_BUCKET_LABELS.get(key, key)


def _enquiry_haystack(e):
    """Everything the search box should look through for one enquiry."""
    bits = [e.subject, e.enquiry_type, e.fee_earner, e.source, e.notes,
            e.req_area, e.next_action]
    if e.contact:
        bits.append(e.contact.full_name)
        bits += [e.contact.email, e.contact.mobile, e.contact.phone]
    if e.organisation:
        bits.append(e.organisation.name)
    if e.linked_property:
        bits += [e.linked_property.address, e.linked_property.postcode]
    return ' '.join(b for b in bits if b).lower()


# Panel colours, kept away from the brand red so status never reads as an alert.
ENQUIRY_CHART_COLOURS = ['#c9992b', '#1c3160', '#2f8f83', '#7b4b8a', '#4a5568',
                         '#5b8c5a', '#b06a3b', '#8a939f']


def _slice_counts(rows, colours=ENQUIRY_CHART_COLOURS):
    """Turn {label: count} into ring segments, largest first, with angles."""
    total = sum(rows.values()) or 0
    out, offset = [], 0.0
    for i, (label, count) in enumerate(sorted(rows.items(), key=lambda kv: -kv[1])):
        share = (count / total * 100) if total else 0
        out.append({'label': label, 'count': count, 'pct': round(share),
                    'dash': round(share, 2), 'offset': round(offset, 2),
                    'colour': colours[i % len(colours)]})
        offset += share
    return out


def _enquiry_overview(enqs, today):
    """The figures behind the summary panels at the top of the Enquiries page.

    Everything is counted from the enquiries currently in view, so the panels
    always describe the same set as the list underneath them.
    """
    ids = [e.id for e in enqs]
    total = len(enqs)

    # Received per day over the last month, split into worked and overdue.
    days, by_day = [], {}
    for e in enqs:
        on = e.received_date or (e.created_at.date() if e.created_at else None)
        if on and (today - on).days < 30 and on <= today:
            slot = by_day.setdefault(on, {'ok': 0, 'late': 0})
            late = e.next_follow_up and e.next_follow_up < today and not e.archived
            slot['late' if late else 'ok'] += 1
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        slot = by_day.get(d, {'ok': 0, 'late': 0})
        days.append({'date': d, 'label': d.strftime('%-d %b'),
                     'ok': slot['ok'], 'late': slot['late'],
                     'total': slot['ok'] + slot['late']})
    peak = max([d['total'] for d in days] or [0]) or 1

    buckets = {key: 0 for key, _ in ENQUIRY_BUCKETS}
    for e in enqs:
        buckets[enquiry_bucket(e)] += 1

    matched   = sum(1 for e in enqs if e.property_id)
    contacted = sum(1 for e in enqs if e.last_contact_date)
    overdue   = sum(1 for e in enqs if e.next_follow_up and e.next_follow_up < today
                    and not e.archived)
    converted = sum(1 for e in enqs if (e.stage == 'Heads of Terms Signed'
                                        or e.status == 'Won'))
    viewings  = sum(1 for e in enqs if enquiry_bucket(e) == 'viewing')
    terms     = sum(1 for e in enqs if e.stage in ('Terms Agreed', 'Heads of Terms Issued'))

    # Correspondence, counted in one go rather than per enquiry.
    notes = emails = 0
    if ids:
        rows = (db.session.query(EnquiryNote.direction, db.func.count(EnquiryNote.id))
                .filter(EnquiryNote.enquiry_id.in_(ids))
                .group_by(EnquiryNote.direction).all())
        for direction, count in rows:
            if direction in ('inbound', 'outbound'):
                emails += count
            else:
                notes += count

    def panel(title, rows, foot_label, foot_value):
        top = max([r[1] for r in rows] or [0]) or 1
        return {'title': title, 'foot_label': foot_label, 'foot_value': foot_value,
                'rows': [{'label': l, 'count': c, 'width': round(c / top * 100)}
                         for l, c in rows]}

    def rate(part):
        return f'{round(part / total * 100)}%' if total else '—'

    look_for = {}
    for e in enqs:
        key = (e.req_use_class or e.req_property_type or e.req_category or '').strip()
        look_for[key.title() if key else 'Not specified'] = \
            look_for.get(key.title() if key else 'Not specified', 0) + 1

    sources = {}
    for e in enqs:
        key = (e.source or 'Direct').strip()
        sources[key] = sources.get(key, 0) + 1

    return {
        'total': total,
        'days': days, 'peak': peak,
        'buckets': buckets,
        'panels': [
            panel('Enquiry summary',
                  [('Enquiries received', total),
                   ('Matched to a property', matched),
                   ('Contact made', contacted)],
                  'Matched to a property', rate(matched)),
            panel('Pipeline summary',
                  [('Contact made', contacted),
                   ('Viewing arranged', viewings),
                   ('Terms agreed', terms),
                   ('Converted', converted)],
                  'Conversion rate', rate(converted)),
            panel('Follow-up summary',
                  [('Notes and calls logged', notes),
                   ('Emails exchanged', emails),
                   ('Follow-ups overdue', overdue)],
                  'Followed up on time', rate(contacted - overdue) if total else '—'),
        ],
        'rings': [
            {'title': 'What applicants are looking for', 'slices': _slice_counts(look_for)},
            {'title': 'Enquiries received by source', 'slices': _slice_counts(sources)},
        ],
    }


@app.route('/enquiries')
def enquiries_list():
    today = date.today()
    args = request.args
    q           = args.get('q', '').strip()
    bucket      = args.get('bucket', '')
    status      = args.get('status', '')            # kept: older links still use it
    source      = args.get('source', '')
    etype       = args.get('type', '')
    negotiator  = args.get('negotiator', '')
    property_id = _fint(args.get('property_id'))
    from_date   = _parse_date(args.get('from'))
    to_date     = _parse_date(args.get('to'))
    followup    = args.get('followup', '')

    # Newest first by the date it came in, falling back to when it was keyed.
    everything = Enquiry.query.all()
    everything.sort(key=lambda e: (e.received_date or date.min,
                                   e.created_at or datetime.min), reverse=True)

    def keep(e, skip_bucket=False):
        if q and q.lower() not in _enquiry_haystack(e):
            return False
        if status and e.status != status:
            return False
        if source and (e.source or 'Direct') != source:
            return False
        if etype and e.enquiry_type != etype:
            return False
        if negotiator and e.fee_earner != negotiator:
            return False
        if property_id and e.property_id != property_id:
            return False
        on = e.received_date or (e.created_at.date() if e.created_at else None)
        if from_date and (on is None or on < from_date):
            return False
        if to_date and (on is None or on > to_date):
            return False
        if followup == 'due' and not (e.next_follow_up and e.next_follow_up <= today):
            return False
        if followup == 'overdue' and not (e.next_follow_up and e.next_follow_up < today):
            return False
        if followup == 'none' and e.next_follow_up:
            return False
        if not skip_bucket:
            if bucket:
                if enquiry_bucket(e) != bucket:
                    return False
            elif e.archived:
                return False        # archived stays out of the working list
        return True

    enquiries = [e for e in everything if keep(e)]
    # The chip counts describe what each chip would show, so they ignore the
    # chip currently pressed but respect every other filter.
    chip_pool = [e for e in everything if keep(e, skip_bucket=True)]
    counts = {key: 0 for key, _ in ENQUIRY_BUCKETS}
    for e in chip_pool:
        counts[enquiry_bucket(e)] += 1

    overview = _enquiry_overview(enquiries, today)
    overview['buckets'] = counts

    filters_on = any([q, bucket, status, source, etype, negotiator, property_id,
                      from_date, to_date, followup])

    return render_template(
        'crm/enquiries_list.html',
        enquiries=enquiries, overview=overview, today=today,
        selected_id=_fint(args.get('selected')),
        buckets=ENQUIRY_BUCKETS, counts=counts, filters_on=filters_on,
        q=q, bucket=bucket, status=status, source=source, type_=etype,
        negotiator=negotiator, property_id=property_id, followup=followup,
        from_date=args.get('from', ''), to_date=args.get('to', ''),
        all_sources=sorted({(e.source or 'Direct') for e in everything}),
        all_types=sorted({e.enquiry_type for e in everything if e.enquiry_type}),
        all_negotiators=sorted({e.fee_earner for e in everything if e.fee_earner}),
        all_properties=Property.query.order_by(Property.address).all(),
    )


def _parse_enquiry_form(form, e=None):
    def pd(v): return datetime.strptime(v, '%Y-%m-%d').date() if v else None
    def pf(v): return float(v.replace(',','')) if v and v.strip() else None
    def pi(v): return int(v) if v else None
    fields = dict(
        subject=form['subject'],
        enquiry_type=form.get('enquiry_type'),
        status=form.get('status', 'Open'),
        property_id=pi(form.get('property_id')),
        contact_id=pi(form.get('contact_id')),
        organisation_id=pi(form.get('organisation_id')),
        project_id=pi(form.get('project_id')),
        fee_earner=form.get('fee_earner'),
        received_date=pd(form.get('received_date')),
        last_contact_date=pd(form.get('last_contact_date')),
        next_follow_up=pd(form.get('next_follow_up')),
        notes=form.get('notes'),
        req_size_min=pf(form.get('req_size_min')),
        req_size_max=pf(form.get('req_size_max')),
        req_budget_min=pf(form.get('req_budget_min')),
        req_budget_max=pf(form.get('req_budget_max')),
        req_budget_unit=form.get('req_budget_unit') or 'pa',
        req_use_class=form.get('req_use_class') or None,
        req_category=form.get('req_category') or None,
    )
    if e:
        for k, v in fields.items(): setattr(e, k, v)
    return fields


@app.route('/enquiries/new', methods=['GET', 'POST'])
def enquiry_new():
    properties = Property.query.order_by(Property.address).all()
    contacts = Contact.query.order_by(Contact.last_name).all()
    organisations = Organisation.query.order_by(Organisation.name).all()
    projects = Project.query.order_by(Project.name).all()
    if request.method == 'POST':
        e = Enquiry(**_parse_enquiry_form(request.form))
        db.session.add(e)
        db.session.commit()
        flash('Enquiry recorded.', 'success')
        return redirect(url_for('enquiry_detail', id=e.id))
    return render_template('crm/enquiry_form.html', enquiry=None,
                           properties=properties, contacts=contacts,
                           organisations=organisations, projects=projects)


def _enquiry_activity(e):
    """Everything that has happened on an enquiry, newest first.

    Three sources are woven together: notes and correspondence, movements
    through the pipeline, and any diary appointments booked against it.
    """
    items = []

    for n in e.notes_chain:
        kind = {'inbound': 'Email in', 'outbound': 'Email out'}.get(n.direction, 'Note')
        items.append({
            'when': n.created_at, 'on': n.created_at.date() if n.created_at else None,
            'kind': kind, 'icon': '✉' if n.direction in ('inbound', 'outbound') else '✎',
            'title': n.subject or kind, 'body': n.body,
            'who': n.author, 'url': None, 'delete': url_for('enquiry_note_delete', id=n.id),
        })

    for ev in e.stage_events:
        items.append({
            'when': datetime.combine(ev.occurred_on or date.today(), datetime.min.time()),
            'on': ev.occurred_on, 'kind': 'Status', 'icon': '●',
            'title': ev.stage + (f' (from {ev.from_stage})' if ev.from_stage else ''),
            'body': ev.note, 'who': ev.author, 'url': None, 'delete': None,
        })

    for ap in DiaryEvent.query.filter_by(enquiry_id=e.id).all():
        label = EVENT_TYPES.get(ap.event_type, ('Appointment', ''))[0]
        items.append({
            'when': ap.start_at, 'on': ap.start_at.date() if ap.start_at else None,
            'kind': label, 'icon': '\U0001f4c5', 'title': ap.title,
            'body': ap.place or ap.notes, 'who': ap.owner,
            'url': url_for('diary_event', id=ap.id), 'delete': None,
        })

    items.sort(key=lambda i: i['when'] or datetime.min, reverse=True)
    return items


@app.route('/enquiries/<int:id>')
def enquiry_detail(id):
    e = Enquiry.query.get_or_404(id)
    # Match properties to enquiry requirements
    matched = []
    q = Property.query.filter_by(website_listed=True)
    if e.req_category:
        q = q.filter_by(website_category=e.req_category)
    if e.req_use_class:
        q = q.filter_by(use_class=e.req_use_class)
    candidates = q.all()
    for p in candidates:
        score = 0
        if e.req_size_min and p.size and p.size >= e.req_size_min: score += 1
        if e.req_size_max and p.size and p.size <= e.req_size_max: score += 1
        if e.req_budget_max and p.listing_price and p.listing_price <= e.req_budget_max: score += 2
        if e.req_budget_min and p.listing_price and p.listing_price >= e.req_budget_min: score += 1
        if score > 0:
            matched.append((score, p))
    matched.sort(key=lambda x: x[0], reverse=True)
    matched_props = [p for _, p in matched[:6]]
    return render_template('crm/enquiry_detail.html', e=e, matched_props=matched_props,
                           today=date.today(),
                           activity=_enquiry_activity(e),
                           enquiry_stages=ENQUIRY_STAGES,
                           enquiry_stages_closed=ENQUIRY_STAGES_CLOSED,
                           next_stage=next_enquiry_stage(e.stage),
                           bucket=enquiry_bucket(e),
                           event_types=EVENT_TYPES,
                           all_contacts=Contact.query.order_by(Contact.last_name).all(),
                           all_properties=Property.query.order_by(Property.address).all(),
                           all_organisations=Organisation.query.order_by(Organisation.name).all(),
                           all_projects=Project.query.order_by(Project.name).all())


@app.route('/enquiries/<int:id>/edit', methods=['GET', 'POST'])
def enquiry_edit(id):
    e = Enquiry.query.get_or_404(id)
    properties = Property.query.order_by(Property.address).all()
    contacts = Contact.query.order_by(Contact.last_name).all()
    organisations = Organisation.query.order_by(Organisation.name).all()
    projects = Project.query.order_by(Project.name).all()
    if request.method == 'POST':
        _save_enquiry_from_form(e, request.form)
        db.session.commit()
        audit('edit', entity='Enquiry', entity_id=e.id)
        flash('Enquiry updated.', 'success')
        return _back_to('enquiry_detail', id=e.id)
    return render_template('crm/enquiry_form.html', enquiry=e,
                           properties=properties, contacts=contacts,
                           organisations=organisations, projects=projects)


@app.route('/enquiries/<int:id>/log-contact', methods=['POST'])
def enquiry_log_contact(id):
    e = Enquiry.query.get_or_404(id)
    e.last_contact_date = date.today()
    nf = request.form.get('next_follow_up', '').strip()
    if nf:
        try: e.next_follow_up = datetime.strptime(nf, '%Y-%m-%d').date()
        except: pass
    db.session.commit()
    flash(f'Contact logged for "{e.subject}". Follow-up set.', 'success')
    return redirect(request.referrer or url_for('enquiries_list'))


@app.route('/enquiries/<int:id>/archive', methods=['POST'])
def enquiry_archive(id):
    """Put an enquiry away, or bring it back. Nothing is deleted."""
    e = Enquiry.query.get_or_404(id)
    e.archived = not bool(e.archived)
    db.session.commit()
    audit('archive' if e.archived else 'restore', entity='Enquiry', entity_id=e.id)
    flash('Enquiry archived.' if e.archived else 'Enquiry restored.', 'info')
    return _back_to('enquiry_detail', id=e.id)


@app.route('/enquiries/<int:id>/schedule', methods=['POST'])
def enquiry_schedule(id):
    """Book a call, viewing or task against an enquiry, in the CRM diary.

    Appointments are held at a property, so the enquiry's property is used;
    without one there is nowhere to put the appointment.
    """
    e = Enquiry.query.get_or_404(id)
    if not e.property_id:
        flash('Link a property to this enquiry before booking anything in the diary.', 'warning')
        return redirect(url_for('enquiry_detail', id=e.id))

    kind = request.form.get('event_type', 'appointment')
    if kind not in EVENT_TYPES:
        kind = 'appointment'
    try:
        local = datetime.strptime(request.form.get('start', ''), '%Y-%m-%dT%H:%M')
    except ValueError:
        flash('Choose a date and time.', 'warning')
        return redirect(url_for('enquiry_detail', id=e.id))
    minutes = _fint(request.form.get('minutes')) or 60
    start = from_london(local)          # the diary stores UTC, shows London

    prop = Property.query.get(e.property_id)
    ev = DiaryEvent(
        title=request.form.get('title', '').strip()
              or f'{EVENT_TYPES[kind][0]} — {e.subject[:80]}',
        start_at=start, end_at=start + timedelta(minutes=minutes),
        event_type=kind,
        owner=request.form.get('owner', '').strip() or e.fee_earner
              or getattr(current_user, 'username', None),
        property_id=e.property_id, contact_id=e.contact_id,
        project_id=e.project_id, enquiry_id=e.id,
        location=property_address(prop),
        notes=request.form.get('notes', '').strip() or None,
        created_by=getattr(current_user, 'username', None),
    )
    db.session.add(ev)
    if kind == 'call':
        e.next_call_date = local.date()
    db.session.commit()
    audit('create', entity='DiaryEvent', entity_id=ev.id, detail=f'from enquiry {e.id}')
    flash(f'{EVENT_TYPES[kind][0]} added to the diary.', 'success')
    return redirect(url_for('enquiry_detail', id=e.id))


@app.route('/enquiries/<int:id>/delete', methods=['POST'])
@requires('delete')
def enquiry_delete(id):
    e = Enquiry.query.get_or_404(id)
    db.session.delete(e)
    db.session.commit()
    flash('Enquiry deleted.', 'info')
    return redirect(url_for('enquiries_list'))


@app.route('/enquiries/<int:id>/notes/add', methods=['POST'])
def enquiry_note_add(id):
    e = Enquiry.query.get_or_404(id)
    body = request.form.get('body', '').strip()
    if body:
        n = EnquiryNote(
            enquiry_id=id,
            direction=request.form.get('direction', 'note'),
            subject=request.form.get('subject', '').strip() or None,
            body=body,
            author=request.form.get('author', '').strip() or 'Unknown',
        )
        db.session.add(n)
        e.last_contact_date = date.today()
        lf = request.form.get('next_follow_up', '').strip()
        if lf:
            try: e.next_follow_up = datetime.strptime(lf, '%Y-%m-%d').date()
            except: pass
        db.session.commit()
        flash('Note added.', 'success')
    return redirect(url_for('enquiry_detail', id=id))


@app.route('/enquiry-notes/<int:id>/delete', methods=['POST'])
@requires('delete')
def enquiry_note_delete(id):
    n = EnquiryNote.query.get_or_404(id)
    enq_id = n.enquiry_id
    db.session.delete(n)
    db.session.commit()
    return redirect(url_for('enquiry_detail', id=enq_id))


# ── Email Integration ──────────────────────────────────────────────────────────

@app.route('/email/sync', methods=['POST'])
@requires('admin')
@requires('admin')
def email_sync_trigger():
    from email_sync import sync_inbox, check_configured
    if not check_configured():
        flash('Email not configured — set MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET in Railway variables.', 'warning')
        return redirect(request.referrer or url_for('dashboard'))
    try:
        counts = sync_inbox(db, Contact, Enquiry, EnquiryNote)
        leads, emails = counts['leads'], counts['emails']
        if leads and emails:
            msg = f'Synced {emails} new email(s) and {leads} portal lead(s).'
        elif leads:
            msg = f'Synced {leads} new portal lead(s).'
        elif emails:
            msg = f'Synced {emails} new email(s).'
        else:
            msg = 'No new emails.'
        flash(msg, 'success' if (leads or emails) else 'info')
    except Exception as ex:
        flash(f'Email sync failed: {ex}', 'danger')
    return redirect(request.referrer or url_for('enquiries_list'))


@app.route('/enquiries/<int:id>/send-email', methods=['POST'])
def enquiry_send_email(id):
    from email_sync import send_email, check_configured
    if not check_configured():
        flash('Email not configured — set MS variables in Railway.', 'warning')
        return redirect(url_for('enquiry_detail', id=id))
    e       = Enquiry.query.get_or_404(id)
    to_addr = request.form.get('to_address', '').strip()
    subject = request.form.get('subject', '').strip()
    body    = request.form.get('body', '').strip()
    author  = request.form.get('author', 'BC').strip() or 'BC'
    if not all([to_addr, subject, body]):
        flash('Please fill in all fields.', 'warning')
        return redirect(url_for('enquiry_detail', id=id))
    try:
        send_email(to_addr, subject, f"<p>{'<br>'.join(body.splitlines())}</p>")
        db.session.add(EnquiryNote(
            enquiry_id=id, direction='outbound',
            subject=subject, body=body, author=author,
        ))
        e.last_contact_date = date.today()
        nf = request.form.get('next_follow_up', '').strip()
        if nf:
            try: e.next_follow_up = datetime.strptime(nf, '%Y-%m-%d').date()
            except: pass
        db.session.commit()
        flash(f'Email sent to {to_addr}.', 'success')
    except Exception as ex:
        flash(f'Failed to send email: {ex}', 'danger')
    return redirect(url_for('enquiry_detail', id=id))


# ── Property-Contact Matching & Auto-linking ──────────────────────────────────

def auto_link_contact_to_projects(contact):
    """When a contact enquires, auto-register them on any matching active project."""
    if not contact.req_category:
        return []
    linked = []
    active_projects = Project.query.filter_by(status='Active').all()
    for project in active_projects:
        if not project.property:
            continue
        matches = match_contacts_to_property(project.property)
        for score, reasons, c in matches:
            if c.id == contact.id and score >= 3:
                # Check not already linked
                existing = ProjectApplicant.query.filter_by(
                    project_id=project.id, contact_id=contact.id).first()
                if not existing:
                    pa = ProjectApplicant(
                        project_id=project.id,
                        contact_id=contact.id,
                        status='Active Applicant',
                        match_score=score,
                        auto_linked=True,
                        notes=', '.join(reasons),
                    )
                    db.session.add(pa)
                    linked.append(project)
    if linked:
        db.session.commit()
    return linked


def match_contacts_to_property(prop):
    """Return [(score, contact)] sorted best-first for contacts with requirements."""
    if not prop:
        return []
    candidates = Contact.query.filter(Contact.req_category != None).all()
    results = []
    for c in candidates:
        score = 0
        reasons = []
        # Category
        if c.req_category and prop.website_category and c.req_category == prop.website_category:
            score += 3; reasons.append('Category match')
        # Use class
        if c.req_use_class and prop.use_class and c.req_use_class.lower() == prop.use_class.lower():
            score += 2; reasons.append('Use class match')
        # Property type
        if c.req_property_type and prop.property_type:
            if c.req_property_type.lower() in prop.property_type.lower() or prop.property_type.lower() in c.req_property_type.lower():
                score += 2; reasons.append('Type match')
        # Area
        if c.req_area and (prop.area or prop.postcode):
            for area in c.req_area.split(','):
                area = area.strip().lower()
                if area and (area in (prop.area or '').lower() or area in prop.postcode.lower()):
                    score += 2; reasons.append(f'Area match ({area.title()})'); break
        # Size
        if prop.size:
            if c.req_size_min and prop.size >= c.req_size_min:
                score += 1
            if c.req_size_max and prop.size <= c.req_size_max:
                score += 1; reasons.append('Size in range')
        # Budget
        if prop.listing_price and c.req_budget_max and prop.listing_price <= c.req_budget_max:
            score += 2; reasons.append('Within budget')
        if prop.listing_price and c.req_budget_min and prop.listing_price >= c.req_budget_min:
            score += 1
        if score > 0:
            results.append((score, reasons, c))
    return sorted(results, key=lambda x: x[0], reverse=True)


# ── Project Tasks ─────────────────────────────────────────────────────────────

@app.route('/projects/<int:id>/tasks/add', methods=['POST'])
def task_add(id):
    project = Project.query.get_or_404(id)
    title = request.form.get('title', '').strip()
    if title:
        due_raw = request.form.get('due_date', '')
        due = datetime.strptime(due_raw, '%Y-%m-%d').date() if due_raw else None
        t = ProjectTask(project_id=id, title=title, due_date=due,
                        # Falls back to whoever is signed in, so a task added
                        # without typing a name is still attributed in the
                        # Notes timeline. The Name field itself is unchanged.
                        created_by=(request.form.get('created_by', '').strip()
                                    or getattr(current_user, 'username', '') or 'Unknown'))
        db.session.add(t)
        db.session.commit()
    return redirect(url_for('project_detail', id=id))


@app.route('/tasks/<int:id>/toggle', methods=['POST'])
def task_toggle(id):
    task = ProjectTask.query.get_or_404(id)
    task.completed = not task.completed
    db.session.commit()
    return redirect(url_for('project_detail', id=task.project_id))


@app.route('/tasks/<int:id>/delete', methods=['POST'])
@requires('delete')
def task_delete(id):
    task = ProjectTask.query.get_or_404(id)
    project_id = task.project_id
    db.session.delete(task)
    db.session.commit()
    return redirect(url_for('project_detail', id=project_id))


# ── Project Notes ────────────────────────────────────────────────────────────

@app.route('/projects/<int:id>/services/add', methods=['POST'])
def service_add(id):
    project = Project.query.get_or_404(id)
    stype = request.form.get('service_type', '').strip()
    if stype:
        def pf(v): return float(v.replace(',','')) if v and v.strip() else None
        s = ProjectService(
            project_id=id,
            service_type=stype,
            status=request.form.get('status', 'Active'),
            fee_earner=request.form.get('fee_earner') or project.fee_earner,
            fee_percent=pf(request.form.get('fee_percent','')),
            fee_fixed=pf(request.form.get('fee_fixed','')),
            notes=request.form.get('notes') or None,
        )
        db.session.add(s)
        db.session.commit()
        flash(f'{stype} service added.', 'success')
    return redirect(url_for('project_detail', id=id))


@app.route('/services/<int:id>/edit', methods=['GET', 'POST'])
def service_edit(id):
    s = ProjectService.query.get_or_404(id)
    if request.method == 'POST':
        def pf(v): return float(v.replace(',','')) if v and v.strip() else None
        s.service_type = request.form.get('service_type', s.service_type)
        s.status       = request.form.get('status', 'Active')
        s.fee_earner   = request.form.get('fee_earner') or None
        s.fee_percent  = pf(request.form.get('fee_percent',''))
        s.fee_fixed    = pf(request.form.get('fee_fixed',''))
        s.notes        = request.form.get('notes') or None
        db.session.commit()
        flash('Service updated.', 'success')
        return redirect(url_for('project_detail', id=s.project_id))
    return render_template('projects/service_form.html', s=s)


@app.route('/projects/<int:proj_id>/applicants/register/<int:contact_id>', methods=['POST'])
def applicant_register(proj_id, contact_id):
    existing = ProjectApplicant.query.filter_by(project_id=proj_id, contact_id=contact_id).first()
    if not existing:
        pa = ProjectApplicant(project_id=proj_id, contact_id=contact_id,
                              status='Active Applicant', auto_linked=False)
        db.session.add(pa)
        db.session.commit()
        flash('Applicant registered on this project.', 'success')
    return redirect(url_for('project_detail', id=proj_id))


@app.route('/applicants/<int:id>/status', methods=['POST'])
def applicant_status(id):
    pa = ProjectApplicant.query.get_or_404(id)
    pa.status = request.form.get('status', pa.status)
    pa.notes  = request.form.get('notes', pa.notes)
    db.session.commit()
    return redirect(url_for('project_detail', id=pa.project_id))


@app.route('/applicants/<int:id>/remove', methods=['POST'])
def applicant_remove(id):
    pa = ProjectApplicant.query.get_or_404(id)
    project_id = pa.project_id
    db.session.delete(pa)
    db.session.commit()
    return redirect(url_for('project_detail', id=project_id))


@app.route('/services/<int:id>/delete', methods=['POST'])
def service_delete(id):
    s = ProjectService.query.get_or_404(id)
    project_id = s.project_id
    db.session.delete(s)
    db.session.commit()
    flash('Service removed.', 'info')
    return redirect(url_for('project_detail', id=project_id))


@app.route('/projects/<int:id>/notes/add', methods=['POST'])
def note_add(id):
    project = Project.query.get_or_404(id)
    content = request.form.get('content', '').strip()
    # Who is signed in, rather than asking them to type their own name.
    author = (request.form.get('author', '').strip()
              or getattr(current_user, 'username', '') or 'Unknown')
    if content:
        note = ProjectNote(project_id=id, content=content, author=author)
        db.session.add(note)
        db.session.commit()
        flash('Note added.', 'success')
    return redirect(url_for('project_detail', id=id) + '#tab-overview')


@app.route('/notes/<int:id>/delete', methods=['POST'])
@requires('delete')
def note_delete(id):
    note = ProjectNote.query.get_or_404(id)
    project_id = note.project_id
    db.session.delete(note)
    db.session.commit()
    flash('Note deleted.', 'info')
    return redirect(url_for('project_detail', id=project_id) + '#tab-overview')


# ── Project Photos ────────────────────────────────────────────────────────────

@app.route('/projects/<int:id>/photos/add', methods=['POST'])
def photo_add(id):
    project = Project.query.get_or_404(id)
    url     = request.form.get('url', '').strip()
    caption = request.form.get('caption', '').strip()
    if url:
        photo = ProjectPhoto(project_id=id, url=url, caption=caption or None)
        db.session.add(photo)
        db.session.commit()
        flash('Photo added.', 'success')
    return redirect(url_for('project_detail', id=id))


@app.route('/photos/<int:id>/delete', methods=['POST'])
def photo_delete(id):
    photo = ProjectPhoto.query.get_or_404(id)
    project_id = photo.project_id
    db.session.delete(photo)
    db.session.commit()
    flash('Photo removed.', 'info')
    return redirect(url_for('project_detail', id=project_id))


# ── Property detail API (used by transaction form JS) ────────────────────────

@app.route('/api/property/<int:id>/meta')
def api_property_meta(id):
    p = Property.query.get_or_404(id)
    return jsonify({
        'category':        p.website_category or '',
        'residential_use': p.residential_use or '',
        'property_type':   p.property_type or '',
        'size':            p.size or 0,
    })



# ── Listing Photo upload/delete ───────────────────────────────────────────────

def _listing_media_return(listing):
    """After a media change, go back to where it was being managed. Photos live
    on the Project Overview now, so prefer that; fall back to the standalone
    listing page for a listing with no project."""
    nxt = request.form.get('next') or ''
    if nxt.startswith('/') and not nxt.startswith('//'):
        return nxt
    if listing.project_id:
        return url_for('project_detail', id=listing.project_id)
    return url_for('listing_edit', id=listing.id) + '#media'



# ── Upload validation ────────────────────────────────────────────────────────
# Uploaded files are served back from this same origin, so what a file *claims*
# to be is never trusted: images are decoded before being accepted, PDFs are
# checked for the PDF magic bytes, and filenames are stripped of any path.

ALLOWED_IMAGE_MIMES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024


def _clean_filename(name, fallback):
    from werkzeug.utils import secure_filename
    cleaned = secure_filename(name or '')
    return cleaned[:180] or fallback


def _read_image_upload(f):
    """Return (data, mime, filename) for a genuine image, or (None, reason)."""
    import io
    data = f.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return None, 'is larger than 30 MB'
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.verify()                      # decodes headers; raises on anything else
        fmt = (im.format or '').lower()
    except Exception:
        return None, 'is not a readable image'
    mime = {'jpeg': 'image/jpeg', 'png': 'image/png',
            'webp': 'image/webp', 'gif': 'image/gif'}.get(fmt)
    if mime not in ALLOWED_IMAGE_MIMES:
        return None, 'is not a JPG, PNG, WEBP or GIF'
    return (data, mime, _clean_filename(f.filename, f'photo.{fmt}')), None


def _read_pdf_upload(f, fallback='document.pdf'):
    """Return (data, filename) for a genuine PDF, or (None, reason)."""
    data = f.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return None, 'is larger than 30 MB'
    if not data.startswith(b'%PDF-'):
        return None, 'is not a PDF'
    return (data, _clean_filename(f.filename, fallback)), None


@app.route('/listings/<int:id>/photos/upload', methods=['POST'])
def listing_photo_upload(id):
    listing = Listing.query.get_or_404(id)
    files = request.files.getlist('photos')
    count, rejected = 0, []
    for f in files:
        if not (f and f.filename):
            continue
        ok, why = _read_image_upload(f)
        if not ok:
            rejected.append(f'{f.filename} {why}')
            continue
        data, mime, name = ok
        ph = ListingPhoto(
            listing_id=id,
            file_data=data,
            filename=name,
            file_mime=mime,
            file_size=len(data),
            sort_order=len(listing.photos) + count,
        )
        db.session.add(ph)
        count += 1
    db.session.commit()
    if count:
        flash(f'{count} photo(s) uploaded.', 'success')
    for why in rejected:
        flash(f'Not uploaded — {why}.', 'warning')
    return redirect(_listing_media_return(listing))


@app.route('/listing-photos/<int:id>/delete', methods=['POST'])
@requires('delete')
def listing_photo_delete(id):
    ph = ListingPhoto.query.get_or_404(id)
    listing = ph.listing
    db.session.delete(ph)
    db.session.commit()
    return redirect(_listing_media_return(listing))


@app.route('/listings/<int:id>/photos/order', methods=['POST'])
def listing_photos_order(id):
    """Store the order the photos were dragged into.

    The first photo leads the listing everywhere it is marketed — the website,
    the Zoopla feed and the CRM gallery all read this same sort_order, so there
    is one ordering rather than one per channel.
    """
    listing = Listing.query.get_or_404(id)
    raw = (request.form.get('order') or '').strip()
    wanted = [int(x) for x in raw.split(',') if x.strip().isdigit()]
    by_id = {p.id: p for p in listing.photos}
    position = 0
    for pid in wanted:                       # dragged order first
        if pid in by_id:
            by_id.pop(pid).sort_order = position
            position += 1
    for leftover in by_id.values():          # anything not sent keeps its place at the end
        leftover.sort_order = position
        position += 1
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify({'ok': True, 'count': position})
    flash('Photo order saved.', 'success')
    return _back_to('project_detail', id=listing.project_id)


@app.route('/listing-photos/<int:id>/reorder', methods=['POST'])
def listing_photo_reorder(id):
    """Reorder a listing's photos. The first photo (lowest sort_order) is the
    cover shown on the website. action = 'cover' | 'left' | 'right'."""
    ph = ListingPhoto.query.get_or_404(id)
    listing = ph.listing
    action = request.form.get('action')
    photos = list(listing.photos)                 # already ordered by sort_order
    idx = next((i for i, p in enumerate(photos) if p.id == ph.id), None)
    if idx is not None:
        if action == 'cover':
            photos.insert(0, photos.pop(idx))
        elif action == 'left' and idx > 0:
            photos[idx - 1], photos[idx] = photos[idx], photos[idx - 1]
        elif action == 'right' and idx < len(photos) - 1:
            photos[idx + 1], photos[idx] = photos[idx], photos[idx + 1]
        for i, p in enumerate(photos):            # renumber so order is always clean
            p.sort_order = i
        db.session.commit()
    return redirect(_listing_media_return(listing))


# Small in-process cache of resized thumbnails, keyed by (photo_id, width).
# A photo blob for an id never changes, so entries never go stale. Cleared
# wholesale when it grows past the cap (simple + good enough for one worker).
_THUMB_CACHE = {}
_THUMB_CACHE_MAX = 300


@app.route('/listing-photos/<int:id>/image')
def listing_photo_image(id):
    from flask import send_file
    import io
    w = request.args.get('w', type=int)
    # Load only the columns we need; blobs are big so avoid selecting extras.
    ph = ListingPhoto.query.get_or_404(id)
    data = ph.file_data
    mime = ph.file_mime or 'image/jpeg'

    if w and 32 <= w <= 2000:
        key = (id, w)
        cached = _THUMB_CACHE.get(key)
        if cached is not None:
            mime, data = cached
        else:
            try:
                from PIL import Image
                im = Image.open(io.BytesIO(ph.file_data))
                if im.mode not in ('RGB', 'L'):
                    im = im.convert('RGB')
                im.thumbnail((w, w * 10), Image.LANCZOS)   # cap width, keep aspect
                buf = io.BytesIO()
                im.save(buf, format='JPEG', quality=78, optimize=True, progressive=True)
                data, mime = buf.getvalue(), 'image/jpeg'
                if len(_THUMB_CACHE) >= _THUMB_CACHE_MAX:
                    _THUMB_CACHE.clear()
                _THUMB_CACHE[key] = (mime, data)
            except Exception:
                data, mime = ph.file_data, (ph.file_mime or 'image/jpeg')

    resp = send_file(io.BytesIO(data), mimetype=mime)
    # A photo for a given id (and width) is immutable — let the browser and any
    # CDN cache it for a year so repeat visits are instant. Timing-Allow-Origin
    # lets the public site read image timing/size for its own diagnostics.
    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    resp.headers['Timing-Allow-Origin'] = '*'
    return resp


def _listing_media_back(id):
    """Return to wherever a brochure/floor-plan form was submitted from — the
    project's Website Listing tab or the standalone listing edit page."""
    nxt = request.form.get('next') or request.args.get('next')
    if nxt and nxt.startswith('/') and not nxt.startswith('//'):
        return redirect(nxt)
    return redirect(url_for('listing_edit', id=id) + '#media')


@app.route('/listings/<int:id>/brochure/upload', methods=['POST'])
def listing_brochure_upload(id):
    listing = Listing.query.get_or_404(id)
    f = request.files.get('brochure')
    if f and f.filename:
        ok, why = _read_pdf_upload(f, 'brochure.pdf')
        if not ok:
            flash(f'Brochure not uploaded — the file {why}.', 'warning')
            return _listing_media_back(id)
        listing.brochure_data, listing.brochure_filename = ok
        listing.brochure_size = len(listing.brochure_data)
        db.session.commit()
        flash('Brochure uploaded.', 'success')
    return _listing_media_back(id)


@app.route('/listings/<int:id>/brochure/delete', methods=['POST'])
@requires('delete')
def listing_brochure_delete(id):
    listing = Listing.query.get_or_404(id)
    listing.brochure_data = listing.brochure_filename = listing.brochure_size = None
    db.session.commit()
    flash('Brochure removed.', 'info')
    return _listing_media_back(id)


@app.route('/listings/<int:id>/brochure/download')
def listing_brochure_download(id):
    from flask import send_file
    import io
    listing = Listing.query.get_or_404(id)
    if not listing.brochure_data:
        flash('No brochure uploaded.', 'warning')
        return redirect(url_for('listing_edit', id=id))
    # ?inline=1 opens it in the browser (the View button); otherwise download,
    # the same as the EPC.
    inline = request.args.get('inline') == '1'
    return send_file(io.BytesIO(listing.brochure_data),
                     mimetype='application/pdf', as_attachment=not inline,
                     download_name=listing.brochure_filename or 'brochure.pdf')


# ── Publishing ───────────────────────────────────────────────────────────────
# The listing record is the single source of truth: publishing sets a flag on
# it rather than copying the property anywhere. The website reads /api/listings
# (website_listed) and the Zoopla feed reads zoopla_listed.

PUBLISH_TARGETS = {
    'website': ('website_listed', 'website_published_at', 'the website'),
    'zoopla':  ('zoopla_listed',  'zoopla_published_at',  'the Zoopla feed'),
}


def listing_publish_state(listing):
    """What is live, and whether it matches what is on screen."""
    state = {}
    for target, (flag, stamp, _label) in PUBLISH_TARGETS.items():
        live = bool(getattr(listing, flag, False))
        published_at = getattr(listing, stamp, None)
        changed = bool(live and published_at and listing.updated_at
                       and listing.updated_at > published_at)
        state[target] = {'live': live, 'at': published_at, 'stale': changed}
    return state


@app.route('/listings/<int:id>/publish', methods=['POST'])
@requires('publish')
@requires('publish')
def listing_publish(id):
    listing = Listing.query.get_or_404(id)
    target = (request.form.get('target') or request.args.get('target') or '').lower()
    live = (request.form.get('live') or request.args.get('live')) == '1'
    if target not in PUBLISH_TARGETS:
        flash('Unknown publishing target.', 'warning')
        return _back_to('project_detail', id=listing.project_id)
    flag, stamp, label = PUBLISH_TARGETS[target]
    setattr(listing, flag, live)
    setattr(listing, stamp, datetime.utcnow() if live else None)
    db.session.commit()
    audit('publish' if live else 'unpublish', entity='Listing', entity_id=listing.id, detail=target)
    flash(f"{'Published to' if live else 'Removed from'} {label}.", 'success')
    return _back_to('project_detail', id=listing.project_id)


@app.route('/listings/<int:id>/epc/upload', methods=['POST'])
def listing_epc_upload(id):
    listing = Listing.query.get_or_404(id)
    f = request.files.get('epc')
    if f and f.filename:
        # Uploading again simply replaces what is there — same as the brochure.
        ok, why = _read_pdf_upload(f, 'epc.pdf')
        if not ok:
            flash(f'EPC not uploaded — the file {why}.', 'warning')
            return _listing_media_back(id)
        listing.epc_data, listing.epc_filename = ok
        listing.epc_size = len(listing.epc_data)
        db.session.commit()
        flash('EPC uploaded.', 'success')
    return _listing_media_back(id)


@app.route('/listings/<int:id>/epc/delete', methods=['POST'])
@requires('delete')
def listing_epc_delete(id):
    listing = Listing.query.get_or_404(id)
    listing.epc_data = listing.epc_filename = listing.epc_size = None
    db.session.commit()
    flash('EPC removed.', 'info')
    return _listing_media_back(id)


@app.route('/listings/<int:id>/epc/download')
def listing_epc_download(id):
    from flask import send_file
    import io
    listing = Listing.query.get_or_404(id)
    if not listing.epc_data:
        flash('No EPC uploaded.', 'warning')
        return redirect(url_for('listing_edit', id=id))
    # ?inline=1 opens it in the browser (the View button); otherwise download.
    inline = request.args.get('inline') == '1'
    return send_file(io.BytesIO(listing.epc_data),
                     mimetype='application/pdf', as_attachment=not inline,
                     download_name=listing.epc_filename or 'epc.pdf')


@app.route('/listings/<int:id>/floorplan/upload', methods=['POST'])
def listing_floorplan_upload(id):
    listing = Listing.query.get_or_404(id)
    f = request.files.get('floor_plan')
    if f and f.filename:
        # A floor plan may be a PDF or an image.
        ok, why = _read_pdf_upload(f, 'floorplan.pdf')
        if not ok:
            f.stream.seek(0)
            img_ok, img_why = _read_image_upload(f)
            if not img_ok:
                flash(f'Floor plan not uploaded — the file {why} and {img_why}.', 'warning')
                return _listing_media_back(id)
            data, _mime, name = img_ok
            ok = (data, name)
        listing.floor_plan_data, listing.floor_plan_filename = ok
        listing.floor_plan_size = len(listing.floor_plan_data)
        db.session.commit()
        flash('Floor plan uploaded.', 'success')
    return _listing_media_back(id)


@app.route('/listings/<int:id>/floorplan/delete', methods=['POST'])
@requires('delete')
def listing_floorplan_delete(id):
    listing = Listing.query.get_or_404(id)
    listing.floor_plan_data = listing.floor_plan_filename = listing.floor_plan_size = None
    db.session.commit()
    return _listing_media_back(id)


@app.route('/listings/<int:id>/floorplan/download')
def listing_floorplan_download(id):
    from flask import send_file
    import io, mimetypes
    listing = Listing.query.get_or_404(id)
    if not listing.floor_plan_data:
        flash('No floor plan uploaded.', 'warning')
        return redirect(url_for('listing_edit', id=id))
    name = listing.floor_plan_filename or 'floor-plan.pdf'
    mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    inline = request.args.get('inline') == '1'
    return send_file(io.BytesIO(listing.floor_plan_data),
                     mimetype=mime, as_attachment=not inline, download_name=name)

# ── Enquiry pipeline ─────────────────────────────────────────────────────────

def _set_enquiry_stage(enq, stage, author=None, note=None, occurred_on=None):
    """Move an enquiry to a stage and record the step."""
    if stage not in ENQUIRY_ALL_STAGES or stage == enq.stage:
        return False
    event = EnquiryStageEvent(
        enquiry_id=enq.id, stage=stage, from_stage=enq.stage,
        occurred_on=occurred_on or date.today(),
        author=(author or '').strip() or None,
        note=(note or '').strip() or None,
    )
    enq.stage = stage
    enq.stage_changed_on = event.occurred_on
    # Reaching the end, or falling out of the pipeline, closes the enquiry.
    if stage == 'Heads of Terms Signed':
        enq.status = 'Won'
    elif stage in ENQUIRY_STAGES_CLOSED:
        enq.status = 'Lost'
    elif enq.status in ('Won', 'Lost'):
        enq.status = 'Open'
    db.session.add(event)
    return True


@app.route('/enquiries/<int:id>/stage', methods=['POST'])
def enquiry_set_stage(id):
    enq = Enquiry.query.get_or_404(id)
    stage = (request.form.get('stage') or '').strip()
    if stage == '__next__':
        stage = next_enquiry_stage(enq.stage) or ''
    occurred = None
    raw = (request.form.get('occurred_on') or '').strip()
    if raw:
        try:
            occurred = datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            occurred = None
    if _set_enquiry_stage(enq, stage, request.form.get('author'),
                          request.form.get('note'), occurred):
        enq.last_contact_date = enq.stage_changed_on
        db.session.commit()
        flash(f'Enquiry moved to “{stage}”.', 'success')
    else:
        flash('No change made to the enquiry stage.', 'info')
    return redirect(request.referrer or url_for('enquiry_detail', id=id))


# ── Enquiry schedule (client report) ─────────────────────────────────────────

def _half_month_period(today=None):
    """The current reporting fortnight: the 1st–15th, or the 16th–month end.

    Schedules go out twice a month, so this is the period a schedule generated
    today would normally cover.
    """
    import calendar
    today = today or date.today()
    if today.day <= 15:
        return date(today.year, today.month, 1), date(today.year, today.month, 15)
    last = calendar.monthrange(today.year, today.month)[1]
    return date(today.year, today.month, 16), date(today.year, today.month, last)


def _previous_half_month(today=None):
    start, _ = _half_month_period(today)
    end = start - timedelta(days=1)
    return _half_month_period(end)


def _enquiry_date(e):
    """The date an enquiry came in — received_date if set, else when recorded."""
    return e.received_date or (e.created_at.date() if e.created_at else None)


def _project_enquiries(project):
    """Every enquiry for an instruction, whether linked by project or property.

    Website and portal enquiries can land either way, so both are collected and
    deduplicated — the same rule the project page uses on screen.
    """
    seen, items = set(), []
    for e in (project.enquiries or []):
        if e.id not in seen:
            seen.add(e.id); items.append(e)
    if project.property:
        for e in (project.property.enquiries or []):
            if e.id not in seen:
                seen.add(e.id); items.append(e)
    return items


@app.route('/projects/<int:id>/enquiry-schedule')
@requires('export')
@requires('export')
def project_enquiry_schedule(id):
    """A client-facing schedule of enquiries received on one instruction."""
    project = Project.query.get_or_404(id)

    def parse(param):
        raw = (request.args.get(param) or '').strip()
        try:
            return datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            return None

    preset = request.args.get('preset', '')
    today = date.today()
    if preset == 'previous':
        start, end = _previous_half_month()
    elif preset == 'month':
        start, end = date(today.year, today.month, 1), today
    elif preset == 'two-months':
        start, end = today - timedelta(days=61), today
    elif preset == 'six-months':
        start, end = today - timedelta(days=182), today
    elif preset == 'all':
        start, end = None, None
    else:
        start, end = parse('from'), parse('to')
        if not start and not end and preset != 'custom':
            start, end = _half_month_period()      # default: this fortnight

    rows = []
    for e in _project_enquiries(project):
        on = _enquiry_date(e)
        if start and (not on or on < start):
            continue
        if end and (not on or on > end):
            continue
        rows.append({'enquiry': e, 'on': on})
    rows.sort(key=lambda r: (r['on'] or date.min), reverse=True)

    by_source, by_stage = {}, {}
    for r in rows:
        src = r['enquiry'].source or 'Direct'
        stg = r['enquiry'].stage or 'Enquiry Received'
        by_source[src] = by_source.get(src, 0) + 1
        by_stage[stg] = by_stage.get(stg, 0) + 1

    return render_template(
        'reports/enquiry_schedule.html',
        project=project, rows=rows, start=start, end=end, preset=preset,
        by_source=sorted(by_source.items(), key=lambda kv: -kv[1]),
        by_stage=sorted(by_stage.items(), key=lambda kv: -kv[1]),
        generated_on=today,
        total_all_time=len(_project_enquiries(project)),
    )


# ── Website → DB: inbound enquiry webhook ────────────────────────────────────

# Submissions per address per hour on the public enquiry endpoint.
_ENQUIRY_HITS = {}
ENQUIRY_RATE_LIMIT = 12

def match_property_from_text(ref):
    """Find the Property a free-text reference points at.

    Used by the website enquiry form ("Title — Address, POSTCODE") and by portal
    lead emails, whose property line is similar but not identical. Postcode is
    tried first because it is the only part that is reliably exact.
    """
    ref = (ref or '').strip()
    if not ref:
        return None
    pc = re.search(r'[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}', ref.upper())
    if pc:
        prop = Property.query.filter(Property.postcode.ilike(pc.group(0))).first()
        if prop:
            return prop
    tail = ref.split('—')[-1].strip()
    if tail:
        prop = Property.query.filter(Property.address.ilike(f'%{tail[:40]}%')).first()
        if prop:
            return prop
    return Property.query.filter(Property.address.ilike(f'%{ref[:40]}%')).first()


@app.route('/api/enquiry', methods=['POST', 'OPTIONS'])
def api_enquiry():
    if request.method == 'OPTIONS':
        return '', 204

    data = request.get_json(force=True, silent=True) or request.form.to_dict()

    # This endpoint is open to the internet so the website can post to it.
    # Cap how often one address may submit, so nobody can flood the CRM with
    # junk contacts and enquiries.
    key = _login_key()
    now = datetime.utcnow()
    recent = [t for t in _ENQUIRY_HITS.get(key, []) if now - t < timedelta(hours=1)]
    if len(recent) >= ENQUIRY_RATE_LIMIT:
        return jsonify({'ok': False, 'error': 'Too many enquiries — please call us on 020 7349 6666.'}), 429
    recent.append(now)
    _ENQUIRY_HITS[key] = recent
    if len(_ENQUIRY_HITS) > 5000:                     # keep the dict bounded
        for k in [k for k, v in _ENQUIRY_HITS.items() if not v or now - v[-1] > timedelta(hours=2)]:
            _ENQUIRY_HITS.pop(k, None)

    # Honeypot: a field real people never see and never fill in.
    if (data.get('company_website') or '').strip():
        return jsonify({'ok': True}), 200

    raw_name     = (data.get('from_name')   or '').strip()
    email        = (data.get('from_email')  or '').strip() or None
    phone        = (data.get('phone')       or '').strip() or None
    interest     = (data.get('interest')    or 'General Enquiry').strip()
    message      = (data.get('message')     or '').strip()
    property_ref = (data.get('property')    or '').strip()
    transaction  = (data.get('transaction') or '').strip().lower()  # 'sale' or 'let'
    category     = (data.get('category')    or '').strip().lower()  # 'commercial' or 'residential'

    # Determine the right contact type
    # "Arrange a viewing" from property listing → use transaction type
    # General enquiry from contact page → use interest field
    if transaction == 'sale':
        contact_type = 'Prospective Buyer'
    elif transaction == 'let':
        contact_type = 'Prospective Tenant'
    elif interest == 'Commercial Agency':
        contact_type = 'Prospective Tenant'
    elif interest == 'Residential Agency':
        contact_type = 'Prospect'  # sale vs let unknown without listing context
    elif interest == 'Management':
        contact_type = 'Client'
    else:
        contact_type = 'Prospect'

    # Split full name
    parts      = raw_name.split(' ', 1)
    first_name = parts[0] or 'Unknown'
    last_name  = parts[1] if len(parts) > 1 else '.'

    # Find existing contact by email, or create new one
    contact = None
    if email:
        contact = Contact.query.filter_by(email=email).first()
    if not contact and raw_name:
        contact = Contact(
            first_name=first_name, last_name=last_name,
            email=email, phone=phone,
            contact_type=contact_type,
        )
        db.session.add(contact)
        db.session.flush()
    elif contact:
        # Update phone if missing; upgrade type if it's still generic
        if phone and not contact.phone:
            contact.phone = phone
        if contact.contact_type in (None, 'Enquiry', 'Prospect', 'Other') and contact_type not in (None, 'Prospect'):
            contact.contact_type = contact_type

    # Try to match a property from the property reference passed in the form.
    # The website sends "Title — Address, POSTCODE", so match on the postcode
    # first (most reliable), then the address portion after the dash, then a
    # final loose fallback on the whole string.
    prop = match_property_from_text(property_ref) if property_ref else None

    # Find the active project for that property (if any)
    proj = None
    if prop:
        proj = Project.query.filter_by(
            property_id=prop.id, status='Active'
        ).first()

    # Map interest → enquiry type label
    etype_map = {
        'Commercial Agency':  'Agency — Letting',
        'Residential Agency': 'Agency — Sale',
        'Management':         'Other',
        'General Enquiry':    'Other',
        'Arrange a viewing':  'Agency — Letting' if transaction == 'let' else 'Agency — Sale',
    }
    subject = f"Website — {interest}"
    if property_ref:
        subject = f"Viewing request — {property_ref[:80]}"

    # Portals and website forms re-post the same lead when someone double-clicks
    # or a sync runs twice. The same person, about the same property, on the
    # same day is treated as the enquiry already on file.
    if contact:
        already = (Enquiry.query
                   .filter_by(contact_id=contact.id,
                              property_id=prop.id if prop else None,
                              received_date=date.today())
                   .first())
        if already:
            db.session.commit()          # keep any contact details picked up above
            return jsonify({'ok': True, 'enquiry_id': already.id, 'duplicate': True}), 200

    enq = Enquiry(
        subject=subject,
        enquiry_type=etype_map.get(interest, 'Other'),
        status='Open',
        source='Website',
        contact_id=contact.id if contact else None,
        property_id=prop.id if prop else None,
        project_id=proj.id if proj else None,
        notes=message or None,
        received_date=date.today(),
    )
    db.session.add(enq)
    db.session.commit()

    # Auto-link contact to matching active projects
    linked_projects = []
    if contact:
        linked_projects = auto_link_contact_to_projects(contact)
        # Also directly link to the specific project if known
        if proj and contact:
            existing = ProjectApplicant.query.filter_by(
                project_id=proj.id, contact_id=contact.id).first()
            if not existing:
                pa = ProjectApplicant(
                    project_id=proj.id, contact_id=contact.id,
                    status='Active Applicant', auto_linked=True,
                    notes=f'Enquired via website: {subject}',
                )
                db.session.add(pa)
                db.session.commit()

    return jsonify({
        'ok': True,
        'enquiry_id': enq.id,
        'contact_id': contact.id if contact else None,
        'contact_type': contact_type,
        'auto_linked_projects': len(linked_projects),
    })


# ── Listing CRUD (managed from Project page) ─────────────────────────────────

def _save_listing_from_form(form, l):
    def pf(v): return float(v.replace(',','')) if v and v.strip() else None
    def pi(v): return int(v) if v and str(v).strip() else None
    # Only overwrite a field the form actually submitted. The big create form
    # disables the inactive category's inputs on submit, so a save there used to
    # blank out every field it wasn't showing — this is why listings "lost" info.
    # `website_listed`/`featured` stay unconditional so they can still be unticked.
    def setf(attr, key, conv=None):
        if key in form:
            v = form.get(key)
            setattr(l, attr, conv(v) if conv else (v or None))
    def clean_area(v):
        v = (v or '').strip()
        if not v:
            return None
        prop_for_area = Property.query.get(l.property_id) if l.property_id else None
        pc = (prop_for_area.postcode if prop_for_area else '').strip()
        if pc:
            v = re.sub(r'\s*,?\s*' + re.escape(pc) + r'\s*$', '', v, flags=re.IGNORECASE).strip()
            if v.lower() == pc.lower():
                v = ''
        return v or None
    setf('unit_name', 'unit_name', lambda v: v.strip() or None)
    l.website_listed = bool(form.get('website_listed'))
    if 'listing_status' in form:
        l.listing_status = form.get('listing_status') or 'available'
    l.featured = bool(form.get('featured'))
    l.zoopla_listed = bool(form.get('zoopla_listed'))
    if 'website_category' in form:
        l.website_category = form.get('website_category') or None
    setf('use_class', 'use_class')
    setf('area', 'area', clean_area)
    setf('listing_price', 'listing_price', pf)
    if 'listing_price_unit' in form:
        l.listing_price_unit = form.get('listing_price_unit') or 'poa'
    setf('price_display', 'price_display')
    setf('size', 'size', pf)
    setf('min_size', 'min_size', pf)   # "Size from" — quoted range low end
    setf('max_size', 'max_size', pf)   # "Size to"   — quoted range high end
    # Residential measurement is always GIA; commercial picks the basis.
    if l.website_category == 'residential':
        l.measurement_type = 'GIA'
    else:
        setf('measurement_type', 'measurement_type')
    setf('beds', 'beds', pi)
    setf('baths', 'baths', pi)
    setf('lat', 'lat', pf)
    setf('lng', 'lng', pf)
    setf('photo_id', 'photo_id')
    setf('blurb', 'blurb')            # Description (shown on website)
    # ── Website listing criteria (the four listing types) ──
    setf('residential_use', 'residential_use')
    setf('key_terms', 'key_terms')
    setf('location_description', 'location_description')
    setf('initial_yield', 'initial_yield', pf)
    setf('investment_vacant', 'investment_vacant')
    setf('tenure', 'tenure')
    setf('lease_years_remaining', 'lease_years_remaining', pi)
    # Sale vs let. The inline form sends an explicit `transaction` toggle (let/sale)
    # and sets listing_price_unit to match. The big form has no toggle — derive it
    # from the price basis and reconcile its separate sale_price input.
    transaction = form.get('transaction')
    if transaction in ('let', 'sale'):
        l.set_as_for_sale = (transaction == 'sale')
        l.set_as_to_let   = (transaction == 'let')
    else:
        l.set_as_for_sale = (l.listing_price_unit == 'sale')
        l.set_as_to_let   = (l.listing_price_unit in ('pa', 'pcm'))
        if l.website_category == 'commercial':
            setf('sale_price', 'sale_price', pf)
            l.set_as_for_sale = bool(form.get('set_as_for_sale'))
            l.set_as_to_let   = bool(form.get('set_as_to_let'))
            if l.set_as_for_sale and l.sale_price:
                l.listing_price      = l.sale_price
                l.listing_price_unit = 'sale'
                l.price_display      = form.get('sale_price_display') or l.price_display
            elif l.listing_price:
                l.listing_price_unit = 'pa'    # commercial rent quoted per annum
            else:
                l.listing_price_unit = 'poa'
    # ── Commercial lease detail (INTERNAL — for records/brochure, NOT on /api/listings) ──
    setf('lease_type', 'lease_type')
    setf('rent_qualifier', 'rent_qualifier')
    setf('rent_inclusive', 'rent_inclusive')
    setf('rent_from', 'rent_from', pf)
    setf('rent_to', 'rent_to', pf)
    setf('rent_comment', 'rent_comment')
    setf('lease_length_years', 'lease_length_years', pi)
    setf('lease_length_months', 'lease_length_months', pi)
    setf('inside_1954_act', 'inside_1954_act')
    setf('repair_insuring', 'repair_insuring')
    setf('service_charge', 'service_charge', pf)
    l.service_charge_na   = bool(form.get('service_charge_na'))
    setf('rateable_value', 'rateable_value', pf)
    l.rateable_value_na   = bool(form.get('rateable_value_na'))
    if form.get('epc_band'):
        l.epc_band = form.get('epc_band')
    setf('parking_ratio', 'parking_ratio')
    setf('parking_rent', 'parking_rent', pf)
    setf('parking_spaces', 'parking_spaces', pi)


@app.route('/projects/<int:proj_id>/listing/new', methods=['GET', 'POST'])
def listing_new_for_project(proj_id):
    project = Project.query.get_or_404(proj_id)
    prop = project.property
    if request.method == 'POST':
        l = Listing(project_id=proj_id, property_id=prop.id if prop else None)
        _save_listing_from_form(request.form, l)
        db.session.add(l)
        db.session.commit()
        flash('Website listing created.', 'success')
        return redirect(url_for('project_detail', id=proj_id) + '#tab-listing')
    # GET: never create a second listing for a project — reopening the form on a
    # project that already has one just returns to it (this is how duplicate
    # website listings were being created).
    existing = Listing.query.filter_by(project_id=proj_id).first()
    if existing:
        flash('This project already has a website listing — editing it below.', 'info')
        return redirect(url_for('project_detail', id=proj_id) + '#tab-listing')
    # Create a blank listing immediately and drop the user on the inline form
    l = Listing(
        project_id=proj_id,
        property_id=prop.id if prop else None,
        website_category='commercial',
        listing_status='available',
        set_as_to_let=True,
    )
    db.session.add(l)
    db.session.commit()
    flash('Website listing created — fill in the details below.', 'success')
    return redirect(url_for('project_detail', id=proj_id) + '#tab-listing')


@app.route('/listings/<int:id>/edit', methods=['GET', 'POST'])
def listing_edit(id):
    l = Listing.query.get_or_404(id)
    project = Project.query.get(l.project_id) if l.project_id else None
    prop = l.prop
    if request.method == 'POST':
        _save_listing_from_form(request.form, l)
        db.session.commit()
        flash('Listing updated.', 'success')
        if project:
            return redirect(url_for('project_detail', id=project.id) + '#tab-listing')
        return redirect(url_for('property_detail', id=prop.id) if prop else url_for('projects_list'))
    # GET: redirect to the inline form on the project detail page
    if project:
        return redirect(url_for('project_detail', id=project.id) + '#tab-listing')
    return redirect(url_for('property_detail', id=prop.id) if prop else url_for('projects_list'))


@app.route('/listings/<int:id>/delete', methods=['POST'])
@requires('delete')
def listing_delete(id):
    l = Listing.query.get_or_404(id)
    proj_id = l.project_id
    prop_id = l.property_id
    db.session.delete(l)
    db.session.commit()
    flash('Listing removed.', 'info')
    if proj_id:
        return redirect(url_for('project_detail', id=proj_id))
    return redirect(url_for('property_detail', id=prop_id) if prop_id else url_for('projects_list'))


# Legacy route kept for backward compatibility
@app.route('/properties/<int:prop_id>/listings/new', methods=['GET', 'POST'])
def listing_new(prop_id):
    prop = Property.query.get_or_404(prop_id)
    if request.method == 'POST':
        l = Listing(property_id=prop_id)
        _save_listing_from_form(request.form, l)
        db.session.add(l)
        db.session.commit()
        flash('Listing added.', 'success')
        return redirect(url_for('property_detail', id=prop_id))
    return render_template('projects/listing_form.html', project=None, prop=prop, listing=None)


# ── Public API (consumed by website) ─────────────────────────────────────────

@app.route('/health')
def health():
    return 'ok', 200


@app.route('/api/listings')
def api_listings():
    # The website is driven SOLELY by project Website Listings. A property
    # appears only when its project's listing has "Show on website" on. A
    # project/instruction with no website-listed listing is NOT shown, and
    # removing/untoggling the listing removes it from the website. No fallback
    # to the legacy Property.website_listed flag.
    result = []
    # Perf: eager-load each listing's property and photos in bulk (kills the
    # N+1), and DEFER the heavy binary columns. The API only needs photo *ids*
    # to build URLs and *sizes* to know a brochure/floor-plan exists — it must
    # never pull the 130 image blobs or the PDF blobs out of Postgres, which is
    # what made this endpoint take 4-6s.
    from sqlalchemy.orm import joinedload, selectinload, load_only, defer
    listings = (Listing.query.filter_by(website_listed=True)
                .options(
                    joinedload(Listing.prop),
                    selectinload(Listing.photos).load_only(
                        ListingPhoto.id, ListingPhoto.listing_id, ListingPhoto.sort_order),
                    defer(Listing.brochure_data),
                    defer(Listing.floor_plan_data),
                )
                .all())
    if listings:
        for l in listings:
            p = l.prop
            if p is None:
                continue
            # Big text = the address. Only prepend the unit name when the address
            # doesn't already start with it (avoids "Unit 2, Unit 2, Marlin House").
            title = _normalise_address(p.address, l.unit_name)
            price = l.listing_price or 0
            unit  = l.listing_price_unit or 'poa'
            # Gallery: absolute URLs to each uploaded photo, served from this
            # app. Force https so images load on the https website (GitHub
            # Pages) without mixed-content blocking. Ordered by sort_order.
            base = request.host_url.replace('http://', 'https://').rstrip('/')
            # Request a card-sized thumbnail (?w=) for the first image the site
            # uses as the cover; the gallery can still request larger widths.
            photos = [base + url_for('listing_photo_image', id=ph.id) for ph in l.photos]
            result.append({
                'id':            f'cr-lst-{l.id}',
                'featured':      bool(l.featured),
                'category':      l.website_category or 'commercial',
                'status':        'sale' if unit == 'sale' else 'let',
                'listingStatus': l.listing_status or 'available',
                'type':          p.property_type or 'Property',
                'use':           l.use_class or 'office',
                'title':         title,
                'area':          l.area or p.postcode,
                'postcode':      p.postcode,
                'address':       p.address,
                'price':         price,
                'priceUnit':     unit,
                'priceDisplay':  l.price_display or None,
                'sqft':          int(l.size or p.size or 0),
                'sizeFrom':      int(l.min_size) if l.min_size else None,
                'sizeTo':        int(l.max_size) if l.max_size else None,
                'lat':           l.lat or p.lat,
                'lng':           l.lng or p.lng,
                'added':         l.created_at.strftime('%Y-%m-%d'),
                # Deliberately null when nothing has been uploaded. This used to
                # fall back to a stock Unsplash id, so a listing with no
                # photographs showed a stranger's building on the website.
                'photo':         None,
                'photos':        photos,
                'blurb':         l.blurb or p.blurb or p.description or '',
                'beds':          l.beds or p.beds,
                'baths':         l.baths or p.baths,
                'measurement':   l.measurement_type or ('GIA' if l.website_category == 'residential' else None),
                'keyTerms':      l.key_terms or None,
                'locationText':  l.location_description or None,
                'yield':         l.initial_yield or None,
                'tenure':        l.investment_vacant or l.residential_use or None,
                'saleTenure':    l.tenure or None,
                'leaseYears':    l.lease_years_remaining or None,
                'vacantPossession': l.investment_vacant == 'Vacant Possession',
                'brochureUrl':   (base + url_for('listing_brochure_download', id=l.id)) if l.brochure_size else None,
                'floorPlanUrl':  (base + url_for('listing_floorplan_download', id=l.id)) if l.floor_plan_size else None,
                'pricePerSqft':  round((l.listing_price or 0) / int(l.size or p.size), 2)
                                 if (unit == 'pa' and int(l.size or p.size or 0) > 0) else None,
            })
    resp = jsonify(result)
    # Always serve fresh data so admin changes (remove/toggle) show on the
    # website without waiting on a stale browser/CDN cache.
    resp.headers['Cache-Control'] = 'no-store, max-age=0'
    return resp


@app.route('/admin/zoopla', methods=['GET'])
@requires('publish')
def admin_zoopla():
    """Zoopla feed dashboard: shows which listings will be sent, a preview of
    the BLM file, feed configuration status, and a Push button. Login-gated via
    the global before_request guard (not in _PUBLIC_ENDPOINTS)."""
    import zoopla_feed as zf
    cfg = zf.feed_config()
    # Send every Zoopla-toggled listing as live, plus any that are on the
    # website but toggled OFF so Zoopla takes them down (PUBLISHED_FLAG=0).
    live = Listing.query.filter_by(zoopla_listed=True).order_by(Listing.id).all()
    takedown = (Listing.query.filter_by(zoopla_listed=False, website_listed=True)
                             .order_by(Listing.id).all())
    to_send = live + takedown
    blm_text, media_files = zf.generate_feed(to_send, cfg['branch_id'])

    def _t(l):
        try:
            return l.display_title
        except Exception:
            return f'Listing #{l.id}'

    live_rows = ''.join(
        f'<tr><td>CR-{l.id}</td><td>{_t(l)}</td>'
        f'<td>{l.website_category or "-"}</td>'
        f'<td>{l.listing_status or "available"}</td>'
        f'<td>{len(list(l.photos or []))}</td></tr>' for l in live) \
        or '<tr><td colspan=5 style="color:#6b7280">No listings toggled for Zoopla yet.</td></tr>'
    takedown_note = (f'<p style="color:#6b7280;font-size:13px">Plus '
                     f'<b>{len(takedown)}</b> website listing(s) not on Zoopla — '
                     f'sent with PUBLISHED_FLAG=0 so Zoopla removes them.</p>'
                     if takedown else '')

    if cfg['ready']:
        status_html = (f'<div style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;'
                       f'padding:12px 16px;margin:14px 0"><b style="color:#1b7a3f">Feed configured</b> — '
                       f'{cfg["host"]}, branch {cfg["branch_id"]}, file {cfg["filename"]}.</div>')
        push_btn = ('<form method="post" action="/admin/zoopla/push" style="margin-top:8px">'
                    '<button style="background:#0e1f44;color:#fff;padding:11px 20px;border:0;'
                    'border-radius:6px;font-size:15px;cursor:pointer">Push feed to Zoopla now</button></form>')
    else:
        status_html = (f'<div style="background:#fef9c3;border:1px solid #fde047;border-radius:8px;'
                       f'padding:12px 16px;margin:14px 0"><b>Feed not connected yet.</b> Ask your Zoopla '
                       f'account manager to enable a custom data feed for your branch, then set these '
                       f'Railway env vars: <code>ZOOPLA_FTP_HOST</code>, <code>ZOOPLA_FTP_USER</code>, '
                       f'<code>ZOOPLA_FTP_PASS</code>, <code>ZOOPLA_BRANCH_ID</code>. Preview below still works.</div>')
        push_btn = ('<button disabled style="background:#9ca3af;color:#fff;padding:11px 20px;border:0;'
                    'border-radius:6px;font-size:15px;margin-top:8px">Push (set credentials first)</button>')

    import html as _html
    preview = _html.escape(blm_text)
    return f'''<!doctype html><meta charset=utf-8>
<body style="font-family:system-ui,Arial;max-width:920px;margin:40px auto;padding:0 20px;color:#111">
<h2 style="color:#0e1f44">Zoopla feed</h2>
{status_html}
<h3 style="margin-bottom:4px">Listings going to Zoopla ({len(live)})</h3>
{takedown_note}
<table style="width:100%;border-collapse:collapse;font-size:14px">
<thead><tr style="text-align:left;border-bottom:2px solid #0e1f44">
<th>Ref</th><th>Listing</th><th>Category</th><th>Status</th><th>Photos</th></tr></thead>
<tbody>{live_rows}</tbody></table>
{push_btn}
<h3 style="margin-top:28px">BLM file preview</h3>
<p style="color:#6b7280;font-size:13px">This is exactly what would be uploaded ({len(media_files)} media file(s) ship alongside it).</p>
<pre style="background:#0e1f44;color:#dbe4ff;padding:16px;border-radius:8px;overflow:auto;font-size:12px;max-height:420px">{preview}</pre>
<p><a href="{url_for('projects_list')}" style="color:#0e1f44">← Back to projects</a></p>
</body>'''


@app.route('/admin/zoopla/push', methods=['POST'])
@requires('export')
def admin_zoopla_push():
    """Generate the BLM feed and upload it (with images) to Zoopla over SFTP."""
    import zoopla_feed as zf
    cfg = zf.feed_config()
    live = Listing.query.filter_by(zoopla_listed=True).order_by(Listing.id).all()
    takedown = (Listing.query.filter_by(zoopla_listed=False, website_listed=True)
                             .order_by(Listing.id).all())
    blm_text, media_files = zf.generate_feed(live + takedown, cfg['branch_id'])
    ok, msg = zf.upload_feed(blm_text, media_files, cfg)
    colour = '#1b7a3f' if ok else '#b91c1c'
    heading = 'Feed pushed to Zoopla' if ok else 'Push failed'
    return f'''<!doctype html><meta charset=utf-8>
<body style="font-family:system-ui,Arial;max-width:640px;margin:60px auto;padding:0 20px;color:#111">
<h2 style="color:{colour}">{heading}</h2>
<p>{msg}</p>
<p style="color:#6b7280;font-size:13px">Sent {len(live)} live listing(s). Zoopla ingests the feed on its own schedule, so changes appear on the portal after their next pickup — not instantly.</p>
<p><a href="{url_for('admin_zoopla')}" style="display:inline-block;margin-top:10px;background:#0e1f44;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none">← Back to Zoopla feed</a></p>
</body>'''


def _migrate_security_columns():
    """Add the role, MFA and audit columns. Idempotent."""
    from sqlalchemy import text, inspect
    with app.app_context():
        insp = inspect(db.engine)
        existing = {c['name'] for c in insp.get_columns('users')}
        cols = [('role', f"TEXT DEFAULT '{DEFAULT_ROLE}'"), ('totp_secret', 'TEXT'),
                ('mfa_enabled', 'BOOLEAN DEFAULT FALSE'), ('last_login_at', 'TIMESTAMP')]
        with db.engine.connect() as conn:
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE users ADD COLUMN {name} {ddl}'))
            conn.commit()


def _migrate_email_columns():
    from sqlalchemy import text, inspect
    with app.app_context():
        insp = inspect(db.engine)
        existing = {col['name'] for col in insp.get_columns('enquiry_notes')}
        if 'ms_message_id' not in existing:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE enquiry_notes ADD COLUMN ms_message_id TEXT'))
                conn.commit()


def _ensure_default_user():
    with app.app_context():
        if not User.query.first():
            pw = os.environ.get('APP_PASSWORD', 'changeme')
            db.session.add(User(username='admin', password_hash=generate_password_hash(pw)))
            db.session.commit()
            if pw == 'changeme':
                print("WARNING: No APP_PASSWORD env var set. Default password is 'changeme' — change it.")


def _migrate_project_columns():
    from sqlalchemy import text, inspect
    with app.app_context():
        insp = inspect(db.engine)
        proj_existing = {col['name'] for col in insp.get_columns('projects')}
        proj_cols = [
            ('instruction_type', 'TEXT'), ('fee_percent', 'REAL'), ('fee_fixed', 'REAL'),
            ('available_from', 'TEXT'),   ('next_call', 'TEXT'),
            ('client_phone', 'TEXT'),     ('client_mobile', 'TEXT'),
            ('client_email', 'TEXT'),     ('key_contact', 'TEXT'),
            ('landlord_name', 'TEXT'),    ('agent_assigned', 'TEXT'),
            ('location_description', 'TEXT'),
        ]
        cont_existing  = {col['name'] for col in insp.get_columns('contacts')}
        prop_existing  = {col['name'] for col in insp.get_columns('properties')}
        trans_existing = {col['name'] for col in insp.get_columns('transactions')}

        prop_extra = [
            ('residential_use',       'TEXT'),
            ('listing_size',          'REAL'),
            ('listing_size_unit',     'TEXT'),
            ('brochure_data',         'BLOB'),
            ('brochure_filename',     'TEXT'),
            ('brochure_size',         'INTEGER'),
            ('floor_plan_data',       'BLOB'),
            ('floor_plan_filename',   'TEXT'),
            ('floor_plan_size',       'INTEGER'),
        ]
        trans_cols = [
            ('description',       'TEXT'), ('niy',              'REAL'),
            ('giy',               'REAL'), ('capital_rate_psf', 'REAL'),
            ('wault',             'REAL'), ('passing_income',   'REAL'),
            ('income_pct',        'REAL'), ('erv',              'REAL'),
            ('tenant_covenant',   'TEXT'), ('written_analysis', 'TEXT'),
            ('done_by',           'TEXT'), ('third_party_name', 'TEXT'),
            ('part_or_floor',     'TEXT'), ('source',           'TEXT'),
            ('source_contact',    'TEXT'), ('nda',              'BOOLEAN DEFAULT 0'),
            ('size_units',        'TEXT'), ('size_basis',       'TEXT'),
            ('demise_description','TEXT'), ('incentive_years',  'REAL'),
            ('headline_rate',     'REAL'), ('headline_rate_unit','TEXT'),
            ('net_rate',          'REAL'), ('next_break_date',  'TEXT'),
            ('no_break',          'BOOLEAN DEFAULT 0'),
            ('next_review_date',  'TEXT'), ('no_review',        'BOOLEAN DEFAULT 0'),
            ('review_type',       'TEXT'), ('repair',           'TEXT'),
            ('alienation',        'TEXT'), ('primary_use_class','TEXT'),
            ('lt_act',            'TEXT'), ('epc_rating',       'TEXT'),
            ('fitted',            'TEXT'),
        ]

        with db.engine.connect() as conn:
            for col_name, col_def in proj_cols:
                if col_name not in proj_existing:
                    conn.execute(text(f'ALTER TABLE projects ADD COLUMN {col_name} {col_def}'))
            for col_name, col_def in prop_extra:
                if col_name not in prop_existing:
                    conn.execute(text(f'ALTER TABLE properties ADD COLUMN {col_name} {col_def}'))
            for col_name, col_def in trans_cols:
                if col_name not in trans_existing:
                    conn.execute(text(f'ALTER TABLE transactions ADD COLUMN {col_name} {col_def}'))

        cont_cols = [
            ('req_category', 'TEXT'),      ('req_property_type', 'TEXT'),
            ('req_use_class', 'TEXT'),     ('req_area', 'TEXT'),
            ('req_size_min', 'REAL'),      ('req_size_max', 'REAL'),
            ('req_budget_min', 'REAL'),    ('req_budget_max', 'REAL'),
            ('req_budget_unit', 'TEXT'),   ('req_notes', 'TEXT'),
        ]
        with db.engine.connect() as conn:
            for col_name, col_def in cont_cols:
                if col_name not in cont_existing:
                    conn.execute(text(f'ALTER TABLE contacts ADD COLUMN {col_name} {col_def}'))
            conn.commit()


def _migrate_listing_columns():
    """Add new listing columns to existing properties table if missing."""
    from sqlalchemy import text, inspect
    with app.app_context():
        insp = inspect(db.engine)
        existing = {col['name'] for col in insp.get_columns('properties')}
        new_cols = [
            ('website_listed',     'BOOLEAN DEFAULT 0'),
            ('website_category',   'TEXT'),
            ('listing_status',     'TEXT'),
            ('featured',           'BOOLEAN DEFAULT 0'),
            ('area',               'TEXT'),
            ('use_class',          'TEXT'),
            ('listing_price',      'REAL'),
            ('listing_price_unit', 'TEXT'),
            ('price_display',      'TEXT'),
            ('beds',               'INTEGER'),
            ('baths',              'INTEGER'),
            ('lat',                'REAL'),
            ('lng',                'REAL'),
            ('photo_id',           'TEXT'),
            ('blurb',              'TEXT'),
        ]
        with db.engine.connect() as conn:
            for col_name, col_def in new_cols:
                if col_name not in existing:
                    conn.execute(text(f'ALTER TABLE properties ADD COLUMN {col_name} {col_def}'))
            conn.commit()


def _migrate_enquiry_columns():
    from sqlalchemy import text, inspect
    with app.app_context():
        insp = inspect(db.engine)
        existing = {col['name'] for col in insp.get_columns('enquiries')}
        new_cols = [
            ('req_size_min','REAL'),('req_size_max','REAL'),
            ('req_budget_min','REAL'),('req_budget_max','REAL'),
            ('req_budget_unit','TEXT'),('req_use_class','TEXT'),('req_category','TEXT'),
            ('last_contact_date','TEXT'),('next_follow_up','TEXT'),
            ('source','TEXT'),
            ('stage',"TEXT DEFAULT 'Enquiry Received'"),('stage_changed_on','DATE'),
            ('req_area','TEXT'),('req_property_type','TEXT'),('req_tenure','TEXT'),
            ('req_occupation_date','DATE'),('req_notes','TEXT'),
            ('preferred_contact','TEXT'),('priority','TEXT'),
            ('next_action','TEXT'),('next_call_date','DATE'),
            ('archived','BOOLEAN DEFAULT FALSE'),
        ]
        with db.engine.connect() as conn:
            for col_name, col_def in new_cols:
                if col_name not in existing:
                    conn.execute(text(f'ALTER TABLE enquiries ADD COLUMN {col_name} {col_def}'))
            conn.commit()


def _migrate_document_columns():
    from sqlalchemy import text, inspect
    with app.app_context():
        insp = inspect(db.engine)
        existing = {col['name'] for col in insp.get_columns('project_documents')}
        new_cols = [('file_data', 'BYTEA'), ('file_mime', 'TEXT'), ('file_size', 'INTEGER')]
        with db.engine.connect() as conn:
            for col_name, col_def in new_cols:
                if col_name not in existing:
                    conn.execute(text(f'ALTER TABLE project_documents ADD COLUMN {col_name} {col_def}'))
            conn.commit()


def _migrate_listings_table_columns():
    """Add any missing columns to the listings table."""
    from sqlalchemy import text, inspect
    insp = inspect(db.engine)
    if 'listings' not in insp.get_table_names():
        return
    existing = {col['name'] for col in insp.get_columns('listings')}
    new_cols = [
        ('project_id',           'INTEGER'),
        ('unit_name',            'TEXT'),
        ('zoopla_listed',        'BOOLEAN DEFAULT FALSE'),
        ('residential_use',      'TEXT'),
        ('min_size',             'REAL'),
        ('max_size',             'REAL'),
        ('measurement_std',      'TEXT'),
        ('total_size',           'REAL'),
        ('self_contained',       'BOOLEAN DEFAULT FALSE'),
        ('add_on_factor',        'REAL'),
        ('build_status',         'TEXT'),
        ('set_as_to_let',        'BOOLEAN DEFAULT TRUE'),
        ('lease_type',           'TEXT'),
        ('rent_qualifier',       'TEXT'),
        ('rent_inclusive',       'TEXT'),
        ('rent_from',            'REAL'),
        ('rent_to',              'REAL'),
        ('rent_comment',         'TEXT'),
        ('rent_on_application',  'BOOLEAN DEFAULT FALSE'),
        ('possession_now',       'BOOLEAN DEFAULT FALSE'),
        ('possession_quarter',   'TEXT'),
        ('possession_year',      'INTEGER'),
        ('possession_comment',   'TEXT'),
        ('lease_length_months',  'INTEGER'),
        ('lease_length_years',   'INTEGER'),
        ('lease_length_comment', 'TEXT'),
        ('inside_1954_act',      'TEXT'),
        ('repair_insuring',      'TEXT'),
        ('set_as_for_sale',      'BOOLEAN DEFAULT FALSE'),
        ('sale_price',           'REAL'),
        ('sale_price_display',   'TEXT'),
        ('service_charge',       'REAL'),
        ('service_charge_na',    'BOOLEAN DEFAULT FALSE'),
        ('service_charge_comment','TEXT'),
        ('rateable_value',       'REAL'),
        ('rateable_value_na',    'BOOLEAN DEFAULT FALSE'),
        ('rates_multiplier',     'REAL'),
        ('rates_payable',        'REAL'),
        ('epc_band',             'TEXT'),
        ('epc_band_potential',   'TEXT'),
        ('vat_comment',          'TEXT'),
        ('legal_fees',           'TEXT'),
        ('parking_ratio',        'TEXT'),
        ('parking_rent',         'REAL'),
        ('parking_rent_na',      'BOOLEAN DEFAULT FALSE'),
        ('parking_spaces',       'INTEGER'),
        ('summary_text',         'TEXT'),
        ('key_points',           'TEXT'),
        ('amenities',            'TEXT'),
        ('availability_reason',  'TEXT'),
        ('key_terms',            'TEXT'),
        ('location_description', 'TEXT'),
        ('initial_yield',        'REAL'),
        ('investment_vacant',    'TEXT'),
        ('tenure',               'TEXT'),
        ('lease_years_remaining','INTEGER'),
        ('lat',                  'REAL'),
        ('lng',                  'REAL'),
        ('brochure_data',        'BYTEA'),
        ('brochure_filename',    'TEXT'),
        ('brochure_size',        'INTEGER'),
        ('floor_plan_data',      'BYTEA'),
        ('floor_plan_filename',  'TEXT'),
        ('floor_plan_size',      'INTEGER'),
        ('updated_at',           'TIMESTAMP'),
        ('website_published_at', 'TIMESTAMP'),
        ('zoopla_published_at',  'TIMESTAMP'),
        ('epc_data',             'BYTEA'),
        ('epc_filename',         'TEXT'),
        ('epc_size',             'INTEGER'),
    ]
    with db.engine.connect() as conn:
        for col_name, col_def in new_cols:
            if col_name not in existing:
                conn.execute(text(f'ALTER TABLE listings ADD COLUMN {col_name} {col_def}'))
        # Widen text columns that db.create_all() first made too small (e.g. VARCHAR(5)
        # for epc_band, VARCHAR(30) for repair_insuring). Postgres enforces VARCHAR
        # length and 500s on longer dropdown values like "Not Required" or
        # "FRI — Full Repairing & Insuring"; SQLite ignores length so it only bit live.
        # Converting to TEXT removes the limit. Postgres-only (SQLite can't ALTER TYPE).
        if db.engine.dialect.name == 'postgresql':
            for col in ('epc_band', 'epc_band_potential', 'repair_insuring',
                        'inside_1954_act', 'rent_qualifier', 'rent_inclusive', 'lease_type'):
                conn.execute(text(f'ALTER TABLE listings ALTER COLUMN {col} TYPE TEXT'))
        conn.commit()


def _seed_listings_from_properties():
    """One-time: create Listing records from properties that have website_listed=True."""
    with app.app_context():
        if Listing.query.count() > 0:
            return
        props = Property.query.filter_by(website_listed=True).all()
        for p in props:
            db.session.add(Listing(
                property_id=p.id, unit_name=None,
                website_listed=True, listing_status=p.listing_status or 'available',
                featured=bool(p.featured), website_category=p.website_category,
                use_class=p.use_class, area=p.area,
                listing_price=p.listing_price, listing_price_unit=p.listing_price_unit,
                price_display=p.price_display,
                size=p.size, measurement_type=p.measurement_type,
                beds=p.beds, baths=p.baths,
                lat=p.lat, lng=p.lng,
                photo_id=p.photo_id, blurb=p.blurb,
            ))
        db.session.commit()


def _seed_project_listings():
    """Ensure every website-listed Property has a Project + project-managed Listing.

    Idempotent: skips any property that already has a Listing, so it is safe to
    call on every startup. On a fresh database this gives each website property a
    Project and a project-linked Listing, so the public website is driven by
    project-managed listings and each listing is editable from its project.
    Assumes an active app context (provided by serve.py / __main__).
    """
    for p in Property.query.filter_by(website_listed=True).all():
        if Listing.query.filter_by(property_id=p.id).first():
            continue
        project = Project.query.filter_by(property_id=p.id).first()
        if project is None:
            project = Project(property_id=p.id,
                              name=p.address or f"Property {p.id}",
                              status='Active')
            db.session.add(project)
            db.session.flush()  # assign project.id before referencing it
        db.session.add(Listing(
            project_id=project.id, property_id=p.id, unit_name=None,
            website_listed=True, listing_status=p.listing_status or 'available',
            featured=bool(p.featured), website_category=p.website_category,
            use_class=p.use_class, residential_use=p.residential_use,
            area=p.area, listing_price=p.listing_price,
            listing_price_unit=p.listing_price_unit, price_display=p.price_display,
            size=p.size or p.listing_size, measurement_type=p.measurement_type,
            beds=p.beds, baths=p.baths, photo_id=p.photo_id,
            blurb=p.blurb or p.description, lat=p.lat, lng=p.lng,
            created_at=p.created_at,
        ))
    db.session.commit()


def _migrate_crm_columns():
    """Add CRM lifecycle-status + follow-up columns to contacts & organisations.
    Idempotent (only adds missing columns). contact_activities table is created
    by db.create_all(). Postgres backfills existing rows with the DEFAULT."""
    from sqlalchemy import text, inspect
    with app.app_context():
        insp = inspect(db.engine)
        cont_existing = {c['name'] for c in insp.get_columns('contacts')}
        org_existing  = {c['name'] for c in insp.get_columns('organisations')}
        cont_cols = [
            ('status',            "TEXT DEFAULT 'Prospect'"),
            ('preferred_move_in', 'DATE'),
            ('lease_length',      'TEXT'),
            ('assigned_agent',    'TEXT'),
            ('last_contact_date', 'DATE'),
            ('next_follow_up',    'DATE'),
        ]
        org_cols = [('status', "TEXT DEFAULT 'Prospect'")]
        with db.engine.connect() as conn:
            for col_name, col_def in cont_cols:
                if col_name not in cont_existing:
                    conn.execute(text(f'ALTER TABLE contacts ADD COLUMN {col_name} {col_def}'))
            for col_name, col_def in org_cols:
                if col_name not in org_existing:
                    conn.execute(text(f'ALTER TABLE organisations ADD COLUMN {col_name} {col_def}'))
            conn.commit()


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        _migrate_project_columns()
        _migrate_listing_columns()
        _migrate_listings_table_columns()
        _migrate_document_columns()
        _migrate_enquiry_columns()
        _migrate_email_columns()
        _migrate_crm_columns()
        _ensure_default_user()
        if Property.query.count() == 0:
            import import_listings  # seeds the 32 website properties
            _seed_project_listings()
    app.run(debug=False, host='127.0.0.1', port=8080)

# GoHighLevel live sync (Cowan & Rutter sub-account)
import ghl_sync
ghl_sync.init(app, db, Contact=Contact, Enquiry=Enquiry, Project=Project)
