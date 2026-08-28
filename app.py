import io
import os
import re
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import ms_graph
import ms_sync
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

class Council(db.Model):
    """A billing authority, and how to reach its business rates team.

    Held once, centrally, rather than typed onto each property or written into
    a brochure template. When a council changes its number, it is corrected
    here and every property and every set of particulars follows.
    """
    __tablename__ = 'councils'
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(160), nullable=False, unique=True)
    short_name   = db.Column(db.String(80))       # for a list, where the full name is long
    phone        = db.Column(db.String(40))
    email        = db.Column(db.String(160))
    website      = db.Column(db.String(255))
    address      = db.Column(db.Text)
    verified_on  = db.Column(db.Date)             # when the office last checked these
    active       = db.Column(db.Boolean, default=True, nullable=False)
    notes        = db.Column(db.Text)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    properties = db.relationship('Property', backref='council', lazy=True)

    @property
    def label(self):
        return self.short_name or self.name

    @property
    def contact_line(self):
        """The council's number, for a brochure. Nothing else goes on one."""
        return (self.phone or '').strip()

    def __repr__(self):
        return f'<Council {self.name}>'


class RatesMultiplier(db.Model):
    """A published multiplier, for one tax year.

    Effective-dated rather than written into the code, because the government
    sets a new one every year and changes who may use which. Adding next
    year's figures is a row, not a release.
    """
    __tablename__ = 'rates_multipliers'
    id           = db.Column(db.Integer, primary_key=True)
    tax_year     = db.Column(db.String(10), nullable=False, index=True)   # 2025/26
    name         = db.Column(db.String(120), nullable=False)             # Standard multiplier
    multiplier_type = db.Column(db.String(40))                           # Standard / Small business
    value        = db.Column(db.Integer, nullable=False)                 # scaled: 0.499 -> 49900
    category     = db.Column(db.String(60))       # a property category, or blank for any
    rv_min       = db.Column(db.BigInteger)       # pence, inclusive; blank for no floor
    rv_max       = db.Column(db.BigInteger)       # pence, exclusive; blank for no ceiling
    starts_on    = db.Column(db.Date)
    ends_on      = db.Column(db.Date)
    source       = db.Column(db.String(255))      # where the figure came from
    verified_on  = db.Column(db.Date)             # when the office last checked it
    active       = db.Column(db.Boolean, default=True, nullable=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def display_value(self):
        import business_rates as br
        return br.multiplier_str(self.value)

    @property
    def is_verified(self):
        return self.verified_on is not None

    def as_row(self):
        """The plain dictionary the calculation module works in."""
        return {'id': self.id, 'tax_year': self.tax_year, 'name': self.name,
                'multiplier_type': self.multiplier_type, 'value': self.value,
                'category': self.category, 'rv_min': self.rv_min,
                'rv_max': self.rv_max, 'starts_on': self.starts_on,
                'source': self.source, 'verified_on': self.verified_on}

    def __repr__(self):
        return f'<RatesMultiplier {self.tax_year} {self.name}>'


class RatesCalculation(db.Model):
    """One saved estimate, with every input that produced it.

    Kept rather than overwritten. A brochure issued last year quoted a figure,
    and the record of how that figure was reached has to survive the next tax
    year's recalculation. Only one row per property is current; the rest are
    history and are never edited.
    """
    __tablename__ = 'rates_calculations'
    id            = db.Column(db.Integer, primary_key=True)
    property_id   = db.Column(db.Integer, db.ForeignKey('properties.id'),
                              nullable=False, index=True)
    is_current    = db.Column(db.Boolean, default=True, nullable=False, index=True)

    tax_year      = db.Column(db.String(10), nullable=False)
    rateable_value = db.Column(db.BigInteger)     # pence

    multiplier_id    = db.Column(db.Integer, db.ForeignKey('rates_multipliers.id'))
    multiplier_value = db.Column(db.Integer)      # scaled, as used
    multiplier_name  = db.Column(db.String(120))  # as it read at the time
    multiplier_type  = db.Column(db.String(40))
    multiplier_overridden = db.Column(db.Boolean, default=False, nullable=False)
    override_reason  = db.Column(db.Text)

    base_payable  = db.Column(db.BigInteger)      # pence
    relief_type   = db.Column(db.String(80))
    relief_percent = db.Column(db.Numeric(6, 3))
    relief_amount = db.Column(db.BigInteger)      # pence
    transitional  = db.Column(db.BigInteger)      # pence, may be negative
    supplement    = db.Column(db.BigInteger)      # pence
    supplement_label = db.Column(db.String(120))
    other_adjustment = db.Column(db.BigInteger)   # pence, may be negative
    estimated_payable = db.Column(db.BigInteger)  # pence

    notes         = db.Column(db.Text)            # internal; never on a brochure
    calculated_on = db.Column(db.Date)
    calculated_by = db.Column(db.String(80))
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    multiplier = db.relationship('RatesMultiplier', lazy=True)

    @property
    def estimated(self):
        import business_rates as br
        return br.money(self.estimated_payable)

    @property
    def monthly_payable(self):
        from decimal import Decimal, ROUND_HALF_UP
        if self.estimated_payable is None:
            return None
        return int((Decimal(self.estimated_payable) / 12)
                   .quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    def __repr__(self):
        return f'<RatesCalculation property={self.property_id} {self.tax_year}>'


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
    brochure_data     = db.deferred(db.Column(db.LargeBinary))
    brochure_filename = db.Column(db.String(255))
    brochure_size     = db.Column(db.Integer)
    floor_plan_data   = db.deferred(db.Column(db.LargeBinary))
    floor_plan_filename = db.Column(db.String(255))
    floor_plan_size   = db.Column(db.Integer)

    # ── Business rates ──
    # The billing authority is a fact about the property, so it is held here
    # and every instruction on the property inherits it.
    council_id        = db.Column(db.Integer, db.ForeignKey('councils.id'), index=True)

    # A council's own figure, kept apart from anything the CRM worked out. One
    # is evidence; the other is an estimate, and a brochure must not blur them.
    rates_confirmed        = db.Column(db.Boolean, default=False)
    rates_confirmed_amount = db.Column(db.BigInteger)     # pence
    rates_confirmed_on     = db.Column(db.Date)
    rates_confirmed_ref    = db.Column(db.Text)
    rates_confirmed_by     = db.Column(db.String(80))

    rates_calculations = db.relationship(
        'RatesCalculation', backref='prop', lazy=True,
        cascade='all, delete-orphan',
        order_by='RatesCalculation.created_at.desc()')

    # Who the property is held for. Their details are read from their own
    # record and never copied here.
    client_contact_id = db.Column(db.Integer, db.ForeignKey('contacts.id'), index=True)

    transactions = db.relationship('Transaction', backref='property', lazy=True, cascade='all, delete-orphan')
    projects = db.relationship('Project', backref='property', lazy=True, cascade='all, delete-orphan')

    @property
    def tenures(self):
        return [t for t in self.transactions if t.transaction_type == 'Leasehold']

    @property
    def client_contact(self):
        return Contact.query.get(self.client_contact_id) if self.client_contact_id else None

    @property
    def display_size(self):
        if self.size and self.measurement_type:
            return f"{self.size:,.0f} sq ft ({self.measurement_type})"
        return '—'

    # ── Business rates ──

    @property
    def current_rates(self):
        """The saved estimate in force, or nothing. History is not returned."""
        return next((c for c in self.rates_calculations if c.is_current), None)

    @property
    def rateable_value(self):
        """The rateable value the current estimate was worked from, in pence."""
        current = self.current_rates
        return current.rateable_value if current else None

    @property
    def listing_rateable_value(self):
        """A rateable value typed on one of this property's listings, in pence.

        That field predates the calculator. It is not used for anything on a
        brochure any more, but somebody may have typed a figure into it, so the
        calculator offers it rather than letting the two quietly disagree.
        """
        row = (Listing.query
               .filter(Listing.property_id == self.id,
                       Listing.rateable_value.isnot(None),
                       Listing.rateable_value > 0)
               .order_by(Listing.id.desc()).first())
        if not row:
            return None
        import business_rates as _br
        return _br.to_pence(row.rateable_value)

    @property
    def rates_for_brochure(self):
        """What a brochure may quote, in pence, or None.

        The CRM's own estimate, and nothing else. Older records may still hold
        a council-confirmed figure from when the CRM asked for one; it is not
        read here and cannot override the estimate. The columns are left in
        place rather than dropped, so no history is destroyed.
        """
        current = self.current_rates
        if current and current.estimated_payable is not None:
            return current.estimated_payable
        return None


# ── Property types ───────────────────────────────────────────────────────────
#
# One list, used wherever a property type is offered, so the same words mean
# the same thing on a property, an instruction, an applicant's requirements and
# a website listing. The value stored is the label: it reads properly in older
# records and in exports, and there is only one of it.
#
# Light Industrial is its own category, never rolled in with Industrial. A
# workshop is not a warehouse, and an applicant who asked for one has not asked
# for the other.

PROPERTY_TYPES = [
    'Office',
    'Retail',
    'Industrial',
    'Creative / Art Studio',
    'Light Industrial',
]

# Kept so records entered before this list are still offered their own value
# and are never silently rewritten.
PROPERTY_TYPES_LEGACY = [
    'Warehouse', 'Residential', 'Mixed Use', 'Land', 'Hotel', 'Leisure', 'Other',
]

ALL_PROPERTY_TYPES = PROPERTY_TYPES + PROPERTY_TYPES_LEGACY


def property_type_options(current=None):
    """The types to offer, with anything already on the record kept.

    A property recorded years ago as something no longer offered keeps its own
    value rather than being quietly changed to the nearest thing.
    """
    options = list(ALL_PROPERTY_TYPES)
    if current and current not in options:
        options.append(current)
    return options


def same_property_type(wanted, found):
    """Whether a property's type is the one asked for.

    Compared whole, not by substring. "Industrial" and "Light Industrial"
    share a word and are different things; matching on the word alone would
    offer every warehouse to somebody who asked for a workshop.
    """
    return (wanted or '').strip().lower() == (found or '').strip().lower()


# ── Transactions: the shared vocabulary and the money rules ──────────────────

TRANSACTION_STATUSES = [
    'Draft', 'In Progress', 'Under Offer', 'Terms Agreed',
    'Solicitors Instructed', 'Completed', 'Commission Billed', 'Part Paid',
    'Paid', 'Fallen Through', 'Archived',
]

# How far a transaction has actually got. The single status field mixes
# progression with payment — "Paid" says nothing about whether it completed —
# so the two are read apart. A transaction that has been billed or paid must
# have completed to get there, so its stage is Completed.
TRANSACTION_STAGES = [
    'Draft', 'In Progress', 'Under Offer', 'Terms Agreed',
    'Solicitors Instructed', 'Completed',
]

STATUS_TO_STAGE = {
    'Commission Billed': 'Completed',
    'Part Paid': 'Completed',
    'Paid': 'Completed',
}

# Where the money has got to. Worked out from what is actually recorded
# against the transaction, never from its stage: completing a deal does not
# pay the invoice.
PAYMENT_STATES = ['Not Billed', 'Commission Billed', 'Part Paid', 'Paid']

# Earned nothing, so left out of every figure on the dashboard.
TRANSACTION_EXCLUDED = {'Fallen Through', 'Archived'}

# Reached completion. A transaction does not stop being completed once the
# invoice is raised or paid, so those statuses count too.
TRANSACTION_COMPLETED = {'Completed', 'Commission Billed', 'Part Paid', 'Paid'}

# Pipeline weight for the new stage, between agreed and with the solicitors.


# A letting goes through solicitors; a licence does not, so the solicitor
# details are put away when one is chosen.
AGREEMENT_TYPES = ['Letting', 'Licence']

# The VAT rate used when a transaction does not carry its own.
VAT_RATE_DEFAULT = float(os.environ.get('VAT_RATE', '20'))


def money_gbp(amount, blank='—'):
    """A sum of money the way an invoice writes it: £12,345.00."""
    if amount is None:
        return blank
    return f'£{float(amount):,.2f}'


def money_short(amount):
    """A sum for a chart axis, where the pennies would only be noise."""
    if amount is None:
        return '—'
    a = float(amount)
    if abs(a) >= 1_000_000:
        return f'£{a / 1_000_000:,.2f}m'.replace('.00m', 'm')
    if abs(a) >= 1_000:
        return f'£{a / 1_000:,.1f}k'.replace('.0k', 'k')
    return f'£{a:,.0f}'


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

    # ── Fee and invoicing ──
    # These carry the money. Nothing here is derived and stored: the
    # commission, VAT, invoice total and outstanding balance are all worked
    # out from these on the way out, so a figure can never go stale.
    reference         = db.Column(db.String(30), index=True)  # TR-0001
    status            = db.Column(db.String(30), default='Draft', index=True)
    fee_earner        = db.Column(db.String(120), index=True)   # kept: what was typed before
    fee_earner_id     = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    client            = db.Column(db.String(255))   # who we act for and invoice
    project_id        = db.Column(db.Integer, db.ForeignKey('projects.id'))
    agreed_value      = db.Column(db.Float)         # agreed rent or sale price
    fee_type          = db.Column(db.String(20))    # Percentage / Fixed
    fee_percent       = db.Column(db.Float)         # % of the agreed value
    fixed_fee         = db.Column(db.Float)         # £, used instead of a %
    vat_rate          = db.Column(db.Float)         # % — blank means the default
    invoice_number    = db.Column(db.String(40))
    invoice_date      = db.Column(db.Date, index=True)
    payment_due_date  = db.Column(db.Date)
    completion_date   = db.Column(db.Date, index=True)
    agreement_type    = db.Column(db.String(20))    # Licence / Letting
    expected_completion_date = db.Column(db.Date, index=True)
    terms_agreed_date         = db.Column(db.Date)
    solicitors_instructed_date = db.Column(db.Date)

    # ── Solicitors ──
    client_solicitor       = db.Column(db.String(255))
    client_solicitor_firm  = db.Column(db.String(255))
    client_solicitor_email = db.Column(db.String(255))
    client_solicitor_phone = db.Column(db.String(60))
    other_solicitor        = db.Column(db.String(255))
    other_solicitor_firm   = db.Column(db.String(255))
    other_solicitor_email  = db.Column(db.String(255))
    other_solicitor_phone  = db.Column(db.String(60))

    organisation_links = db.relationship(
        'OrganisationRole', lazy=True,
        primaryjoin='Transaction.id == foreign(OrganisationRole.transaction_id)',
        viewonly=True)
    payments  = db.relationship('TransactionPayment', backref='transaction',
                                lazy=True, cascade='all, delete-orphan')
    documents = db.relationship('TransactionDocument', backref='transaction',
                                lazy=True, cascade='all, delete-orphan')
    project   = db.relationship('Project', foreign_keys=[project_id])

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

    # ── The money ────────────────────────────────────────────────────────────
    # One place, worked out on demand. Every total on the Transactions page —
    # the cards, the chart, the table — comes through these, so a figure can
    # never disagree with the record it came from.

    @property
    def counterparty(self):
        """The tenant or purchaser, whichever this transaction has."""
        return self.purchaser or self.tenant or None

    @property
    def commission_basis(self):
        """The agreed sum the fee is charged on.

        The agreed figure if one has been entered, otherwise whichever of the
        sale price and the rent the record actually carries. Older records were
        made before there was an agreed-value field, and a sale price is often
        recorded against a transaction typed as leasehold and the other way
        round, so the type is a preference here rather than a rule. Every
        figure comes off the record; nothing is estimated.
        """
        if self.agreed_value:
            return float(self.agreed_value)
        sale, rent = float(self.value or 0.0), float(self.rent_pa or 0.0)
        if self.transaction_type == 'Capital':
            return sale or rent
        return rent or sale

    @property
    def basis_source(self):
        """Which figure the fee is being charged on, for the record to say."""
        if self.agreed_value:
            return 'the agreed value'
        if not self.commission_basis:
            return None
        sale, rent = float(self.value or 0.0), float(self.rent_pa or 0.0)
        if self.transaction_type == 'Capital':
            return 'the sale price' if sale else 'the rent'
        return 'the rent' if rent else 'the sale price'

    @property
    def charges_fixed_fee(self):
        """Whether this transaction is on a fixed fee rather than a percentage.

        A record made before the fee basis existed carries no choice at all, so
        whichever figure was actually filled in decides it. That stops a fee
        someone has plainly entered from being read as nothing.
        """
        if self.fee_type == 'Fixed':
            return bool(self.fixed_fee) or not self.fee_percent
        if self.fee_type == 'Percentage':
            return not self.fee_percent and bool(self.fixed_fee)
        return bool(self.fixed_fee) and not self.fee_percent

    @property
    def net_commission(self):
        """Our fee, before VAT."""
        if self.charges_fixed_fee:
            return round(float(self.fixed_fee or 0.0), 2)
        if self.fee_percent:
            return round(self.commission_basis * float(self.fee_percent) / 100.0, 2)
        return 0.0

    @property
    def applicable_vat_rate(self):
        return VAT_RATE_DEFAULT if self.vat_rate is None else float(self.vat_rate)

    @property
    def vat_amount(self):
        return round(self.net_commission * self.applicable_vat_rate / 100.0, 2)

    @property
    def total_invoice(self):
        return round(self.net_commission + self.vat_amount, 2)

    @property
    def commission_received(self):
        """Only money actually recorded as received, each payment once."""
        return round(sum(float(p.amount or 0.0) for p in self.payments), 2)

    @property
    def outstanding(self):
        return round(self.total_invoice - self.commission_received, 2)

    @property
    def counts_towards_totals(self):
        """Whether this transaction belongs in the firm's figures at all.

        A transaction that fell through or was archived earned nothing and is
        left out of every total, which is what makes the dashboard recalculate
        the moment its status changes.
        """
        return self.status not in TRANSACTION_EXCLUDED

    @property
    def has_completed(self):
        """Reached completion: a completion date and a post-completion status."""
        return bool(self.completion_date) and self.status in TRANSACTION_COMPLETED

    @property
    def is_billed(self):
        """Invoiced. A draft is never counted as billed, even if dated."""
        return bool(self.invoice_date) and self.counts_towards_totals \
            and self.status != 'Draft'

    @property
    def is_overdue(self):
        return bool(self.payment_due_date and self.outstanding > 0.005
                    and self.payment_due_date < date.today())

    @property
    def client_display(self):
        """Who the transaction is for: the linked organisation, or the name
        typed before organisations were linked."""
        live = [r for r in getattr(self, 'organisation_links', [])
                if r.role == 'Client' and r.is_current]
        if live:
            return live[0].organisation.display_name
        return self.client or None

    @property
    def fee_earner_display(self):
        """The fee earner's full name, or whatever was recorded before."""
        return fee_earner_name(self.fee_earner_id, self.fee_earner)

    @property
    def stage(self):
        """How far the transaction has got, apart from the money.

        A transaction that has been billed or paid must have completed to get
        there, so it still reads as Completed. Somebody looking at the
        Organiser can see that a deal completed as well as whether the
        commission has come in.
        """
        status = self.status or 'Draft'
        if status in TRANSACTION_EXCLUDED:
            return status              # fallen through or archived is the whole story
        return STATUS_TO_STAGE.get(status, status)

    @property
    def payment_state(self):
        """Where the money has got to, from what is actually recorded.

        The same invoice and payment rules the Transactions page uses, so the
        two can never disagree. Completing a deal does not pay its invoice, so
        a completed transaction with no invoice reads as Not Billed.
        """
        if not self.is_billed:
            return 'Not Billed'
        total, received = self.total_invoice, self.commission_received
        if received <= 0.005:
            return 'Commission Billed'
        if received + 0.005 < total:
            return 'Part Paid'
        return 'Paid'

    @property
    def shows_payment_state(self):
        """Whether a payment position is worth showing at all.

        Nothing was ever going to be billed on a deal that fell through or was
        archived, so saying "Not Billed" about it would only mislead.
        """
        return self.counts_towards_totals

    @property
    def fee_basis_label(self):
        """How the fee is charged, said plainly so the sum is never a mystery."""
        if self.charges_fixed_fee:
            return f'Fixed {money_gbp(self.fixed_fee)}' if self.fixed_fee else 'Fixed fee'
        if self.fee_percent:
            return f'{self.fee_percent:g}%'
        return 'No fee entered'


class CommissionTarget(db.Model):
    """What the office is aiming to bill in a month, a quarter or a year.

    Targets are held per period rather than as one figure, so a busy quarter
    can be aimed at differently from a quiet one. A quarter or a year with no
    target of its own is added up from the months inside it, so entering
    twelve monthly targets is enough on its own.
    """
    __tablename__ = 'commission_targets'
    id          = db.Column(db.Integer, primary_key=True)
    period_type = db.Column(db.String(10), nullable=False)   # month / quarter / year
    period_key  = db.Column(db.String(10), nullable=False)   # 2026-08 / 2026-Q3 / 2026
    amount      = db.Column(db.Float, nullable=False)
    set_by      = db.Column(db.String(80))
    set_at      = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('period_type', 'period_key',
                                          name='uq_target_period'),)


class TransactionPayment(db.Model):
    """One payment received against a transaction's invoice.

    Payments are rows rather than a running total on the transaction, so a
    part payment is recorded as itself and the same money cannot be counted
    twice by saving the record again.
    """
    __tablename__ = 'transaction_payments'
    id             = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id'),
                               nullable=False, index=True)
    amount         = db.Column(db.Float, nullable=False)
    received_on    = db.Column(db.Date, index=True)
    method         = db.Column(db.String(40))
    reference      = db.Column(db.String(80))
    note           = db.Column(db.String(255))
    recorded_by    = db.Column(db.String(80))
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)


class TransactionDocument(db.Model):
    """A file kept against a transaction. The file itself is loaded only when
    somebody asks for it, so listing a transaction never pulls it out."""
    __tablename__ = 'transaction_documents'
    id             = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id'),
                               nullable=False, index=True)
    kind           = db.Column(db.String(40))       # Invoice, Terms, Contract…
    filename       = db.Column(db.String(255))
    size           = db.Column(db.Integer)
    data           = db.deferred(db.Column(db.LargeBinary))
    uploaded_by    = db.Column(db.String(80))
    uploaded_at    = db.Column(db.DateTime, default=datetime.utcnow)


# ── CRM Models ─────────────────────────────────────────────────────────────

# CRM applicant/contact + organisation lifecycle statuses (one shared vocabulary).
CONTACT_STATUSES = [
    'New Enquiry', 'Active Requirement', 'Prospect', 'Under Offer',
    'Current Tenant', 'Requirement Satisfied', 'Inactive', 'Archived',
]
# Statuses hidden from the default "active" list views.
ARCHIVED_STATUSES = ['Archived']


# ── Organisations and the roles they play ────────────────────────────────────
#
# A company is one record. Landlord, tenant, client, applicant, vendor and
# purchaser are things it *does*, not things it *is*, so each one is a row in
# organisation_roles pointing back at the same organisation. That is what lets
# Marsden Estates be the landlord of one building and the tenant of another
# without existing twice.

ORG_TYPES = [
    'Landlord', 'Tenant', 'Applicant', 'Client', 'Vendor', 'Purchaser',
    'Property company', 'Managing agent', 'Solicitor', 'Surveyor',
    'Contractor', 'Supplier', 'Introducer', 'Other',
]

# One status at a time, and always shown in words. Colour only ever supports
# the word; it never carries the meaning on its own.
ORG_STATUSES = [
    ('Prospect',         'Potential client or business relationship'),
    ('Active',           'Involved in current agency activity'),
    ('Searching',        'Has an active property requirement'),
    ('Under Offer',      'Connected to an agreed offer or progressing transaction'),
    ('Current Client',   'Has a current instruction or active professional relationship'),
    ('Current Occupier', 'Occupies a linked property'),
    ('Past Client',      'Previously completed business with the agency'),
    ('Inactive',         'Valid record with no current activity'),
    ('Do Not Contact',   'Excluded from marketing and routine communications'),
    ('Archived',         'Retained for history but hidden from active lists'),
]
ORG_STATUS_NAMES = [name for name, _ in ORG_STATUSES]

# Kept off the active lists, but never deleted.
ORG_HIDDEN_STATUSES = {'Archived'}

# Never emailed in bulk, whatever a marketing list says.
ORG_NO_CONTACT = 'Do Not Contact'

# The relationships an organisation can hold, and what each one attaches to.
ORG_ROLES = [
    ('Landlord',  'Landlord of a property'),
    ('Tenant',    'Tenant or occupier of a property'),
    ('Client',    'Client on a project or instruction'),
    ('Applicant', 'Searching for property'),
    ('Vendor',    'Selling in a sale instruction'),
    ('Purchaser', 'Buying in a transaction'),
]
ORG_ROLE_NAMES = [name for name, _ in ORG_ROLES]


class Organisation(db.Model):
    __tablename__ = 'organisations'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    # Kept for the records that already carry it. The types a company holds now
    # live in organisation_types, because a company usually holds several.
    org_type = db.Column(db.String(50))
    status = db.Column(db.String(30), default='Prospect')
    address = db.Column(db.String(255))
    postcode = db.Column(db.String(20))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(120))
    website = db.Column(db.String(200))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ── Essential details ──
    trading_name    = db.Column(db.String(255))
    legal_name      = db.Column(db.String(255))
    fee_earner      = db.Column(db.String(120), index=True)   # kept: what was typed before
    fee_earner_id   = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    source          = db.Column(db.String(120))     # how they came to us
    main_contact_id = db.Column(db.Integer, db.ForeignKey('contacts.id'))

    # ── Company information ──
    company_number     = db.Column(db.String(40), index=True)
    vat_number         = db.Column(db.String(40))
    registered_address = db.Column(db.String(400))
    trading_address    = db.Column(db.String(400))
    companies_house_status = db.Column(db.String(60))
    incorporated_on    = db.Column(db.Date)
    nature_of_business = db.Column(db.String(255))

    # ── AML and compliance ── (read and written under permission)
    aml_status         = db.Column(db.String(40))
    aml_reviewed_on    = db.Column(db.Date)
    beneficial_owners  = db.Column(db.Text)
    verification_notes = db.Column(db.Text)
    marketing_consent  = db.Column(db.Boolean, default=False)

    # ── Accounts ──
    accounts_contact = db.Column(db.String(255))
    accounts_email   = db.Column(db.String(255))
    invoice_address  = db.Column(db.String(400))
    payment_terms    = db.Column(db.String(120))
    vat_status       = db.Column(db.String(60))
    accounts_notes   = db.Column(db.Text)

    contacts = db.relationship('Contact', backref='organisation', lazy=True,
                               foreign_keys='Contact.organisation_id')
    main_contact = db.relationship('Contact', foreign_keys=[main_contact_id])
    types = db.relationship('OrganisationType', backref='organisation',
                            lazy=True, cascade='all, delete-orphan')
    roles = db.relationship('OrganisationRole', backref='organisation',
                            lazy=True, cascade='all, delete-orphan')
    requirements = db.relationship('OrganisationRequirement', backref='organisation',
                                   lazy=True, cascade='all, delete-orphan')

    @property
    def type_names(self):
        """Every type this organisation holds, in the order they are offered."""
        held = {t.name for t in self.types}
        if self.org_type:
            held.add(self.org_type)          # whatever the record already carried
        return [t for t in ORG_TYPES if t in held] + sorted(held - set(ORG_TYPES))

    @property
    def display_name(self):
        return self.trading_name or self.name

    @property
    def do_not_contact(self):
        """Never to be emailed in bulk. Checked on the server, not in a list."""
        return self.status == ORG_NO_CONTACT

    @property
    def is_archived(self):
        return self.status in ORG_HIDDEN_STATUSES

    @property
    def current_roles(self):
        return [r for r in self.roles if r.is_current]

    @property
    def former_roles(self):
        return [r for r in self.roles if not r.is_current]

    def roles_of(self, role):
        return [r for r in self.roles if r.role == role]


class OrganisationType(db.Model):
    """One of the things an organisation is. A company usually holds several."""
    __tablename__ = 'organisation_types'
    id = db.Column(db.Integer, primary_key=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey('organisations.id'),
                                nullable=False, index=True)
    name = db.Column(db.String(60), nullable=False)
    __table_args__ = (db.UniqueConstraint('organisation_id', 'name',
                                          name='uq_org_type'),)


class OrganisationRole(db.Model):
    """One relationship between an organisation and something in the CRM.

    The role, what it attaches to, who to speak to about it and when it ran.
    Ending a relationship sets its end date; the row stays, because what a
    company used to be is part of its history.
    """
    __tablename__ = 'organisation_roles'
    id = db.Column(db.Integer, primary_key=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey('organisations.id'),
                                nullable=False, index=True)
    role = db.Column(db.String(30), nullable=False, index=True)

    property_id    = db.Column(db.Integer, db.ForeignKey('properties.id'))
    project_id     = db.Column(db.Integer, db.ForeignKey('projects.id'))
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id'))
    # The contact for this relationship, which need not be the organisation's
    # general main contact — a different person often handles each building.
    contact_id     = db.Column(db.Integer, db.ForeignKey('contacts.id'))

    start_date = db.Column(db.Date)
    end_date   = db.Column(db.Date)
    notes      = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.String(80))
    ended_by   = db.Column(db.String(80))

    property_ref = db.relationship('Property', foreign_keys=[property_id])
    project      = db.relationship('Project', foreign_keys=[project_id])
    transaction  = db.relationship('Transaction', foreign_keys=[transaction_id])
    contact      = db.relationship('Contact', foreign_keys=[contact_id])

    @property
    def is_current(self):
        """Still running: no end date, or one that has not arrived."""
        return self.end_date is None or self.end_date > date.today()

    @property
    def attached_to(self):
        """What this relationship is about, whichever kind it is."""
        return self.property_ref or self.project or self.transaction


class OrganisationRequirement(db.Model):
    """What an organisation is looking for. It may be looking for more than
    one thing at once, so this is a table rather than a set of columns."""
    __tablename__ = 'organisation_requirements'
    id = db.Column(db.Integer, primary_key=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey('organisations.id'),
                                nullable=False, index=True)
    title          = db.Column(db.String(160))
    locations      = db.Column(db.String(400))
    property_type  = db.Column(db.String(120))
    intended_use   = db.Column(db.String(160))
    use_class      = db.Column(db.String(60))
    size_min       = db.Column(db.Float)
    size_max       = db.Column(db.Float)
    rent_min       = db.Column(db.Float)
    rent_max       = db.Column(db.Float)
    price_min      = db.Column(db.Float)
    price_max      = db.Column(db.Float)
    tenure         = db.Column(db.String(30))      # Lease / Purchase / Either
    occupation_from = db.Column(db.Date)
    lease_length   = db.Column(db.String(120))
    extra          = db.Column(db.Text)
    active         = db.Column(db.Boolean, default=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)


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
    # Which Outlook contact this one is mirrored to, if it has been.
    ms_contact_id  = db.Column(db.String(255))
    ms_synced_at   = db.Column(db.DateTime)
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
    assigned_agent    = db.Column(db.String(100))   # kept: what was typed before
    fee_earner_id     = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
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
    fee_earner = db.Column(db.String(100))   # kept: what was typed before
    fee_earner_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
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
    fee_earner = db.Column(db.String(100))   # kept: what was typed before
    fee_earner_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    # Who the instruction is for. The company beside their name comes from
    # their own contact record, never copied here.
    client_contact_id = db.Column(db.Integer, db.ForeignKey('contacts.id'), index=True)
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

    @property
    def client_contact(self):
        return Contact.query.get(self.client_contact_id) if self.client_contact_id else None

    @property
    def client_display(self):
        """The client's name, with their company, or the name typed before."""
        linked = self.client_contact
        return contact_label(linked) if linked else (self.client or None)

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
    # The one-line headline for this listing, e.g.
    #   GROUND FLOOR OFFICE SPACE | TO LET | FULHAM SW6
    # It is the principal summary on the particulars and the summary sent to
    # Zoopla. There is deliberately only one of it, so the two cannot differ.
    strapline          = db.Column(db.String(400))
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
    brochure_data      = db.deferred(db.Column(db.LargeBinary))
    brochure_filename  = db.Column(db.String(255))
    brochure_size      = db.Column(db.Integer)
    floor_plan_data    = db.deferred(db.Column(db.LargeBinary))
    floor_plan_filename= db.Column(db.String(255))
    floor_plan_size    = db.Column(db.Integer)
    epc_data           = db.deferred(db.Column(db.LargeBinary))
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
        if self.listing_price_unit == 'sale': return n
        return n


class ListingPhoto(db.Model):
    """Individual photo attached to a listing."""
    __tablename__ = 'listing_photos'
    id         = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey('listings.id'), nullable=False)
    file_data  = db.deferred(db.Column(db.LargeBinary, nullable=False))
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
    file_data = db.deferred(db.Column(db.LargeBinary))
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
    fee_earner   = db.Column(db.String(100))   # kept: what was typed before
    fee_earner_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
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

    # How this person is named on a record. The username is for signing in;
    # this is what a client-facing figure should read.
    full_name     = db.Column(db.String(120))
    email         = db.Column(db.String(255))   # printed on particulars
    # Deliberately not called is_active: Flask-Login reads that name to decide
    # whether somebody may sign in, and a column defaulting to nothing would
    # lock the office out. This only decides who appears in a list.
    active        = db.Column(db.Boolean, default=True, nullable=False)
    can_earn_fees = db.Column(db.Boolean, default=True, nullable=False)

    @property
    def display_name(self):
        """What to show on a record. Never initials."""
        return self.full_name or self.username

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
        """Whether this appointment exists in the Outlook calendar as well."""
        return bool(self.ms_event_id)

    @property
    def from_outlook_only(self):
        """Made in Outlook rather than here.

        Those are pulled in and shown, but the CRM is not their home: it does
        not push changes back, and its own appointments are never overwritten
        by a pull.
        """
        return bool(self.ms_event_id) and self.created_by == 'Outlook'

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


def property_photographs(prop):
    """Every photograph of a property, in the order they are marketed in.

    A property's photographs live on its listings, so this gathers them across
    all of them and keeps the order each listing was arranged in — the first is
    the one that leads the property everywhere.
    """
    if prop is None:
        return []
    photos = []
    for listing in getattr(prop, 'unit_listings', []) or []:
        photos.extend(sorted(listing.photos or [],
                             key=lambda p: (p.sort_order or 0, p.id)))
    return photos


def gallery_photos(photos):
    """Photographs as the gallery needs them: a full view, a thumbnail, a caption.

    The images are asked for at the size they are shown at, so opening the
    gallery does not pull a full-resolution photograph for a thumbnail.
    """
    return [{
        'id': p.id,
        'src': url_for('listing_photo_image', id=p.id, w=1400),
        'thumb': url_for('listing_photo_image', id=p.id, w=200),
        'caption': p.caption or '',
    } for p in photos]


def contact_label(contact):
    """A person's name, with their company after it where they have one.

    "Jane Smith (Example Holdings Ltd)", or just "Jane Smith". The company is
    read from the person's own record, so it follows a rename rather than
    going stale, and somebody with no company shows no empty brackets.
    """
    if contact is None:
        return ''
    name = contact.full_name
    company = contact.organisation.name if contact.organisation else None
    return f'{name} ({company})' if company else name


def fee_earners():
    """Everyone who may be assigned work, in the order they should be offered.

    Only active accounts allowed to carry a fee. A viewer cannot be a fee
    earner because they cannot act on anything.
    """
    return (User.query
            .filter(User.active.is_(True), User.can_earn_fees.is_(True))
            .filter(User.role != 'viewer')
            .order_by(User.full_name, User.username).all())


def default_fee_earner():
    """The one to preselect on a new record, when there is only one to choose."""
    people = fee_earners()
    return people[0] if len(people) == 1 else None


def fee_earner_name(user_id, fallback=None):
    """A fee earner's name for display, falling back to whatever was recorded."""
    if user_id:
        user = User.query.get(user_id)
        if user:
            return user.display_name
    return fallback or None


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


def _projects_of_type(instruction_type, limit=25):
    """Active projects on a given instruction type, newest first.

    Read straight from the projects, not from their listings: an instruction
    such as a market appraisal has no website listing behind it. Because it is
    a live query, a project appears here the moment its type is set and drops
    out the moment it is changed.
    """
    return (Project.query
            .filter(Project.instruction_type == instruction_type,
                    Project.status == 'Active')
            .order_by(Project.created_at.desc())
            .limit(limit).all())


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
                           outlook_connected=ms_graph.is_configured())


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
    ok, note = ms_sync.push_event(app, db, ev)
    flash('Appointment added.' + (' ' + note if ok else ''), 'success')
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
        ms_sync.push_event(app, db, ev)      # keep Outlook in step
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
    ms_sync.push_event(app, db, ev)          # keep Outlook in step
    return jsonify({'ok': True, 'start': data['start'], 'end': data['end']})


@app.route('/diary/event/<int:id>/delete', methods=['POST'])
@requires('delete')
def diary_event_delete(id):
    ev = DiaryEvent.query.get_or_404(id)
    # Take it out of Outlook first, while the id is still on the record.
    ms_sync.remove_event(app, db, ev)
    return delete_record(ev, 'Appointment', 'diary')


@app.route('/')
def dashboard():
    prop_count = Property.query.count()
    trans_count = Transaction.query.count()
    proj_count = Project.query.count()
    contacts = Contact.query.order_by(Contact.created_at.desc()).limit(20).all()
    today = date.today()

    # Today's diary, in London time. An appointment is shown if any part of it
    # falls today, so something running from yesterday evening still appears.
    day_start = from_london(datetime.combine(to_london(datetime.utcnow()).date(),
                                             datetime.min.time()))
    day_end = day_start + timedelta(days=1)
    todays_diary = []
    for ev in (DiaryEvent.query
               .filter(DiaryEvent.start_at < day_end, DiaryEvent.end_at > day_start)
               .order_by(DiaryEvent.all_day.desc(), DiaryEvent.start_at)
               .limit(30).all()):
        start, end = to_london(ev.start_at), to_london(ev.end_at)
        todays_diary.append({
            'id': ev.id,
            'title': ev.title or 'Appointment',
            'when': 'All day' if ev.all_day else
                    f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}",
            'place': ev.location,
            'owner': ev.owner,
            'kind': ev.event_type,
            'from_outlook': getattr(ev, 'from_outlook_only', False),
            'past': (not ev.all_day) and end < to_london(datetime.utcnow()),
        })

    to_let = _available_listings(INSTRUCTION_TO_LET)
    for_sale = _available_listings(INSTRUCTION_FOR_SALE)
    appraisals = _projects_of_type(INSTRUCTION_APPRAISAL)
    # Landlords and clients with a call due — the third column of the organiser.
    landlords_to_call = (Contact.query
                         .filter(Contact.contact_type.in_(['Landlord', 'Client']),
                                 Contact.status.notin_(ARCHIVED_STATUSES))
                         .order_by(Contact.next_follow_up.is_(None), Contact.next_follow_up)
                         .limit(25).all())
    diary_items = _diary_items()[:12]

    # Recent transactions, narrowed by stage or payment position if asked.
    # Both are read from the transaction itself, so the Organiser can never
    # disagree with the Transactions page.
    tx_stage = (request.args.get('tx_stage') or '').strip()
    tx_payment = (request.args.get('tx_payment') or '').strip()
    recent_transactions = Transaction.query.order_by(
        Transaction.created_at.desc()).limit(60).all()
    if tx_stage in TRANSACTION_STAGES or tx_stage in TRANSACTION_EXCLUDED:
        recent_transactions = [t for t in recent_transactions if t.stage == tx_stage]
    if tx_payment in PAYMENT_STATES:
        recent_transactions = [t for t in recent_transactions
                               if t.shows_payment_state and t.payment_state == tx_payment]
    recent_transactions = recent_transactions[:10]
    enq_count = Enquiry.query.filter(Enquiry.status == 'Open').count()
    contact_count = Contact.query.count()

    return render_template('dashboard.html',
                           todays_diary=todays_diary,
                           to_let=to_let, for_sale=for_sale, appraisals=appraisals,
                           landlords_to_call=landlords_to_call, diary_items=diary_items,
                           prop_count=prop_count,
                           trans_count=trans_count,
                           proj_count=proj_count,
                           contact_count=contact_count,
                           enq_count=enq_count,
                           contacts=contacts,
                           recent_transactions=recent_transactions,
                           tx_stage=tx_stage, tx_payment=tx_payment,
                           today=today)


@app.route('/properties')
def properties_list():
    q = request.args.get('q', '')
    ptype = (request.args.get('property_type') or '').strip()
    query = Property.query
    if q:
        query = query.filter(
            db.or_(Property.address.ilike(f'%{q}%'), Property.postcode.ilike(f'%{q}%'))
        )
    properties = query.order_by(Property.address).all()
    if ptype:
        # Whole-value, so filtering for Industrial does not sweep up every
        # Light Industrial unit as well.
        properties = [p for p in properties if same_property_type(ptype, p.property_type)]
    return render_template('properties/list.html', properties=properties, q=q,
                           ptype=ptype,
                           type_counts={t: sum(1 for p in Property.query.all()
                                               if same_property_type(t, p.property_type))
                                        for t in PROPERTY_TYPES})


@app.route('/properties/new', methods=['GET', 'POST'])
@requires('create')
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
            council_id=_fcouncil(request.form.get('council_id')),
        )
        if not prop.council_id:
            flash('Choose the local authority. It decides which council’s '
                  'details go on the particulars, so it is not guessed.', 'error')
            return render_template('properties/form.html', prop=None,
                                   errors={'council_id': 'Choose the local authority.'},
                                   **rates_form_context())
        db.session.add(prop)
        db.session.commit()
        audit('create', entity='Property', entity_id=prop.id)
        flash('Property added successfully.', 'success')
        return redirect(url_for('property_detail', id=prop.id))
    return render_template('properties/form.html', prop=None,
                           **rates_form_context())


@app.route('/properties/<int:id>')
def property_detail(id):
    prop = Property.query.get_or_404(id)
    folder_labels = FOLDER_LABELS
    return render_template('properties/detail.html', prop=prop, folder_labels=folder_labels)


@app.route('/properties/<int:id>/edit', methods=['GET', 'POST'])
@requires('edit')
def property_edit(id):
    prop = Property.query.get_or_404(id)
    if request.method == 'POST':
        # Presence-guarded so the property record page, which shows part of the
        # record, cannot blank what it does not display.
        # Website listing details (category/price/photos/brochure) are managed per
        # instruction on the project's Website Listing tab — not on the Property.
        if 'council_id' in request.form and not _fcouncil(request.form.get('council_id')):
            flash('Choose the local authority. It decides which council’s '
                  'details go on the particulars, so it is not guessed.', 'error')
            return render_template('properties/form.html', prop=prop,
                                   errors={'council_id': 'Choose the local authority.'},
                                   **rates_form_context())
        was_council = prop.council_id
        apply_form_fields(prop, request.form, PROPERTY_FIELDS)
        if 'postcode' in request.form:
            prop.postcode = (request.form.get('postcode') or '').upper()
        db.session.commit()
        if prop.council_id != was_council:
            audit('council-changed', entity='Property', entity_id=prop.id,
                  detail=f'local authority set to {prop.council.name if prop.council else "none"}')
        audit('edit', entity='Property', entity_id=prop.id)
        flash('Property updated.', 'success')
        return _back_to('property_detail', id=prop.id)
    return render_template('properties/form.html', prop=prop,
                           **rates_form_context())


@app.route('/properties/<int:id>/delete', methods=['POST'])
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


# ── Business rates reference data ───────────────────────────────────────────
# Councils and multipliers are maintained here rather than in the code, so a
# new borough or next year's figures are a few minutes' typing and not a
# deploy. Nothing on these screens changes a property.

@app.route('/admin/rates')
@requires('admin')
def rates_reference():
    """The councils on record and the multipliers for each tax year."""
    return render_template(
        'admin/rates.html',
        council_list=Council.query.order_by(Council.name).all(),
        multipliers=(RatesMultiplier.query
                     .order_by(RatesMultiplier.tax_year.desc(),
                               RatesMultiplier.name).all()),
        tax_years=br.tax_year_options(date.today()),
        today=date.today())


@app.route('/admin/rates/council', methods=['POST'])
@requires('admin')
def rates_council_save():
    """Add a council, or correct one. Its telephone number reaches every set of
    particulars for every property in that borough, so a change is audited."""
    form = request.form
    raw_id = (form.get('id') or '').strip()
    council = Council.query.get(int(raw_id)) if raw_id.isdigit() else None
    name = (form.get('name') or '').strip()
    if not name:
        flash('A council needs its official name.', 'error')
        return redirect(url_for('rates_reference'))

    clash = Council.query.filter(Council.name == name).first()
    if clash and (not council or clash.id != council.id):
        flash(f'{name} is already on record.', 'error')
        return redirect(url_for('rates_reference'))

    creating = council is None
    if creating:
        council = Council(name=name)
        db.session.add(council)
    was_phone = council.phone
    council.name = name
    council.short_name = (form.get('short_name') or '').strip() or None
    council.phone = (form.get('phone') or '').strip() or None
    council.email = (form.get('email') or '').strip() or None
    council.website = (form.get('website') or '').strip() or None
    council.address = (form.get('address') or '').strip() or None
    council.verified_on = _parse_date(form.get('verified_on'))
    council.active = bool(form.get('active'))
    db.session.commit()

    detail = f'{name}'
    if not creating and was_phone != council.phone:
        detail += f' telephone changed from {was_phone or "none"} to {council.phone or "none"}'
    audit('council-added' if creating else 'council-edited',
          entity='Council', entity_id=council.id, detail=detail)
    flash(f'{name} saved.', 'success')
    return redirect(url_for('rates_reference'))


@app.route('/admin/rates/multiplier', methods=['POST'])
@requires('admin')
def rates_multiplier_save():
    """Add or correct a multiplier for a tax year.

    Verifying one is a deliberate act: ticking the box records who checked it
    against the official source and when, which is the whole point of holding a
    verification date at all.
    """
    form = request.form
    raw_id = (form.get('id') or '').strip()
    row = RatesMultiplier.query.get(int(raw_id)) if raw_id.isdigit() else None

    tax_year = (form.get('tax_year') or '').strip()
    name = (form.get('name') or '').strip()
    value = br.to_multiplier(form.get('value'))
    problems = []
    if not re.fullmatch(r'\d{4}/\d{2}', tax_year):
        problems.append('a tax year written like 2025/26')
    if not name:
        problems.append('a name')
    if value is None or value <= 0:
        problems.append('a multiplier such as 0.555')
    if problems:
        flash('A multiplier needs ' + ', '.join(problems) + '.', 'error')
        return redirect(url_for('rates_reference'))

    creating = row is None
    if creating:
        row = RatesMultiplier(tax_year=tax_year, name=name, value=value)
        db.session.add(row)
    starts, ends = br.tax_year_bounds(tax_year)
    row.tax_year, row.name, row.value = tax_year, name, value
    row.multiplier_type = (form.get('multiplier_type') or '').strip() or None
    row.category = (form.get('category') or '').strip() or None
    row.rv_min = br.to_pence(form.get('rv_min'))
    row.rv_max = br.to_pence(form.get('rv_max'))
    row.starts_on, row.ends_on = starts, ends
    row.source = (form.get('source') or '').strip() or None
    row.active = bool(form.get('active'))
    if form.get('verified'):
        row.verified_on = _parse_date(form.get('verified_on')) or date.today()
    else:
        row.verified_on = None
    db.session.commit()

    audit('multiplier-added' if creating else 'multiplier-edited',
          entity='RatesMultiplier', entity_id=row.id,
          detail=f'{tax_year} {name} at {br.multiplier_str(value)}'
                 + (' (verified)' if row.verified_on else ' (not verified)'))
    flash(f'{name} for {tax_year} saved.', 'success')
    return redirect(url_for('rates_reference'))


# ── Business rates: calculating, saving and confirming ──────────────────────

def _rates_inputs(form, prop=None):
    """Read a rates form into checked values, and say what is wrong with it.

    Everything is parsed here, on the server, from the posted request. The
    browser's own arithmetic is only there to keep the screen responsive; it is
    never trusted, never read back, and never saved.
    """
    errors = {}
    today = date.today()

    tax_year = (form.get('tax_year') or '').strip() or br.tax_year_of(today)
    if not re.fullmatch(r'\d{4}/\d{2}', tax_year):
        errors['tax_year'] = 'Choose a tax year.'
        tax_year = br.tax_year_of(today)

    rv = br.to_pence(form.get('rateable_value'))
    if form.get('rateable_value', '').strip() and rv is None:
        errors['rateable_value'] = 'That is not an amount.'
    elif rv is not None and rv < 0:
        errors['rateable_value'] = 'A rateable value cannot be negative.'
        rv = None
    elif rv is None:
        errors['rateable_value'] = 'Enter the rateable value.'

    # The multiplier. An id must belong to the chosen year — a request that
    # names one from another year is refused rather than quietly used.
    overridden = bool(form.get('multiplier_override'))
    row = None
    value = None
    if overridden:
        value = br.to_multiplier(form.get('multiplier_value'))
        if value is None:
            errors['multiplier_value'] = 'Enter the multiplier to use.'
        elif value > br.MULTIPLIER_SCALE:
            errors['multiplier_value'] = 'A multiplier is a rate in the pound, such as 0.555.'
            value = None
    else:
        raw = (form.get('multiplier_id') or '').strip()
        if raw.isdigit():
            row = RatesMultiplier.query.get(int(raw))
        if row and (row.tax_year != tax_year or not row.active):
            row = None
        if not row:
            errors['multiplier_id'] = 'Choose a multiplier for this tax year.'
        else:
            value = row.value

    relief_type = (form.get('relief_type') or '').strip() or None
    relief_percent = None
    raw_pct = (form.get('relief_percent') or '').strip()
    if raw_pct:
        try:
            from decimal import Decimal as _D
            relief_percent = _D(raw_pct.replace('%', '').strip())
            if relief_percent < 0 or relief_percent > 100:
                errors['relief_percent'] = 'A relief is between 0 and 100 per cent.'
                relief_percent = None
        except Exception:
            errors['relief_percent'] = 'That is not a percentage.'

    def amount(key, allow_negative=False):
        raw = (form.get(key) or '').strip()
        if not raw:
            return None
        parsed = br.to_pence(raw)
        if parsed is None:
            errors[key] = 'That is not an amount.'
            return None
        if parsed < 0 and not allow_negative:
            errors[key] = 'That cannot be negative.'
            return None
        return parsed

    return {
        'tax_year': tax_year,
        'rateable_value': rv,
        'multiplier_row': row,
        'multiplier_value': value,
        'multiplier_overridden': overridden,
        'override_reason': (form.get('override_reason') or '').strip() or None,
        'relief_type': relief_type,
        'relief_percent': relief_percent,
        'relief_amount': amount('relief_amount'),
        'transitional': amount('transitional', allow_negative=True),
        'supplement': amount('supplement'),
        'supplement_label': (form.get('supplement_label') or '').strip() or None,
        'other_adjustment': amount('other_adjustment', allow_negative=True),
        'notes': (form.get('notes') or '').strip() or None,
    }, errors


def _rates_result(inputs):
    """The calculation, from checked inputs."""
    return br.calculate(
        inputs['rateable_value'], inputs['multiplier_value'],
        relief_percent=inputs['relief_percent'],
        relief_amount_pence=inputs['relief_amount'],
        transitional_pence=inputs['transitional'],
        supplement_pence=inputs['supplement'],
        other_pence=inputs['other_adjustment'])


def _rates_payload(prop, inputs, errors, result):
    """What the calculator screen shows: the breakdown, and the assumptions.

    The assumptions are listed before anything is saved, because a figure this
    is going to put on a brochure should not rest on anything the reader has
    not been shown.
    """
    assumptions = []
    if inputs['relief_percent'] or inputs['relief_amount']:
        assumptions.append(
            f"A {inputs['relief_type'] or 'relief'} has been applied because it was "
            'entered on this form. The CRM has not checked eligibility, which '
            'depends on the occupier and on how many properties they hold.')
    if inputs['supplement']:
        assumptions.append(
            f"A supplement of {br.money(inputs['supplement'])} "
            f"({inputs['supplement_label'] or 'unlabelled'}) has been added because "
            'it was entered on this form. No supplement is applied on its own.')
    if inputs['transitional']:
        assumptions.append(
            f"A transitional adjustment of {br.money(inputs['transitional'])} has "
            'been applied because it was entered on this form. Transitional '
            'arrangements are never applied silently.')
    if inputs['multiplier_overridden']:
        assumptions.append(
            'The multiplier was entered by hand rather than taken from the table '
            'for this tax year.')
    row = inputs['multiplier_row']
    if row and not row.verified_on:
        assumptions.append(
            f'The {row.name} for {row.tax_year} has not been verified against '
            'its official source by anyone in the office.')
    assumptions.append(
        'This is an estimate. It assumes nothing about the occupier’s other '
        'properties or circumstances, and is not a figure from the council.')
    if result and result.get('floored'):
        assumptions.append(
            'The deductions came to more than the liability, so the estimate has '
            'been held at zero rather than shown as a negative bill.')

    return {
        'ok': not errors and result is not None,
        'errors': errors,
        'result': None if not result else {
            'rateable_value': br.money(result['rateable_value']),
            'multiplier': br.multiplier_str(result['multiplier']),
            'multiplier_name': (inputs['multiplier_row'].name
                                if inputs['multiplier_row'] else 'Entered by hand'),
            'multiplier_type': (inputs['multiplier_row'].multiplier_type
                                if inputs['multiplier_row'] else 'Other'),
            'overridden': inputs['multiplier_overridden'],
            'base': br.money(result['base']),
            'relief': br.money(result['relief']) if result['relief'] else None,
            'adjustments': (br.money(result['adjustments'])
                            if result['adjustments'] else None),
            'total': br.money(result['total']),
            'monthly': br.money(result['monthly']),
            'tax_year': inputs['tax_year'],
            'calculated_on': date.today().strftime('%d %b %Y'),
            'lines': [(label, br.money(value)) for label, value in result['lines']],
        },
        'assumptions': assumptions,
    }


@app.route('/properties/<int:id>/rates/calculate', methods=['POST'])
@requires('edit')
def property_rates_calculate(id):
    """Work the figure out, and save nothing.

    The screen calls this on every change so the breakdown always matches the
    inputs. Saving is a separate, deliberate act.
    """
    prop = Property.query.get_or_404(id)
    inputs, errors = _rates_inputs(request.form, prop)
    result = None if errors else _rates_result(inputs)
    return jsonify(_rates_payload(prop, inputs, errors, result))


@app.route('/properties/<int:id>/rates/save', methods=['POST'])
@requires('edit')
def property_rates_save(id):
    """Keep the estimate, having worked it out again here.

    The posted total is ignored entirely. Whatever the browser calculated, the
    figure that is stored is the one this server produced from the inputs.
    """
    prop = Property.query.get_or_404(id)
    inputs, errors = _rates_inputs(request.form, prop)
    result = None if errors else _rates_result(inputs)
    if errors or not result:
        flash('The rates calculation could not be saved: '
              + '; '.join(errors.values()), 'error')
        return _back_to('property_edit', id=prop.id)

    # The previous estimate becomes history rather than being overwritten.
    for old in prop.rates_calculations:
        old.is_current = False

    row = inputs['multiplier_row']
    calc = RatesCalculation(
        property_id=prop.id, is_current=True,
        tax_year=inputs['tax_year'],
        rateable_value=inputs['rateable_value'],
        multiplier_id=row.id if row else None,
        multiplier_value=inputs['multiplier_value'],
        multiplier_name=row.name if row else 'Entered by hand',
        multiplier_type=row.multiplier_type if row else 'Other',
        multiplier_overridden=inputs['multiplier_overridden'],
        override_reason=inputs['override_reason'],
        base_payable=result['base'],
        relief_type=inputs['relief_type'],
        relief_percent=inputs['relief_percent'],
        relief_amount=inputs['relief_amount'],
        transitional=inputs['transitional'],
        supplement=inputs['supplement'],
        supplement_label=inputs['supplement_label'],
        other_adjustment=inputs['other_adjustment'],
        estimated_payable=result['total'],
        notes=inputs['notes'],
        calculated_on=date.today(),
        calculated_by=getattr(current_user, 'username', None))
    db.session.add(calc)
    db.session.commit()

    detail = (f"{inputs['tax_year']} estimate {br.money(result['total'])} "
              f"at {br.multiplier_str(inputs['multiplier_value'])}")
    if inputs['multiplier_overridden']:
        detail += ' (multiplier overridden by hand)'
    audit('rates-calculated', entity='Property', entity_id=prop.id, detail=detail)
    flash(f"Estimated business rates saved: {br.money(result['total'])} "
          f"for {inputs['tax_year']}.", 'success')
    return _back_to('property_edit', id=prop.id)


@app.route('/properties/<int:id>/rates/suggest')
@requires('view')
def property_rates_suggest(id):
    """What the CRM would propose for a tax year and rateable value."""
    prop = Property.query.get_or_404(id)
    tax_year = (request.args.get('tax_year') or br.tax_year_of(date.today())).strip()
    rv = br.to_pence(request.args.get('rateable_value'))
    pick = suggest_multiplier_for(tax_year, rv, prop.property_type)
    rows = multiplier_rows(tax_year)
    return jsonify({
        'suggested_id': pick['id'] if pick else None,
        'why': pick['why'] if pick else None,
        'verified': bool(pick and pick.get('verified_on')) if pick else False,
        'options': [{'id': r['id'], 'name': r['name'],
                     'type': r['multiplier_type'],
                     'value': br.multiplier_str(r['value']),
                     'verified': bool(r['verified_on'])} for r in rows],
    })


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

# ── Transactions: the firm's figures ─────────────────────────────────────────
# Everything on the Transactions page is worked out here, from the records
# themselves, every time the page is asked for. Nothing is cached and no total
# is written back to the database, so adding, editing, completing or deleting
# a transaction changes the dashboard on the next load with nothing to refresh.


def _month_start(d):
    return date(d.year, d.month, 1)


def _month_shift(d, months):
    """The first of the month `months` away from d, forwards or backwards."""
    total = (d.year * 12 + d.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def _in_month(when, first_of_month):
    """Whether a date falls in that calendar month. Missing dates never do."""
    return bool(when) and first_of_month <= when < _month_shift(first_of_month, 1)


def _change(now, before):
    """How this month compares with last, honestly.

    A percentage against nothing is meaningless, so a month following a zero
    says so rather than showing an invented rise.
    """
    now, before = float(now or 0), float(before or 0)
    if before == 0:
        if now == 0:
            return {'kind': 'none', 'pct': None,
                    'label': 'No previous-month comparison'}
        return {'kind': 'new', 'pct': None, 'label': 'New'}
    pct = (now - before) / abs(before) * 100.0
    if abs(pct) < 0.05:
        return {'kind': 'flat', 'pct': 0.0, 'label': 'No change'}
    kind = 'up' if pct > 0 else 'down'
    return {'kind': kind, 'pct': round(pct, 1),
            'label': f'{abs(pct):,.1f}% on last month'}


def counting_transactions():
    """Every transaction that belongs in the figures.

    Fallen-through and archived transactions are dropped here, once, so no
    total further down can accidentally include them.
    """
    return [t for t in Transaction.query.options(
        db.joinedload(Transaction.payments)).all() if t.counts_towards_totals]


def transaction_dashboard(rows=None):
    """The KPI cards. Real records only — every figure traces to a row."""
    rows = counting_transactions() if rows is None else rows
    this_month = _month_start(date.today())
    last_month = _month_shift(this_month, -1)

    completed = [t for t in rows if t.has_completed]
    billed_rows = [t for t in rows if t.is_billed]

    def completed_in(m):
        return [t for t in completed if _in_month(t.completion_date, m)]

    def billed_in(m):
        return sum(t.net_commission for t in billed_rows
                   if _in_month(t.invoice_date, m))

    billed_total = sum(t.net_commission for t in billed_rows)
    received_total = sum(t.commission_received for t in billed_rows)
    completed_now = completed_in(this_month)
    completed_before = completed_in(last_month)
    billed_now = billed_in(this_month)

    return {
        'total_count':      len(rows),
        'excluded_count':   Transaction.query.count() - len(rows),
        'completed_count':  len(completed),
        'completed_month':  len(completed_now),
        'completed_change': _change(len(completed_now), len(completed_before)),
        'billed_total':     round(billed_total, 2),
        'billed_month':     round(billed_now, 2),
        'billed_change':    _change(billed_now, billed_in(last_month)),
        'received_total':   round(received_total, 2),
        'outstanding_total': round(billed_total - received_total, 2),
        'overdue_total':    round(sum(t.outstanding for t in billed_rows
                                      if t.is_overdue), 2),
        'value_total':      round(sum(t.commission_basis for t in rows), 2),
        'value_completed':  round(sum(t.commission_basis for t in completed), 2),
        'avg_commission':   round(sum(t.net_commission for t in completed)
                                  / len(completed), 2) if completed else None,
        'this_month_label': this_month.strftime('%B %Y'),
        'last_month_label': last_month.strftime('%B %Y'),
    }


# How much of a deal's fee to count while it is still in progress. A deal with
# solicitors instructed is far likelier to complete than one just agreed, and
# counting either at its full value would overstate what is coming in. Change
# these figures to match how the office actually converts.
PIPELINE_WEIGHTS = {
    'Draft': 0.0,
    'In Progress': 0.10,
    'Terms Agreed': 0.50,
    'Solicitors Instructed': 0.80,
}


def _annual_value(price, unit):
    """A listing's asking figure as a yearly sum, so fees compare like for like.

    A sale price is the sum itself; a rent per calendar month becomes a year's
    rent; anything else is already yearly. Price on application is worth
    nothing here, because there is no price to charge a fee on.
    """
    if not price:
        return 0.0
    unit = (unit or '').lower()
    if unit == 'poa':
        return 0.0
    if unit == 'pcm':
        return float(price) * 12
    return float(price)


def stock_fee_value():
    """What the fee on everything currently available would come to.

    Only stock carrying a fee is valued. An instruction with no fee recorded
    is counted separately and left out of the total, rather than being valued
    at nothing or at a rate nobody agreed to.
    """
    # A listing with no instruction behind it is still a unit on the market, so
    # it is counted as stock and reported as unpriced rather than dropped.
    available = [
        lst for lst in Listing.query.filter(Listing.listing_status == 'available').all()
        if lst.project is None
        or lst.project.instruction_type in (INSTRUCTION_FOR_SALE, INSTRUCTION_TO_LET)
    ]
    fee_total = asking_total = 0.0
    valued = no_fee = 0
    sale_fee = let_fee = 0.0
    for lst in available:
        project = lst.project
        value = _annual_value(lst.listing_price, lst.listing_price_unit)
        asking_total += value
        if project is None:
            no_fee += 1
            continue
        if project.fee_fixed:
            fee = float(project.fee_fixed)
        elif project.fee_percent and value:
            fee = value * float(project.fee_percent) / 100.0
        else:
            no_fee += 1
            continue
        fee_total += fee
        valued += 1
        if project.instruction_type == INSTRUCTION_FOR_SALE:
            sale_fee += fee
        else:
            let_fee += fee
    return {
        'fee_total': round(fee_total, 2),
        'sale_fee': round(sale_fee, 2),
        'let_fee': round(let_fee, 2),
        'asking_total': round(asking_total, 2),
        'stock_count': len(available),
        'valued': valued,
        'no_fee': no_fee,
    }


def transaction_extras(rows, everyone):
    """The figures behind the headline ones: pipeline, speed and conversion.

    `rows` is what counts towards the totals; `everyone` includes the deals
    that fell through, which conversion needs in order to mean anything.
    """
    # ── Value done, split by what kind of deal it was ──
    sales = [t for t in rows if t.transaction_type == 'Capital']
    lettings = [t for t in rows if t.transaction_type != 'Capital']

    def summarise(group):
        done = [t for t in group if t.has_completed]
        return {
            'count': len(group),
            'value': round(sum(t.commission_basis for t in group), 2),
            'completed': len(done),
            'completed_value': round(sum(t.commission_basis for t in done), 2),
            'commission': round(sum(t.net_commission for t in done), 2),
        }

    # ── Deals still in play, discounted by how far along they are ──
    pipeline = weighted = 0.0
    in_play = 0
    for t in rows:
        weight = PIPELINE_WEIGHTS.get(t.status)
        if weight is None or t.has_completed:
            continue
        in_play += 1
        pipeline += t.net_commission
        weighted += t.net_commission * weight

    # ── How long things take ──
    def started(t):
        """When the clock started: the instruction, or the deal itself."""
        if t.project and t.project.instruction_date:
            return t.project.instruction_date
        return t.transaction_date

    spans = [(t.completion_date - started(t)).days for t in rows
             if t.has_completed and started(t) and t.completion_date
             and t.completion_date >= started(t)]
    waits = [(p.received_on - t.invoice_date).days
             for t in rows for p in t.payments
             if t.invoice_date and p.received_on and p.received_on >= t.invoice_date]

    # ── Won against lost ──
    lost = [t for t in everyone if t.status == 'Fallen Through']
    settled = len([t for t in rows if t.has_completed]) + len(lost)

    # ── Who did it ──
    board = {}
    for t in rows:
        who = fee_earner_name(t.fee_earner_id, t.fee_earner)
        if not who:
            continue
        row = board.setdefault(who, {
            'name': who, 'completed': 0, 'billed': 0.0,
            'received': 0.0, 'live': 0})
        if t.has_completed:
            row['completed'] += 1
        else:
            row['live'] += 1
        if t.is_billed:
            row['billed'] += t.net_commission
            row['received'] += t.commission_received
    for row in board.values():
        row['billed'] = round(row['billed'], 2)
        row['received'] = round(row['received'], 2)

    return {
        'sales': summarise(sales),
        'lettings': summarise(lettings),
        'pipeline': round(pipeline, 2),
        'weighted': round(weighted, 2),
        'in_play': in_play,
        'weights': sorted(PIPELINE_WEIGHTS.items(), key=lambda kv: kv[1]),
        'days_to_complete': round(sum(spans) / len(spans)) if spans else None,
        'completions_measured': len(spans),
        'days_to_paid': round(sum(waits) / len(waits)) if waits else None,
        'payments_measured': len(waits),
        'lost_count': len(lost),
        'conversion': round(len([t for t in rows if t.has_completed])
                            / settled * 100, 1) if settled else None,
        'board': sorted(board.values(), key=lambda r: -r['billed']),
    }


TRANSACTION_PERIODS = [('month', 'Month'), ('quarter', 'Quarter'), ('year', 'Year')]


def _bucket_of(when, period):
    """Which chart column a date belongs in: (sort key, label)."""
    if not when:
        return None
    if period == 'year':
        return (when.year, 0), str(when.year)
    if period == 'quarter':
        q = (when.month - 1) // 3 + 1
        return (when.year, q), f'Q{q} {when.year}'
    return (when.year, when.month), when.strftime('%b %y')


def _bucket_keys(period, ahead=0):
    """The columns to draw, oldest first.

    Money already earned is behind us, so those views look back. Commission
    still expected is ahead of us, so that view gives most of its columns to
    the months to come. `ahead` is how many of them fall after the one we are
    in; the number of columns stays the same either way.
    """
    today = date.today()
    if period == 'year':
        span, step = 5, lambda i: date(today.year + i, 1, 1)
    elif period == 'quarter':
        span, step = 8, lambda i: _month_shift(_month_start(today), 3 * i)
    else:
        span, step = 12, lambda i: _month_shift(_month_start(today), i)
    ahead = max(0, min(ahead, span - 1))
    offsets = range(ahead - span + 1, ahead + 1)
    seen, out = set(), []
    for i in offsets:
        key = _bucket_of(step(i), period)
        if key and key[0] not in seen:
            seen.add(key[0])
            out.append(key)
    return out

# ── The three ways of looking at the chart ───────────────────────────────────

TRANSACTION_VIEWS = [
    ('expected', 'Expected Commission'),
    ('count',    'Number of Transactions'),
    ('target',   'Performance Against Target'),
]

# Where commission is expected to come from. A transaction sits in exactly one
# of these, checked in this order, so nothing is counted twice.
EXPECTED_STAGES = [
    ('terms',      'Terms Agreed',                 '#9fb0cb'),
    ('solicitors', 'Solicitors Instructed',        '#5b7bb0'),
    ('awaiting',   'Completed, awaiting invoice',  '#26406e'),
    ('invoiced',   'Invoiced, awaiting payment',   '#b5762c'),
]

# Where a transaction has got to. Part paid sits with commission billed: the
# invoice is out, so that is the stage it has reached.
COUNT_STAGES = [
    ('terms',      'Terms Agreed',          '#9fb0cb'),
    ('solicitors', 'Solicitors Instructed', '#5b7bb0'),
    ('completed',  'Completed',             '#26406e'),
    ('billed',     'Commission Billed',     '#b5762c'),
    ('paid',       'Paid',                  '#2f7a4f'),
    ('fallen',     'Fallen Through',        '#b3463c'),
]

COUNT_STATUS_STAGE = {
    'Terms Agreed': 'terms', 'Solicitors Instructed': 'solicitors',
    'Completed': 'completed', 'Commission Billed': 'billed',
    'Part Paid': 'billed', 'Paid': 'paid', 'Fallen Through': 'fallen',
}

# How close to target still counts as close.
TARGET_NEAR = float(os.environ.get('TARGET_NEAR', '85'))


def expected_stage(t):
    """The one stage a transaction's commission is still expected in.

    Nothing that fell through, was archived or has been paid in full is
    expected to earn again, so those return nothing at all.
    """
    if not t.counts_towards_totals or t.status == 'Paid':
        return None
    if t.is_billed and t.outstanding > 0.005:
        return 'invoiced'
    if t.has_completed and not t.is_billed:
        return 'awaiting'
    if t.status == 'Solicitors Instructed':
        return 'solicitors'
    if t.status == 'Terms Agreed':
        return 'terms'
    return None


def expected_date(t, stage):
    """When the money for that stage is expected to be due."""
    if stage == 'invoiced':
        return t.payment_due_date or t.invoice_date
    if stage == 'awaiting':
        return t.completion_date
    return t.expected_completion_date or t.completion_date


def expected_amount(t, stage):
    """What is still expected from it, net of VAT.

    An invoice part paid is only expected to bring in what is left, and never
    more than the commission itself, so VAT already collected is not counted
    as commission still to come.
    """
    if stage == 'invoiced':
        return round(min(t.outstanding, t.net_commission), 2)
    return t.net_commission


def count_stage(t):
    return COUNT_STATUS_STAGE.get(t.status)


def count_date(t, stage):
    """The date that put a transaction into the stage it is in."""
    if stage == 'paid':
        paid = [p.received_on for p in t.payments if p.received_on]
        return max(paid) if paid else (t.invoice_date or t.completion_date)
    if stage == 'billed':
        return t.invoice_date or t.completion_date
    if stage == 'completed':
        return t.completion_date
    return (t.expected_completion_date or t.completion_date
            or t.transaction_date
            or (t.created_at.date() if t.created_at else None))


def bucket_key_text(key, period):
    """A period as text, for a link back to the transactions behind a column."""
    year, second = key
    if period == 'year':
        return str(year)
    if period == 'quarter':
        return f'{year}-Q{second}'
    return f'{year}-{second:02d}'


def _stack(columns, stages, peak):
    """Turn each column's per-stage figures into stacked segment heights."""
    for col in columns:
        running = 0.0
        col['segments'] = []
        for key, label, colour in stages:
            value = col['stages'][key]['value']
            if not value:
                continue
            height = value / peak * 100 if peak else 0
            col['segments'].append({
                'key': key, 'label': label, 'colour': colour,
                'value': value, 'count': col['stages'][key]['count'],
                'height': round(height, 2), 'bottom': round(running, 2),
            })
            running += height
    return columns


# How much of the expected-commission chart is given to the months to come.
EXPECTED_AHEAD = {'month': 8, 'quarter': 5, 'year': 3}


def expected_commission_chart(rows, period='month'):
    """Commission still expected, by the stage it is expected from."""
    keys = _bucket_keys(period, ahead=EXPECTED_AHEAD.get(period, 8))
    order = {k: i for i, (k, _) in enumerate(keys)}
    columns = [{'key': k, 'label': lbl, 'period_key': bucket_key_text(k, period),
                'total': 0.0, 'count': 0,
                'stages': {s: {'value': 0.0, 'count': 0} for s, _, _ in EXPECTED_STAGES}}
               for k, lbl in keys]
    undated = in_view = 0
    for t in rows:
        stage = expected_stage(t)
        if not stage:
            continue
        when = expected_date(t, stage)
        if not when:
            undated += 1
            continue
        b = _bucket_of(when, period)
        if not b or b[0] not in order:
            continue
        col = columns[order[b[0]]]
        col['stages'][stage]['value'] += expected_amount(t, stage)
        col['stages'][stage]['count'] += 1
        col['total'] += expected_amount(t, stage)
        col['count'] += 1
        in_view += 1
    for col in columns:
        col['total'] = round(col['total'], 2)
        for st in col['stages'].values():
            st['value'] = round(st['value'], 2)
    peak = max([c['total'] for c in columns] + [0.0])
    return {
        'view': 'expected', 'period': period, 'money': True,
        'columns': _stack(columns, EXPECTED_STAGES, peak),
        'stages': EXPECTED_STAGES, 'peak': peak,
        'total': round(sum(c['total'] for c in columns), 2),
        'count': in_view, 'undated': undated,
        'axis': [money_short(peak * f) for f in (1, 0.75, 0.5, 0.25)],
        'zero': '£0',
    }


# Two of the counting stages are placed by a date that has not happened yet,
# so this view reaches a little way forward as well.
COUNT_AHEAD = {'month': 3, 'quarter': 2, 'year': 1}


def transaction_count_chart(rows, period='month'):
    """How many transactions sit at each stage, period by period."""
    keys = _bucket_keys(period, ahead=COUNT_AHEAD.get(period, 3))
    order = {k: i for i, (k, _) in enumerate(keys)}
    columns = [{'key': k, 'label': lbl, 'period_key': bucket_key_text(k, period),
                'total': 0, 'count': 0,
                'stages': {s: {'value': 0, 'count': 0} for s, _, _ in COUNT_STAGES}}
               for k, lbl in keys]
    undated = elsewhere = 0
    for t in rows:
        stage = count_stage(t)
        if not stage:
            elsewhere += 1
            continue
        when = count_date(t, stage)
        if not when:
            undated += 1
            continue
        b = _bucket_of(when, period)
        if not b or b[0] not in order:
            continue
        col = columns[order[b[0]]]
        col['stages'][stage]['value'] += 1
        col['stages'][stage]['count'] += 1
        col['total'] += 1
        col['count'] += 1
    peak = max([c['total'] for c in columns] + [0])
    return {
        'view': 'count', 'period': period, 'money': False,
        'columns': _stack(columns, COUNT_STAGES, peak),
        'stages': COUNT_STAGES, 'peak': peak,
        'total': sum(c['total'] for c in columns),
        'count': sum(c['total'] for c in columns),
        'undated': undated, 'elsewhere': elsewhere,
        'axis': [f'{round(peak * f):,}' for f in (1, 0.75, 0.5, 0.25)],
        'zero': '0',
    }


def target_for(period, key):
    """The target for one column, or None if nobody has set one.

    A quarter or a year with no figure of its own is added up from the months
    inside it, so entering monthly targets is enough on its own. Nothing is
    ever invented: with no months entered either, there is no target.
    """
    year, second = key
    own = CommissionTarget.query.filter_by(
        period_type=period, period_key=bucket_key_text(key, period)).first()
    if own:
        return float(own.amount), True
    if period == 'month':
        return None, False
    months = range((second - 1) * 3 + 1, (second - 1) * 3 + 4) if period == 'quarter' \
        else range(1, 13)
    parts = [t.amount for t in CommissionTarget.query.filter(
        CommissionTarget.period_type == 'month',
        CommissionTarget.period_key.in_([f'{year}-{m:02d}' for m in months])).all()]
    if parts:
        return round(float(sum(parts)), 2), False
    if period == 'year':
        quarters = [t.amount for t in CommissionTarget.query.filter(
            CommissionTarget.period_type == 'quarter',
            CommissionTarget.period_key.in_([f'{year}-Q{q}' for q in range(1, 5)])).all()]
        if quarters:
            return round(float(sum(quarters)), 2), False
    return None, False


def target_performance_chart(rows, period='month'):
    """What was secured and billed against what the office aimed at."""
    keys = _bucket_keys(period)
    order = {k: i for i, (k, _) in enumerate(keys)}
    columns = [{'key': k, 'label': lbl, 'period_key': bucket_key_text(k, period),
                'secured': 0.0, 'billed': 0.0, 'secured_count': 0, 'billed_count': 0}
               for k, lbl in keys]
    for t in rows:
        if t.has_completed:
            b = _bucket_of(t.completion_date, period)
            if b and b[0] in order:
                columns[order[b[0]]]['secured'] += t.net_commission
                columns[order[b[0]]]['secured_count'] += 1
        if t.is_billed:
            b = _bucket_of(t.invoice_date, period)
            if b and b[0] in order:
                columns[order[b[0]]]['billed'] += t.net_commission
                columns[order[b[0]]]['billed_count'] += 1

    any_target = False
    for col in columns:
        col['secured'] = round(col['secured'], 2)
        col['billed'] = round(col['billed'], 2)
        target, exact = target_for(period, col['key'])
        col['target'] = target
        col['target_is_own'] = exact
        any_target = any_target or target is not None
        if target:
            col['achieved'] = round(col['secured'] / target * 100, 1)
            col['variance'] = round(col['secured'] - target, 2)
            col['state'] = ('over' if col['achieved'] >= 100
                            else 'near' if col['achieved'] >= TARGET_NEAR else 'under')
        else:
            col['achieved'] = col['variance'] = None
            col['state'] = 'none'

    peak = max([max(c['secured'], c['billed'], c['target'] or 0.0) for c in columns] + [0.0])
    for col in columns:
        col['secured_h'] = round(col['secured'] / peak * 100, 2) if peak else 0
        col['billed_h'] = round(col['billed'] / peak * 100, 2) if peak else 0
        col['target_h'] = round(col['target'] / peak * 100, 2) if peak and col['target'] else None

    totals_target = sum(c['target'] for c in columns if c['target'])
    secured = round(sum(c['secured'] for c in columns), 2)
    return {
        'view': 'target', 'period': period, 'money': True, 'columns': columns,
        'peak': peak, 'any_target': any_target,
        'total': secured,
        'total_billed': round(sum(c['billed'] for c in columns), 2),
        'total_target': round(totals_target, 2) if totals_target else None,
        'total_achieved': round(secured / totals_target * 100, 1) if totals_target else None,
        'total_variance': round(secured - totals_target, 2) if totals_target else None,
        'near': TARGET_NEAR,
        'axis': [money_short(peak * f) for f in (1, 0.75, 0.5, 0.25)],
        'zero': '£0',
    }


def transaction_chart(rows=None, period='month', view='expected', everyone=None):
    """The dashboard chart, in whichever of the three views was asked for.

    The counting view has a Fallen through stage, so it is given every
    transaction. The two money views are given only the ones that count,
    because nothing that fell through will ever be billed.
    """
    period = period if period in dict(TRANSACTION_PERIODS) else 'month'
    view = view if view in dict(TRANSACTION_VIEWS) else 'expected'
    rows = counting_transactions() if rows is None else rows
    if view == 'count':
        return transaction_count_chart(everyone if everyone is not None else rows, period)
    if view == 'target':
        return target_performance_chart(rows, period)
    return expected_commission_chart(rows, period)


def next_transaction_reference():
    """The next TR-0001. Falls forward past anything already used."""
    highest = 0
    for (ref,) in db.session.query(Transaction.reference).filter(
            Transaction.reference.isnot(None)).all():
        digits = ''.join(ch for ch in str(ref) if ch.isdigit())
        if digits:
            highest = max(highest, int(digits))
    return f'TR-{highest + 1:04d}'




TRANSACTION_STATUS_CLASS = {
    'Draft': 'st-draft', 'In Progress': 'st-progress', 'Terms Agreed': 'st-terms',
    'Solicitors Instructed': 'st-solicitors', 'Completed': 'st-completed',
    'Commission Billed': 'st-billed', 'Part Paid': 'st-part', 'Paid': 'st-paid',
    'Fallen Through': 'st-fallen', 'Archived': 'st-archived',
}
app.jinja_env.globals['status_class'] = \
    lambda st: TRANSACTION_STATUS_CLASS.get(st, 'st-draft')
app.jinja_env.globals['PROPERTY_TYPES'] = PROPERTY_TYPES
app.jinja_env.globals['property_type_options'] = property_type_options
def can_edit():
    """Whether the person looking may change records.

    A global rather than a template variable, because an imported macro does
    not inherit the page's context and would otherwise have no way to ask.
    """
    try:
        return bool(current_user.is_authenticated and current_user.can('edit'))
    except Exception:
        return False


app.jinja_env.globals['can_edit'] = can_edit
app.jinja_env.globals['property_photographs'] = property_photographs
app.jinja_env.globals['gallery_photos'] = gallery_photos
def zoopla_summary_limit():
    """The character limit Zoopla applies to the summary field."""
    try:
        import zoopla_feed as zf
        return zf.SUMMARY_LIMIT
    except Exception:
        return 2000


app.jinja_env.globals['zoopla_summary_limit'] = zoopla_summary_limit
app.jinja_env.globals['contact_label'] = contact_label
app.jinja_env.globals['fee_earners'] = fee_earners
app.jinja_env.globals['fee_earner_name'] = fee_earner_name
app.jinja_env.globals['ORG_TYPES'] = ORG_TYPES
app.jinja_env.globals['ORG_STATUSES'] = ORG_STATUSES
app.jinja_env.globals['ORG_STATUS_NAMES'] = ORG_STATUS_NAMES
app.jinja_env.globals['ORG_ROLES'] = ORG_ROLES
app.jinja_env.globals['ORG_ROLE_NAMES'] = ORG_ROLE_NAMES
app.jinja_env.globals['money_gbp'] = money_gbp
app.jinja_env.globals['money_short'] = money_short
app.jinja_env.globals['TRANSACTION_STATUSES'] = TRANSACTION_STATUSES
STAGE_CLASS = {
    'Draft': 'st-draft', 'In Progress': 'st-progress', 'Under Offer': 'st-offer',
    'Terms Agreed': 'st-terms', 'Solicitors Instructed': 'st-solicitors',
    'Completed': 'st-completed', 'Fallen Through': 'st-fallen',
    'Archived': 'st-archived',
}
PAYMENT_CLASS = {
    'Not Billed': 'st-notbilled', 'Commission Billed': 'st-billed',
    'Part Paid': 'st-part', 'Paid': 'st-paid',
}
app.jinja_env.globals['stage_class'] = lambda v: STAGE_CLASS.get(v, 'st-draft')
app.jinja_env.globals['payment_class'] = lambda v: PAYMENT_CLASS.get(v, 'st-draft')
app.jinja_env.globals['TRANSACTION_STAGES'] = TRANSACTION_STAGES
app.jinja_env.globals['PAYMENT_STATES'] = PAYMENT_STATES
app.jinja_env.globals['AGREEMENT_TYPES'] = AGREEMENT_TYPES
app.jinja_env.globals['TRANSACTION_PERIODS'] = TRANSACTION_PERIODS
app.jinja_env.globals['VAT_RATE_DEFAULT'] = VAT_RATE_DEFAULT


# Columns the table can be ordered by. Money and balances are worked out per
# record rather than held in a column, so they are sorted in the same pass
# that renders them rather than by the database.
TRANSACTION_SORTS = {
    'reference':   lambda t: (t.reference or '').lower(),
    'address':     lambda t: (t.property.address if t.property else '').lower(),
    'client':      lambda t: (t.client or '').lower(),
    'counterparty': lambda t: (t.counterparty or '').lower(),
    'type':        lambda t: (t.transaction_type or '').lower(),
    'fee_earner':  lambda t: (fee_earner_name(t.fee_earner_id, t.fee_earner) or '').lower(),
    'status':      lambda t: (t.status or '').lower(),
    'value':       lambda t: t.commission_basis,
    'fee':         lambda t: (t.fee_percent or 0.0) if t.fee_type != 'Fixed' else (t.fixed_fee or 0.0),
    'commission':  lambda t: t.net_commission,
    'vat':         lambda t: t.vat_amount,
    'invoice':     lambda t: t.total_invoice,
    'received':    lambda t: t.commission_received,
    'outstanding': lambda t: t.outstanding,
    'invoice_date':     lambda t: t.invoice_date or date.min,
    'payment_due_date': lambda t: t.payment_due_date or date.min,
    'completion_date':  lambda t: t.completion_date or date.min,
}


def _transaction_filters(rows, args):
    """Narrow the list to what was asked for. Every filter is applied here."""
    q = (args.get('q') or '').strip().lower()
    if q:
        def hit(t):
            fields = [t.reference, t.client, t.counterparty, t.tenant,
                      t.purchaser, t.vendor, t.landlord, t.invoice_number,
                      t.property.address if t.property else None,
                      t.property.postcode if t.property else None]
            return any(q in (v or '').lower() for v in fields)
        rows = [t for t in rows if hit(t)]
    for key, get in (('type', lambda t: t.transaction_type),
                     ('status', lambda t: t.status),
                     ('fee_earner_id', lambda t: t.fee_earner_id),
                     ('client', lambda t: t.client)):
        want = (args.get(key) or '').strip()
        if want:
            # Compared as text, because a fee earner is an id and a status is
            # a word, and both arrive from the query string as strings.
            rows = [t for t in rows if str(get(t) or '') == want]
    prop_id = (args.get('property') or '').strip()
    if prop_id.isdigit():
        rows = [t for t in rows if t.property_id == int(prop_id)]
    # Clicking a chart segment asks for exactly what that segment counted, so
    # the same stage and period rules are used rather than an approximation.
    stage, bucket = (args.get('stage') or '').strip(), (args.get('bucket') or '').strip()
    if stage and bucket:
        period = args.get('period') if args.get('period') in dict(TRANSACTION_PERIODS) else 'month'
        picker = expected_stage if args.get('view') == 'expected' else count_stage
        dater = expected_date if args.get('view') == 'expected' else count_date

        def in_segment(t):
            got = picker(t)
            if got != stage:
                return False
            when = dater(t, got)
            b = _bucket_of(when, period) if when else None
            return bool(b) and bucket_key_text(b[0], period) == bucket
        rows = [t for t in rows if in_segment(t)]

    frm, to = _parse_date(args.get('from')), _parse_date(args.get('to'))
    if frm or to:
        def when(t):
            return t.completion_date or t.invoice_date or t.transaction_date
        rows = [t for t in rows
                if when(t) and (not frm or when(t) >= frm) and (not to or when(t) <= to)]
    return rows


@app.route('/transactions')
def transactions_list():
    """The Transactions page: the firm's figures, then the detail behind them.

    The dashboard is worked out from every transaction that counts, so it
    reports the whole book. The table below shows whatever the filters ask
    for — narrowing the table never changes the figures above it.
    """
    # Read the book once. Everything below works from this one list, so the
    # cards, the chart and the table can never disagree with each other.
    everyone = Transaction.query.options(
        db.joinedload(Transaction.payments),
        db.joinedload(Transaction.property)).all()
    rows = [t for t in everyone if t.counts_towards_totals]
    dash = transaction_dashboard(rows)
    dash['excluded_count'] = len(everyone) - len(rows)
    extras = transaction_extras(rows, everyone)
    stock = stock_fee_value()

    # The chart answers the filters; the cards above stay firm-wide, so the
    # summary always reports the whole book however the chart is narrowed.
    chart_args = {k: v for k, v in request.args.items()
                  if k not in ('stage', 'bucket', 'sort', 'dir')}
    chart_rows = _transaction_filters(rows, chart_args)
    chart_everyone = _transaction_filters(everyone, chart_args)
    chart = transaction_chart(chart_rows, request.args.get('period', 'month'),
                              request.args.get('view', 'expected'),
                              everyone=chart_everyone)
    filtered = any(chart_args.get(k) for k in
                   ('q', 'type', 'status', 'fee_earner_id', 'client', 'property', 'from', 'to'))

    # Archived and fallen-through transactions are outside the figures, but
    # somebody still has to be able to find them.
    show_all = request.args.get('status') in TRANSACTION_EXCLUDED \
        or request.args.get('show') == 'all'
    listed = everyone if show_all else rows

    listed = _transaction_filters(listed, request.args)
    sort = request.args.get('sort') or 'completion_date'
    direction = 'asc' if request.args.get('dir') == 'asc' else 'desc'
    key = TRANSACTION_SORTS.get(sort) or TRANSACTION_SORTS['completion_date']
    listed = sorted(listed, key=key, reverse=(direction == 'desc'))

    return render_template(
        'transactions/list.html',
        transactions=listed, dash=dash, chart=chart, extras=extras, stock=stock,
        view=chart['view'], views=TRANSACTION_VIEWS, filtered=filtered,
        segment=(request.args.get('stage'), request.args.get('bucket')),
        sort=sort, dir=direction, args=request.args, today=date.today(),

        clients=sorted({t.client for t in everyone if t.client}),
        properties=Property.query.order_by(Property.address).all(),
        listed_totals={
            'count': len(listed),
            'commission': round(sum(t.net_commission for t in listed
                                    if t.counts_towards_totals), 2),
            'received': round(sum(t.commission_received for t in listed
                                  if t.counts_towards_totals), 2),
            'outstanding': round(sum(t.outstanding for t in listed
                                     if t.is_billed), 2),
        })


@app.route('/transactions/targets', methods=['GET', 'POST'])
@requires('edit')
def transaction_targets():
    """Set what the office is aiming to bill, month by month and year by year.

    A quarter with no figure of its own is added up from its months, so most
    offices only need to fill in the monthly column.
    """
    year = request.args.get('year', type=int) or date.today().year
    if request.method == 'POST':
        year = request.form.get('year', type=int) or year
        saved = cleared = 0
        for kind, keys in (('month', [f'{year}-{m:02d}' for m in range(1, 13)]),
                           ('quarter', [f'{year}-Q{q}' for q in range(1, 5)]),
                           ('year', [str(year)])):
            for key in keys:
                field = f'{kind}:{key}'
                if field not in request.form:
                    continue
                amount = _fnum(request.form.get(field))
                existing = CommissionTarget.query.filter_by(
                    period_type=kind, period_key=key).first()
                if amount is None or amount < 0:
                    if existing:
                        db.session.delete(existing)
                        cleared += 1
                    continue
                if existing:
                    existing.amount = float(amount)
                    existing.set_by = getattr(current_user, 'username', None)
                    existing.set_at = datetime.utcnow()
                else:
                    db.session.add(CommissionTarget(
                        period_type=kind, period_key=key, amount=float(amount),
                        set_by=getattr(current_user, 'username', None)))
                saved += 1
        db.session.commit()
        audit('edit', entity='CommissionTarget', entity_id=year,
              detail=f'{saved} set, {cleared} cleared')
        flash(f'Targets saved for {year}.', 'success')
        return redirect(url_for('transaction_targets', year=year))

    held = {f'{t.period_type}:{t.period_key}': t.amount for t in
            CommissionTarget.query.filter(
                CommissionTarget.period_key.startswith(str(year))).all()}
    months = [{'key': f'{year}-{m:02d}',
               'label': date(year, m, 1).strftime('%B'),
               'amount': held.get(f'month:{year}-{m:02d}'),
               'quarter': (m - 1) // 3 + 1} for m in range(1, 13)]
    quarters = []
    for q in range(1, 5):
        own = held.get(f'quarter:{year}-Q{q}')
        from_months = [m['amount'] for m in months if m['quarter'] == q and m['amount']]
        quarters.append({'key': f'{year}-Q{q}', 'label': f'Quarter {q}', 'amount': own,
                         'from_months': round(sum(from_months), 2) if from_months else None})
    entered = [m['amount'] for m in months if m['amount']]
    return render_template(
        'transactions/targets.html', year=year, months=months, quarters=quarters,
        year_target=held.get(f'year:{year}'),
        months_total=round(sum(entered), 2) if entered else None,
        years=range(date.today().year - 2, date.today().year + 3))


@app.route('/transactions/<int:id>')
def transaction_detail(id):
    """One transaction, on its own page, with the list left behind."""
    t = Transaction.query.get_or_404(id)
    history = AuditLog.query.filter_by(entity='Transaction', entity_id=str(id)) \
        .order_by(AuditLog.at.desc()).limit(30).all()
    return render_template(
        'transactions/detail.html', t=t, today=date.today(), history=history,
        payments=sorted(t.payments, key=lambda p: p.received_on or date.min,
                        reverse=True),
        documents=sorted(t.documents, key=lambda d: d.uploaded_at or datetime.min,
                         reverse=True),
        properties=Property.query.order_by(Property.address).all(),
        projects=Project.query.order_by(Project.name).all())


@app.route('/transactions/<int:id>/save', methods=['POST'])
@requires('edit')
def transaction_save(id):
    """Save the transaction page. Only the fields the page sent are written."""
    t = Transaction.query.get_or_404(id)
    form = request.form

    agreement = (form.get('agreement_type') or '').strip()
    if agreement and agreement not in AGREEMENT_TYPES:
        flash(f'"{agreement}" is not an agreement type, so it was not saved.', 'error')
        form = {k: v for k, v in form.items() if k != 'agreement_type'}

    status = (form.get('status') or '').strip()
    if status and status not in TRANSACTION_STATUSES:
        flash(f'"{status}" is not a transaction status, so it was not saved.', 'error')
        form = {k: v for k, v in form.items() if k != 'status'}

    if 'property_id' in form and (form.get('property_id') or '').isdigit():
        t.property_id = int(form['property_id'])
    if 'project_id' in form:
        pid = (form.get('project_id') or '').strip()
        t.project_id = int(pid) if pid.isdigit() else None

    before = (t.status, t.completion_date, t.net_commission)
    apply_form_fields(t, form, TRANSACTION_FIELDS)
    if not t.reference:
        t.reference = next_transaction_reference()
    db.session.commit()

    changed = []
    if before[0] != t.status:
        changed.append(f'status {before[0]} to {t.status}')
    if before[1] != t.completion_date:
        changed.append('completion date')
    if before[2] != t.net_commission:
        changed.append('commission')
    audit('edit', entity='Transaction', entity_id=t.id,
          detail='; '.join(changed) or 'details')
    flash('Transaction saved.', 'success')
    return redirect(url_for('transaction_detail', id=t.id))


@app.route('/transactions/<int:id>/payments', methods=['POST'])
@requires('edit')
def transaction_payment_add(id):
    """Record money received. Each payment is its own row, counted once."""
    t = Transaction.query.get_or_404(id)
    amount = _fnum(request.form.get('amount'))
    if not amount or amount <= 0:
        flash('Enter the amount received before saving the payment.', 'error')
        return redirect(url_for('transaction_detail', id=id))
    db.session.add(TransactionPayment(
        transaction_id=t.id, amount=round(float(amount), 2),
        received_on=_parse_date(request.form.get('received_on')) or date.today(),
        method=_ftext(request.form.get('method')),
        reference=_ftext(request.form.get('reference')),
        note=_ftext(request.form.get('note')),
        recorded_by=getattr(current_user, 'username', None)))
    db.session.commit()
    audit('create', entity='Transaction', entity_id=t.id,
          detail=f'payment of {money_gbp(amount)} recorded')
    flash(f'Payment of {money_gbp(amount)} recorded.', 'success')
    return redirect(url_for('transaction_detail', id=id))


@app.route('/transactions/<int:id>/payments/<int:pid>/delete', methods=['POST'])
@requires('delete')
def transaction_payment_delete(id, pid):
    p = TransactionPayment.query.filter_by(id=pid, transaction_id=id).first_or_404()
    amount = p.amount
    db.session.delete(p)
    db.session.commit()
    audit('delete', entity='Transaction', entity_id=id,
          detail=f'payment of {money_gbp(amount)} removed')
    flash('Payment removed.', 'success')
    return redirect(url_for('transaction_detail', id=id))


@app.route('/transactions/<int:id>/documents', methods=['POST'])
@requires('edit')
def transaction_document_add(id):
    t = Transaction.query.get_or_404(id)
    upload = request.files.get('document')
    if not upload or not upload.filename:
        flash('Choose a file to upload.', 'error')
        return redirect(url_for('transaction_detail', id=id))
    blob = upload.read()
    if len(blob) > MAX_UPLOAD_BYTES:
        flash('That file is too large to store against a transaction.', 'error')
        return redirect(url_for('transaction_detail', id=id))
    db.session.add(TransactionDocument(
        transaction_id=t.id, filename=_clean_filename(upload.filename, 'document'),
        size=len(blob), data=blob,
        kind=_ftext(request.form.get('kind')) or 'Document',
        uploaded_by=getattr(current_user, 'username', None)))
    db.session.commit()
    audit('create', entity='Transaction', entity_id=t.id, detail='document uploaded')
    flash('Document uploaded.', 'success')
    return redirect(url_for('transaction_detail', id=id))


@app.route('/transactions/<int:id>/documents/<int:did>')
def transaction_document_download(id, did):
    d = TransactionDocument.query.filter_by(id=did, transaction_id=id).first_or_404()
    audit('export', entity='Transaction', entity_id=id,
          detail=f'downloaded {d.filename}')
    from flask import send_file
    import io as _io
    return send_file(_io.BytesIO(d.data or b''), as_attachment=True,
                     download_name=d.filename or 'document')


@app.route('/transactions/<int:id>/documents/<int:did>/delete', methods=['POST'])
@requires('delete')
def transaction_document_delete(id, did):
    d = TransactionDocument.query.filter_by(id=did, transaction_id=id).first_or_404()
    name = d.filename
    db.session.delete(d)
    db.session.commit()
    audit('delete', entity='Transaction', entity_id=id, detail=f'removed {name}')
    flash('Document removed.', 'success')
    return redirect(url_for('transaction_detail', id=id))


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
        if not t.reference:
            t.reference = next_transaction_reference()
        if not t.status:
            t.status = 'Draft'
        db.session.commit()
        audit('create', entity='Transaction', entity_id=t.id, detail=t.reference)
        flash('Transaction recorded. It now appears as a tenure on the property.', 'success')
        return redirect(url_for('transaction_detail', id=t.id))
    prop_id = request.args.get('property_id')
    return render_template('transactions/form.html', properties=properties, prop_id=prop_id, trans=None)


@app.route('/transactions/<int:id>/edit', methods=['GET', 'POST'])
def transaction_edit(id):
    """Kept so older links still work. Editing happens on the record itself,
    which is the one place the money can be changed and the one Save button."""
    return redirect(url_for('transaction_detail', id=id))


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
        'type': project.instruction_type or '',
        'ref': project.project_ref or project.name,
        'address': address or 'No property linked',
        'rent': format_rent(price, unit),
        'size': format_size(size),
    }


@app.route('/projects')
def projects_list():
    q = request.args.get('q', '')
    status = request.args.get('status', '')
    etype = request.args.get('type', '')
    query = Project.query
    if q:
        query = query.filter(
            db.or_(Project.name.ilike(f'%{q}%'), Project.client.ilike(f'%{q}%'),
                   Project.project_ref.ilike(f'%{q}%'))
        )
    if status:
        query = query.filter(Project.status == status)
    if etype:
        query = query.filter(Project.instruction_type == etype)
    projects = query.order_by(Project.created_at.desc()).all()
    rows = [project_row_summary(p) for p in projects]
    return render_template('projects/list.html', projects=projects, rows=rows,
                           q=q, status=status, type_=etype)


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
    target_type = ('Landlord' if (form.get('instruction_type') or '').strip() == INSTRUCTION_TO_LET
                   else 'Client')
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
@requires('create')
def project_new():
    properties = Property.query.order_by(Property.address).all()

    def render(v, errors=None):
        # A rejected form comes back holding the client that was chosen, not
        # just their id, so the selector still shows who it was.
        v = dict(v)
        chosen = _fcontact(v.get('client_contact_id'))
        v['client_contact'] = Contact.query.get(chosen) if chosen else None
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
            fee_earner_id=_fid(form.get('fee_earner_id')),
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
INSTRUCTION_TYPES = ['For Sale – Available', 'To Let – Available',
                     'Market Appraisal', 'Prospect', 'Archived']

# The two that put a property on the market. Used where the CRM needs to know
# whether an instruction is a sale or a letting, rather than just its label.
INSTRUCTION_FOR_SALE = INSTRUCTION_TYPES[0]
INSTRUCTION_TO_LET = INSTRUCTION_TYPES[1]
INSTRUCTION_APPRAISAL = INSTRUCTION_TYPES[2]
app.jinja_env.globals['INSTRUCTION_FOR_SALE'] = INSTRUCTION_FOR_SALE
app.jinja_env.globals['INSTRUCTION_TO_LET'] = INSTRUCTION_TO_LET
app.jinja_env.globals['INSTRUCTION_APPRAISAL'] = INSTRUCTION_APPRAISAL
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
        'fee_earner_id': project.fee_earner_id or '',
        'client_contact_id': project.client_contact_id or '',
        'client_contact': (Contact.query.get(project.client_contact_id)
                           if project.client_contact_id else None),
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
        errors['instruction_type'] = 'Choose one of: ' + ', '.join(INSTRUCTION_TYPES) + '.'

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

    return render_template('projects/detail.html', project=project,
                           folder_labels=FOLDER_LABELS, today=date.today(),
                           matches=matches, registered_ids=registered_ids,
                           activity=activity, enquiries=enquiries,
                           notes_timeline=notes_timeline, pub=pub)


@app.route('/projects/<int:id>/edit', methods=['GET', 'POST'])
@requires('edit')
def project_edit(id):
    project = Project.query.get_or_404(id)
    properties = Property.query.order_by(Property.address).all()
    if request.method == 'POST':
        # Presence-guarded: the Project Overview is editable in place and posts
        # only the fields on screen, so a save must not blank the others.
        was_named = project.name
        was_type = project.instruction_type
        was_client = project.client_contact_id
        if 'instruction_type' in request.form and not instruction_type_ok(
                request.form.get('instruction_type'), was_type):
            flash('Choose one of: ' + ', '.join(INSTRUCTION_TYPES) + '.', 'warning')
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
        # Who an instruction is for matters, so a change of client is recorded
        # by name rather than only by id.
        if project.client_contact_id != was_client:
            audit('edit', entity='Project', entity_id=project.id,
                  detail='client {} to {}'.format(
                      contact_label(Contact.query.get(was_client)) if was_client else 'none',
                      contact_label(Contact.query.get(project.client_contact_id))
                      if project.client_contact_id else 'none'))
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

def organisation_form_values(org):
    """An organisation's fields as plain form values, shared by both pages.

    Add Organisation and the Organisation Overview render from the same
    components, so they cannot drift apart.
    """
    if org is None:
        return {'status': 'Prospect', 'types': []}

    def d(val):
        return val.isoformat() if val else ''

    values = {name: (getattr(org, name) or '') for name, _key, _c in ORGANISATION_FIELDS}
    values.update({
        'incorporated_on': d(org.incorporated_on),
        'aml_reviewed_on': d(org.aml_reviewed_on),
        'status': org.status or 'Prospect',
        'types': org.type_names,
        'main_contact_id': org.main_contact_id or '',
        'fee_earner_id': org.fee_earner_id or '',
    })
    for name, _key, _c in ORGANISATION_COMPLIANCE_FIELDS:
        values.setdefault(name, getattr(org, name) or '')
    values['marketing_consent'] = bool(org.marketing_consent)
    return values


def _normalise(value):
    """Loose form of a name or number, for comparing two records."""
    return re.sub(r'[^a-z0-9]', '', (value or '').lower())


def _email_domain(email):
    return (email or '').strip().lower().rsplit('@', 1)[-1] if '@' in (email or '') else ''


def possible_duplicates(name, trading_name=None, company_number=None, email=None,
                        phone=None, address=None, ignore_id=None):
    """Organisations that might already be this one.

    Nothing is merged and nothing is refused — this only gathers what looks
    similar so a person can decide. Two companies really can share a trading
    name, and a serviced office really can house dozens of them on one address.
    """
    hits = {}

    def note(org, why):
        if org.id == ignore_id:
            return
        hits.setdefault(org.id, {'org': org, 'why': []})
        if why not in hits[org.id]['why']:
            hits[org.id]['why'].append(why)

    number = _normalise(company_number)
    if number:
        for org in Organisation.query.filter(Organisation.company_number.isnot(None)).all():
            if _normalise(org.company_number) == number:
                note(org, 'the same company registration number')

    for field, label in ((name, 'the same name'), (trading_name, 'the same trading name')):
        key = _normalise(field)
        if not key:
            continue
        for org in Organisation.query.all():
            if key in (_normalise(org.name), _normalise(org.trading_name),
                       _normalise(org.legal_name)):
                note(org, label)

    domain = _email_domain(email)
    # A shared webmail domain says nothing about who anybody is.
    if domain and domain not in {'gmail.com', 'hotmail.com', 'outlook.com',
                                 'yahoo.com', 'icloud.com', 'me.com', 'aol.com'}:
        for org in Organisation.query.filter(Organisation.email.isnot(None)).all():
            if _email_domain(org.email) == domain:
                note(org, 'the same email domain')

    digits = re.sub(r'\D', '', phone or '')
    if len(digits) >= 9:
        for org in Organisation.query.filter(Organisation.phone.isnot(None)).all():
            if re.sub(r'\D', '', org.phone or '')[-9:] == digits[-9:]:
                note(org, 'the same telephone number')

    where = _normalise(address)
    if where and len(where) > 8:
        for org in Organisation.query.all():
            if where in (_normalise(org.address), _normalise(org.registered_address)):
                note(org, 'the same address')

    return sorted(hits.values(), key=lambda h: -len(h['why']))


def _apply_org_types(org, form):
    """Set which types an organisation holds, from the boxes that were ticked."""
    if 'types_submitted' not in form:
        return
    wanted = {t for t in form.getlist('types') if t in ORG_TYPES}
    held = {t.name: t for t in org.types}
    for name in wanted - set(held):
        db.session.add(OrganisationType(organisation_id=org.id, name=name))
    for name, row in held.items():
        if name not in wanted:
            db.session.delete(row)
    # The single old type column is kept in step so nothing that still reads it
    # goes blank, but it is no longer where the answer lives.
    org.org_type = sorted(wanted)[0] if wanted else None


def _organisation_required(form, org=None):
    """What an organisation cannot be saved without. Checked on the server."""
    errors = {}
    if not (form.get('name') or '').strip():
        errors['name'] = 'An organisation needs a name.'
    status = (form.get('status') or '').strip()
    if status and status not in ORG_STATUS_NAMES:
        errors['status'] = f'"{status}" is not an organisation status.'
    elif not status and org is None:
        errors['status'] = 'Choose a status.'
    types = [t for t in form.getlist('types') if t in ORG_TYPES]
    if not types and 'types_submitted' in form:
        errors['types'] = 'Choose at least one type.'
    if not _fid(form.get('fee_earner_id')):
        errors['fee_earner'] = 'Every organisation needs an assigned fee earner.'
    return errors


@app.route('/organisations')
def organisations_list():
    q = (request.args.get('q') or '').strip()
    query = Organisation.query
    statuses = [s for s in request.args.getlist('status') if s in ORG_STATUS_NAMES]
    if statuses:
        query = query.filter(Organisation.status.in_(statuses))
    elif request.args.get('archived') != '1':
        # Archived organisations stay on the books; they just keep out of the way.
        query = query.filter(db.or_(Organisation.status.is_(None),
                                    Organisation.status.notin_(ORG_HIDDEN_STATUSES)))
    if q:
        like = f'%{q}%'
        query = query.filter(db.or_(
            Organisation.name.ilike(like), Organisation.trading_name.ilike(like),
            Organisation.legal_name.ilike(like), Organisation.company_number.ilike(like),
            Organisation.email.ilike(like), Organisation.org_type.ilike(like)))
    orgs = query.order_by(Organisation.name).all()
    return render_template(
        'crm/organisations_list.html', orgs=orgs, q=q, statuses=statuses,
        archived=request.args.get('archived') == '1',
        status_counts={s: Organisation.query.filter(Organisation.status == s).count()
                       for s in ORG_STATUS_NAMES})


@app.route('/organisations/new', methods=['GET', 'POST'])
@requires('create')
def organisation_new():
    if request.method == 'POST':
        form = request.form
        errors = _organisation_required(form)
        near = possible_duplicates(
            form.get('name'), form.get('trading_name'), form.get('company_number'),
            form.get('email'), form.get('phone'), form.get('address'))
        # Warn once. Saying "yes, it really is a different company" gets through.
        if near and form.get('confirm_new') != '1':
            return render_template('crm/organisation_form.html', org=None,
                                   v=dict(form, types=form.getlist('types')),
                                   errors=errors, duplicates=near,
                                   contacts=[], next_url=form.get('next'))
        if errors:
            return render_template('crm/organisation_form.html', org=None,
                                   v=dict(form, types=form.getlist('types')),
                                   errors=errors, duplicates=[], contacts=[],
                                   next_url=form.get('next'))
        org = Organisation(status=(form.get('status') or 'Prospect'))
        apply_form_fields(org, form, ORGANISATION_FIELDS)
        org.postcode = (org.postcode or '').upper() or None
        db.session.add(org)
        db.session.commit()
        _apply_org_types(org, form)
        db.session.commit()
        _log_activity('status_change', organisation=org, new_status=org.status,
                      body=f'Organisation created (status: {org.status})')
        db.session.commit()
        audit('create', entity='Organisation', entity_id=org.id, detail=org.name)
        flash('Organisation added.', 'success')
        if form.get('next'):
            return redirect(form['next'])
        return redirect(url_for('organisation_detail', id=org.id))
    return render_template('crm/organisation_form.html', org=None,
                           v=organisation_form_values(None), errors={},
                           duplicates=[], contacts=[],
                           next_url=request.args.get('next'))


@app.route('/organisations/<int:id>')
def organisation_detail(id):
    org = Organisation.query.get_or_404(id)
    return render_template(
        'crm/organisation_detail.html', org=org, v=organisation_form_values(org),
        errors={}, today=date.today(),
        contacts=Contact.query.order_by(Contact.last_name, Contact.first_name).all(),
        properties=Property.query.order_by(Property.address).all(),
        projects=Project.query.order_by(Project.name).all(),
        transactions=Transaction.query.order_by(Transaction.reference).all(),
        enquiries=Enquiry.query.filter_by(organisation_id=id)
                               .order_by(Enquiry.created_at.desc()).all(),
        activity=ContactActivity.query.filter_by(organisation_id=id)
                                      .order_by(ContactActivity.created_at.desc()).limit(30).all(),
        history=AuditLog.query.filter_by(entity='Organisation', entity_id=str(id))
                              .order_by(AuditLog.at.desc()).limit(20).all(),
        suggestions=organisation_link_suggestions(org),
        can_see_compliance=current_user.can('admin'))


@app.route('/organisations/<int:id>/save', methods=['POST'])
@requires('edit')
def organisation_save(id):
    """Save the overview. Only the fields the page sent are written."""
    org = Organisation.query.get_or_404(id)
    form = request.form
    errors = _organisation_required(form, org)
    if errors:
        for message in errors.values():
            flash(message, 'error')
        return redirect(url_for('organisation_detail', id=id))

    apply_form_fields(org, form, ORGANISATION_FIELDS)
    if 'postcode' in form:
        org.postcode = (org.postcode or '').upper() or None
    if 'main_contact_id' in form:
        chosen = (form.get('main_contact_id') or '').strip()
        org.main_contact_id = int(chosen) if chosen.isdigit() else None

    # Compliance and accounts are only writable by someone allowed to see them.
    if current_user.can('admin'):
        apply_form_fields(org, form, ORGANISATION_COMPLIANCE_FIELDS)
        if 'compliance_submitted' in form:
            org.marketing_consent = form.get('marketing_consent') == '1'
    elif any(k in form for k, _f, _c in ORGANISATION_COMPLIANCE_FIELDS):
        audit('denied', entity='Organisation', entity_id=id,
              detail='tried to write compliance or accounts fields')
        flash('Compliance and accounts details were not saved — '
              'your account cannot change them.', 'error')

    _apply_org_types(org, form)
    _apply_org_status(form.get('status'), org)
    db.session.commit()
    audit('edit', entity='Organisation', entity_id=org.id, detail=org.name)
    flash('Organisation saved.', 'success')
    return redirect(url_for('organisation_detail', id=org.id))


def _apply_org_status(new_status, org):
    """Set an organisation's status and record the change.

    Organisations have their own vocabulary — a company can be a current
    occupier or marked do not contact, neither of which means anything for a
    person — so this checks against that list rather than the contact one.
    A status is only ever changed by somebody choosing it.
    """
    if new_status not in ORG_STATUS_NAMES:
        return False
    old = org.status
    if old == new_status:
        return False
    org.status = new_status
    _log_activity('status_change', organisation=org, old_status=old,
                  new_status=new_status,
                  body=f'Status changed from {old or "—"} to {new_status}')
    audit('edit', entity='Organisation', entity_id=org.id,
          detail=f'status {old} to {new_status}')
    return True


def may_contact(org):
    """Whether an organisation may be included in a bulk or marketing send.

    The one place that decides. There is no bulk-email feature in the CRM yet;
    this exists so that when one is built it has to come through here rather
    than reading a list of addresses straight out of the table.
    """
    if org is None:
        return False
    return not org.do_not_contact and not org.is_archived


def contactable_organisations():
    """Every organisation a bulk send is allowed to reach.

    Do Not Contact and archived records are dropped in the query, so they
    cannot be reached by forgetting to filter further down.
    """
    return (Organisation.query
            .filter(db.or_(Organisation.status.is_(None),
                           Organisation.status.notin_(
                               ORG_HIDDEN_STATUSES | {ORG_NO_CONTACT})))
            .order_by(Organisation.name).all())


def organisation_link_suggestions(org, limit=12):
    """Places this organisation's name appears as text, unlinked.

    Landlord, client, tenant, vendor and purchaser were free text long before
    they were relationships, and that text is left exactly as it is. This only
    points out where the same name appears, so somebody can confirm the link.
    Nothing here changes a record.
    """
    keys = {_normalise(n) for n in (org.name, org.trading_name, org.legal_name) if n}
    keys.discard('')
    if not keys:
        return []
    linked = {(r.role, r.project_id, r.transaction_id, r.property_id) for r in org.roles}
    found = []
    for project in Project.query.all():
        for text_value, role in ((project.client, 'Client'),
                                 (project.landlord_name, 'Landlord')):
            if _normalise(text_value) in keys and \
                    (role, project.id, None, None) not in linked:
                found.append({'role': role, 'kind': 'project', 'record': project,
                              'label': project.name, 'text': text_value,
                              'url': url_for('project_detail', id=project.id),
                              'project_id': project.id})
    for t in Transaction.query.all():
        for text_value, role in ((t.client, 'Client'), (t.landlord, 'Landlord'),
                                 (t.tenant, 'Tenant'), (t.vendor, 'Vendor'),
                                 (t.purchaser, 'Purchaser')):
            if _normalise(text_value) in keys and \
                    (role, None, t.id, None) not in linked:
                found.append({'role': role, 'kind': 'transaction', 'record': t,
                              'label': t.reference or 'Transaction', 'text': text_value,
                              'url': url_for('transaction_detail', id=t.id),
                              'transaction_id': t.id})
    return found[:limit]


def _role_target(form):
    """Which record a relationship is being attached to."""
    out = {}
    for key, field in (('property_id', 'property_id'), ('project_id', 'project_id'),
                       ('transaction_id', 'transaction_id')):
        value = (form.get(field) or '').strip()
        out[key] = int(value) if value.isdigit() else None
    return out


@app.route('/organisations/<int:id>/roles', methods=['POST'])
@requires('edit')
def organisation_role_add(id):
    """Link an organisation to a property, project or transaction in a role."""
    org = Organisation.query.get_or_404(id)
    role = (request.form.get('role') or '').strip()
    if role not in ORG_ROLE_NAMES:
        flash(f'"{role}" is not a relationship this CRM records.', 'error')
        return redirect(url_for('organisation_detail', id=id))

    target = _role_target(request.form)
    if not any(target.values()) and role != 'Applicant':
        flash('Choose the property, project or transaction this relationship is about.',
              'error')
        return redirect(url_for('organisation_detail', id=id))

    contact_id = (request.form.get('contact_id') or '').strip()
    link = OrganisationRole(
        organisation_id=org.id, role=role, **target,
        contact_id=int(contact_id) if contact_id.isdigit() else None,
        start_date=_parse_date(request.form.get('start_date')) or date.today(),
        end_date=_parse_date(request.form.get('end_date')),
        notes=_ftext(request.form.get('notes')),
        created_by=getattr(current_user, 'username', None))
    db.session.add(link)
    db.session.commit()
    audit('create', entity='Organisation', entity_id=org.id,
          detail=f'linked as {role}')
    _log_activity('note', organisation=org, body=f'Linked as {role}.')
    db.session.commit()
    flash(f'{org.name} linked as {role.lower()}.', 'success')
    return redirect(url_for('organisation_detail', id=id) + '#relationships')


@app.route('/organisations/<int:id>/roles/<int:rid>/end', methods=['POST'])
@requires('edit')
def organisation_role_end(id, rid):
    """Close a relationship. The row stays: what a company used to be is history."""
    link = OrganisationRole.query.filter_by(id=rid, organisation_id=id).first_or_404()
    link.end_date = _parse_date(request.form.get('end_date')) or date.today()
    link.ended_by = getattr(current_user, 'username', None)
    db.session.commit()
    audit('edit', entity='Organisation', entity_id=id,
          detail=f'ended {link.role} relationship')
    _log_activity('note', organisation=link.organisation,
                  body=f'{link.role} relationship ended.')
    db.session.commit()
    flash('Relationship ended. Its history has been kept.', 'success')
    return redirect(url_for('organisation_detail', id=id) + '#relationships')


@app.route('/organisations/<int:id>/roles/<int:rid>/reopen', methods=['POST'])
@requires('edit')
def organisation_role_reopen(id, rid):
    link = OrganisationRole.query.filter_by(id=rid, organisation_id=id).first_or_404()
    link.end_date = None
    link.ended_by = None
    db.session.commit()
    audit('edit', entity='Organisation', entity_id=id, detail=f'reopened {link.role}')
    flash('Relationship reopened.', 'success')
    return redirect(url_for('organisation_detail', id=id) + '#relationships')


@app.route('/organisations/<int:id>/requirements', methods=['POST'])
@requires('edit')
def organisation_requirement_add(id):
    """What this organisation is looking for. It may be looking for several."""
    org = Organisation.query.get_or_404(id)
    req = OrganisationRequirement(
        organisation_id=org.id,
        title=_ftext(request.form.get('title')) or 'Requirement',
        locations=_ftext(request.form.get('locations')),
        property_type=_ftext(request.form.get('property_type')),
        intended_use=_ftext(request.form.get('intended_use')),
        use_class=_ftext(request.form.get('use_class')),
        size_min=_fnum(request.form.get('size_min')),
        size_max=_fnum(request.form.get('size_max')),
        rent_min=_fnum(request.form.get('rent_min')),
        rent_max=_fnum(request.form.get('rent_max')),
        price_min=_fnum(request.form.get('price_min')),
        price_max=_fnum(request.form.get('price_max')),
        tenure=_ftext(request.form.get('tenure')),
        occupation_from=_parse_date(request.form.get('occupation_from')),
        lease_length=_ftext(request.form.get('lease_length')),
        extra=_ftext(request.form.get('extra')))
    db.session.add(req)
    db.session.commit()
    audit('create', entity='Organisation', entity_id=org.id, detail='requirement added')
    flash('Requirement added.', 'success')
    return redirect(url_for('organisation_detail', id=id) + '#requirements')


@app.route('/organisations/<int:id>/requirements/<int:rid>/close', methods=['POST'])
@requires('edit')
def organisation_requirement_close(id, rid):
    req = OrganisationRequirement.query.filter_by(id=rid, organisation_id=id).first_or_404()
    req.active = not req.active
    db.session.commit()
    audit('edit', entity='Organisation', entity_id=id,
          detail=('reopened' if req.active else 'closed') + ' a requirement')
    return redirect(url_for('organisation_detail', id=id) + '#requirements')


@app.route('/api/contacts')
def api_contacts():
    """People, for a client or tenant selector.

    Searches what somebody would actually type: the person's name, the company
    they belong to, their email or their telephone number. Returns the
    contact's id, so a record links to the person rather than copying a name.
    """
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify([])
    like = f'%{q}%'
    found = {c.id: c for c in Contact.query.filter(db.or_(
        Contact.first_name.ilike(like), Contact.last_name.ilike(like),
        Contact.email.ilike(like), Contact.phone.ilike(like),
        Contact.mobile.ilike(like))).limit(25).all()}

    # Somebody looking for "Marsden" usually means the company, not a surname.
    for org in Organisation.query.filter(db.or_(
            Organisation.name.ilike(like), Organisation.trading_name.ilike(like)
            )).limit(10).all():
        for person in org.contacts:
            found.setdefault(person.id, person)

    # A telephone number is typed with spaces as often as not.
    digits = re.sub(r'\D', '', q)
    if len(digits) >= 6:
        for person in Contact.query.filter(db.or_(
                Contact.phone.isnot(None), Contact.mobile.isnot(None))).limit(400).all():
            joined = re.sub(r'\D', '', f'{person.phone or ""}{person.mobile or ""}')
            if digits in joined:
                found.setdefault(person.id, person)

    rows = list(found.values())[:25]
    return jsonify([{
        'id': c.id,
        'label': contact_label(c),
        'name': c.full_name,
        'company': c.organisation.name if c.organisation else None,
        'job_title': c.job_title,
        'email': c.email,
        'phone': c.phone or c.mobile,
        'url': url_for('contact_detail', id=c.id),
    } for c in rows])


@app.route('/api/organisations')
def api_organisations():
    """The searchable picker behind every Landlord / Tenant / Client field.

    Searches the things somebody would actually type: the company's names, its
    registration number, a contact's name or email, a property address and a
    project reference. Returns the organisation's id so the record is linked
    rather than its details being copied.
    """
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify([])
    like = f'%{q}%'
    found = {o.id: o for o in Organisation.query.filter(db.or_(
        Organisation.name.ilike(like), Organisation.trading_name.ilike(like),
        Organisation.legal_name.ilike(like), Organisation.company_number.ilike(like),
        Organisation.email.ilike(like))).limit(25).all()}

    for c in Contact.query.filter(db.and_(
            Contact.organisation_id.isnot(None),
            db.or_(Contact.first_name.ilike(like), Contact.last_name.ilike(like),
                   Contact.email.ilike(like)))).limit(25).all():
        if c.organisation and c.organisation_id not in found:
            found[c.organisation_id] = c.organisation

    for link in OrganisationRole.query.limit(500).all():
        if link.organisation_id in found:
            continue
        target = link.attached_to
        text_value = ''
        if link.property_ref:
            text_value = link.property_ref.address or ''
        elif link.project:
            text_value = f'{link.project.name} {link.project.project_ref or ""}'
        if target and q.lower() in text_value.lower():
            found[link.organisation_id] = link.organisation

    return jsonify([{
        'id': o.id, 'name': o.name, 'trading_name': o.trading_name,
        'types': o.type_names, 'status': o.status,
        'do_not_contact': o.do_not_contact,
        'main_contact': o.main_contact.full_name if o.main_contact else None,
        'url': url_for('organisation_detail', id=o.id),
    } for o in list(found.values())[:25]])


def _link_target(data):
    """Which record a link is being made against, from a form or JSON body."""
    out = {}
    for field in ('property_id', 'project_id', 'transaction_id'):
        raw = str(data.get(field) or '').strip()
        out[field] = int(raw) if raw.isdigit() else None
    return out


def current_org_link(role, **target):
    """The organisation currently in this role on this record, if any.

    A record can have held the same role several times over the years; this is
    the one running now, so a field shows who the client *is* rather than who
    it once was.
    """
    query = OrganisationRole.query.filter_by(role=role)
    for field, value in target.items():
        query = query.filter(getattr(OrganisationRole, field) == value)
    live = [r for r in query.order_by(OrganisationRole.start_date.desc()).all()
            if r.is_current]
    return live[0] if live else None


app.jinja_env.globals['current_org_link'] = current_org_link


@app.route('/api/organisations/link', methods=['POST'])
@requires('edit')
def api_organisation_link():
    """Attach an organisation to a project, transaction or property in a role.

    This writes a relationship, not a copy: the record keeps the organisation's
    id, so changing the company's name or contact anywhere changes it here too.
    Whatever was typed as free text before is left exactly as it is.
    """
    data = request.get_json(silent=True) or request.form
    role = (data.get('role') or '').strip()
    if role not in ORG_ROLE_NAMES:
        return jsonify({'ok': False, 'error': f'"{role}" is not a relationship.'}), 400

    target = _link_target(data)
    if not any(target.values()):
        return jsonify({'ok': False, 'error': 'Nothing to link it to.'}), 400

    raw = str(data.get('organisation_id') or '').strip()
    if not raw.isdigit():
        return jsonify({'ok': False, 'error': 'Choose an organisation.'}), 400
    org = Organisation.query.get(int(raw))
    if org is None:
        return jsonify({'ok': False, 'error': 'That organisation no longer exists.'}), 404

    contact_raw = str(data.get('contact_id') or '').strip()
    contact_id = int(contact_raw) if contact_raw.isdigit() else None

    existing = current_org_link(role, **target)
    if existing and existing.organisation_id == org.id:
        existing.contact_id = contact_id          # same company, different person
        db.session.commit()
        audit('edit', entity='Organisation', entity_id=org.id,
              detail=f'{role} contact changed')
        return jsonify({'ok': True, 'link': _link_json(existing)})

    if existing:
        # The role has changed hands. The old one is closed, never deleted.
        existing.end_date = date.today()
        existing.ended_by = getattr(current_user, 'username', None)

    link = OrganisationRole(organisation_id=org.id, role=role, contact_id=contact_id,
                            start_date=date.today(),
                            created_by=getattr(current_user, 'username', None),
                            **target)
    db.session.add(link)
    db.session.commit()
    audit('create', entity='Organisation', entity_id=org.id,
          detail=f'linked as {role}')
    _log_activity('note', organisation=org, body=f'Linked as {role}.')
    db.session.commit()
    return jsonify({'ok': True, 'link': _link_json(link)})


@app.route('/api/organisations/unlink', methods=['POST'])
@requires('edit')
def api_organisation_unlink():
    """End the current link in this role. The relationship keeps its history."""
    data = request.get_json(silent=True) or request.form
    role = (data.get('role') or '').strip()
    link = current_org_link(role, **_link_target(data))
    if link is None:
        return jsonify({'ok': True, 'link': None})
    link.end_date = date.today()
    link.ended_by = getattr(current_user, 'username', None)
    db.session.commit()
    audit('edit', entity='Organisation', entity_id=link.organisation_id,
          detail=f'ended {role} link')
    return jsonify({'ok': True, 'link': None})


def _link_json(link):
    org = link.organisation
    return {
        'role': link.role,
        'organisation_id': org.id, 'name': org.name,
        'trading_name': org.trading_name, 'types': org.type_names,
        'status': org.status, 'do_not_contact': org.do_not_contact,
        'contact_id': link.contact_id,
        'contact': link.contact.full_name if link.contact else None,
        'main_contact': org.main_contact.full_name if org.main_contact else None,
        'url': url_for('organisation_detail', id=org.id),
    }


@app.route('/api/organisations/<int:id>/contacts')
def api_organisation_contacts(id):
    """Who works at this organisation, for the relationship-contact box."""
    org = Organisation.query.get_or_404(id)
    return jsonify([{'id': c.id, 'name': c.full_name,
                     'job_title': c.job_title,
                     'is_main': c.id == org.main_contact_id}
                    for c in org.contacts])


@app.route('/api/organisations/quick', methods=['POST'])
@requires('create')
def api_organisation_quick():
    """Add an organisation without leaving the page you were filling in.

    Duplicates are checked first and reported back rather than created; the
    caller decides whether to open the existing one or insist. Nothing already
    typed on the page behind this is touched.
    """
    data = request.get_json(silent=True) or request.form
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'An organisation needs a name.'}), 400
    earner_id = _fid(data.get('fee_earner_id') or data.get('fee_earner'))
    if not earner_id:
        return jsonify({'ok': False, 'error': 'Choose an assigned fee earner.'}), 400
    status = (data.get('status') or 'Prospect').strip()
    if status not in ORG_STATUS_NAMES:
        return jsonify({'ok': False, 'error': f'"{status}" is not a status.'}), 400
    types = [t for t in (data.get('types') or []) if t in ORG_TYPES] \
        if isinstance(data.get('types'), list) else \
        [t for t in request.form.getlist('types') if t in ORG_TYPES]

    if data.get('confirm_new') != True and str(data.get('confirm_new')) != '1':
        near = possible_duplicates(name, data.get('trading_name'),
                                   data.get('company_number'), data.get('email'),
                                   data.get('phone'))
        if near:
            return jsonify({'ok': False, 'duplicates': [{
                'id': h['org'].id, 'name': h['org'].name,
                'why': h['why'], 'status': h['org'].status,
                'types': h['org'].type_names,
                'url': url_for('organisation_detail', id=h['org'].id),
            } for h in near]}), 409

    org = Organisation(name=name, status=status, fee_earner_id=earner_id,
                       trading_name=_ftext(data.get('trading_name')),
                       company_number=_ftext(data.get('company_number')),
                       email=_ftext(data.get('email')),
                       phone=_ftext(data.get('phone')))
    db.session.add(org)
    db.session.commit()
    for name_of_type in types:
        db.session.add(OrganisationType(organisation_id=org.id, name=name_of_type))
    org.org_type = sorted(types)[0] if types else None
    db.session.commit()
    _log_activity('status_change', organisation=org, new_status=org.status,
                  body=f'Organisation created (status: {org.status})')
    db.session.commit()
    audit('create', entity='Organisation', entity_id=org.id,
          detail=f'{org.name} (added from a linked record)')
    return jsonify({'ok': True, 'organisation': {
        'id': org.id, 'name': org.name, 'trading_name': org.trading_name,
        'types': org.type_names, 'status': org.status,
        'do_not_contact': org.do_not_contact, 'main_contact': None,
        'url': url_for('organisation_detail', id=org.id)}})


@app.route('/organisations/<int:id>/edit', methods=['GET', 'POST'])
def organisation_edit(id):
    """Kept so older links still work. Editing happens on the record itself."""
    return redirect(url_for('organisation_detail', id=id))


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

CONTACT_SECTIONS = {
    'Client': ['Client', 'Landlord'],
    'Tenant': ['Tenant', 'Prospective Tenant'],
}


@app.route('/contacts')
def contacts_list():
    q = request.args.get('q', '')
    ctype = request.args.get('type', '')   # which sidebar page this is
    query = Contact.query
    # Each sidebar page is one of these. A landlord is the client on a letting
    # instruction, so Clients covers both; Tenants covers prospective ones too.
    _type_groups = CONTACT_SECTIONS
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
                           all_organisations=Organisation.query.order_by(Organisation.name).all(),
                           matched_properties=match_properties_to_contact(contact),
                           contact=contact)


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


def _fcontact(v):
    """A contact's id, or nothing. Anything that is not a real person is
    refused rather than stored."""
    raw = str(v or '').strip()
    if not raw.isdigit():
        return None
    return int(raw) if Contact.query.get(int(raw)) else None


def _fid(v):
    """A fee earner's id, or nothing.

    Anything that is not an active account allowed to carry a fee is refused
    rather than stored, so a name cannot be invented by editing the request.
    """
    raw = str(v or '').strip()
    if not raw.isdigit():
        return None
    chosen = int(raw)
    return chosen if any(p.id == chosen for p in fee_earners()) else None


def _fcouncil(v):
    """A council's id, or nothing. An id that is not a council on record is
    refused rather than stored, so one cannot be invented by editing the
    request."""
    raw = str(v or '').strip()
    if not raw.isdigit():
        return None
    return int(raw) if Council.query.get(int(raw)) else None


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


ORGANISATION_FIELDS = [
    ('fee_earner_id',      'fee_earner_id',      _fid),
    ('name',               'name',               None),
    ('trading_name',       'trading_name',       _ftext),
    ('legal_name',         'legal_name',         _ftext),
    ('fee_earner',         'fee_earner',         _ftext),
    ('source',             'source',             _ftext),
    ('phone',              'phone',              _ftext),
    ('email',              'email',              _ftext),
    ('website',            'website',            _ftext),
    ('address',            'address',            _ftext),
    ('postcode',           'postcode',           _ftext),
    ('registered_address', 'registered_address', _ftext),
    ('trading_address',    'trading_address',    _ftext),
    ('company_number',     'company_number',     _ftext),
    ('vat_number',         'vat_number',         _ftext),
    ('companies_house_status', 'companies_house_status', _ftext),
    ('incorporated_on',    'incorporated_on',    _parse_date),
    ('nature_of_business', 'nature_of_business', _ftext),
    ('notes',              'notes',              _ftext),
]

# Held back behind a permission: what a company had to prove about itself, and
# what it is billed.
ORGANISATION_COMPLIANCE_FIELDS = [
    ('aml_status',         'aml_status',         _ftext),
    ('aml_reviewed_on',    'aml_reviewed_on',    _parse_date),
    ('beneficial_owners',  'beneficial_owners',  _ftext),
    ('verification_notes', 'verification_notes', _ftext),
    ('accounts_contact',   'accounts_contact',   _ftext),
    ('accounts_email',     'accounts_email',     _ftext),
    ('invoice_address',    'invoice_address',    _ftext),
    ('payment_terms',      'payment_terms',      _ftext),
    ('vat_status',         'vat_status',         _ftext),
    ('accounts_notes',     'accounts_notes',     _ftext),
]


CONTACT_FIELDS = [
    ('fee_earner_id',      'fee_earner_id',      _fid),
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


TRANSACTION_FIELDS = [
    ('fee_earner_id',      'fee_earner_id',      _fid),
    ('reference',          'reference',          _ftext),
    ('status',             'status',             _ftext),
    ('fee_earner',         'fee_earner',         _ftext),
    ('client',             'client',             _ftext),
    ('transaction_type',   'transaction_type',   _ftext),
    ('tenure_type',        'tenure_type',        _ftext),
    ('transaction_date',   'transaction_date',   _parse_date),
    ('vendor',             'vendor',             _ftext),
    ('purchaser',          'purchaser',          _ftext),
    ('landlord',           'landlord',           _ftext),
    ('tenant',             'tenant',             _ftext),
    ('value',              'value',              _fnum),
    ('rent_pa',            'rent_pa',            _fnum),
    ('agreed_value',       'agreed_value',       _fnum),
    ('fee_type',           'fee_type',           _ftext),
    ('fee_percent',        'fee_percent',        _fnum),
    ('fixed_fee',          'fixed_fee',          _fnum),
    ('vat_rate',           'vat_rate',           _fnum),
    ('invoice_number',     'invoice_number',     _ftext),
    ('invoice_date',       'invoice_date',       _parse_date),
    ('payment_due_date',   'payment_due_date',   _parse_date),
    ('completion_date',    'completion_date',    _parse_date),
    ('agreement_type',     'agreement_type',     _ftext),
    ('expected_completion_date', 'expected_completion_date', _parse_date),
    ('terms_agreed_date',  'terms_agreed_date',  _parse_date),
    ('solicitors_instructed_date', 'solicitors_instructed_date', _parse_date),
    ('lease_start',        'lease_start',        _parse_date),
    ('lease_end',          'lease_end',          _parse_date),
    ('break_clause',       'break_clause',       _ftext),
    ('client_solicitor',       'client_solicitor',       _ftext),
    ('client_solicitor_firm',  'client_solicitor_firm',  _ftext),
    ('client_solicitor_email', 'client_solicitor_email', _ftext),
    ('client_solicitor_phone', 'client_solicitor_phone', _ftext),
    ('other_solicitor',        'other_solicitor',        _ftext),
    ('other_solicitor_firm',   'other_solicitor_firm',   _ftext),
    ('other_solicitor_email',  'other_solicitor_email',  _ftext),
    ('other_solicitor_phone',  'other_solicitor_phone',  _ftext),
    ('notes',              'notes',              _ftext),
]


PROJECT_FIELDS = [
    ('fee_earner_id',      'fee_earner_id',      _fid),
    ('client_contact_id',  'client_contact_id',  _fcontact),
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
    ('client_contact_id',  'client_contact_id',  _fcontact),
    ('council_id',       'council_id',       _fcouncil),
    ('address',          'address',          None),
    ('property_type',    'property_type',    _ftext),
    ('area',             'area',             _ftext),
    ('size',             'size',             _fnum),
    ('measurement_type', 'measurement_type', _ftext),
    ('description',      'description',      _ftext),
    ('residential_use',  'residential_use',  _ftext),
    ('use_class',        'use_class',        _ftext),
    ('beds',             'beds',             _fint),
    ('baths',            'baths',            _fint),
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
                   ('Terms Agreed', terms),
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
        fee_earner_id=_fid(form.get('fee_earner_id')),
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


def property_on_the_market(prop):
    """How a property is on the market right now, or None if it is not.

    Returns the instruction type of the live instruction behind it. Only
    For Sale – Available and To Let – Available count: a market appraisal, a
    prospect or an archived instruction is not something to offer an
    applicant, whatever the building itself looks like.
    """
    if prop is None:
        return None
    for pj in prop.projects:
        if pj.status != 'Active':
            continue
        if pj.instruction_type not in (INSTRUCTION_FOR_SALE, INSTRUCTION_TO_LET):
            continue
        # Where the instruction has a website listing, that listing must still
        # be available — a let-agreed or sold unit is off the market.
        listings = list(pj.project_listings)
        if listings and not any((l.listing_status or 'available').lower() == 'available'
                                for l in listings):
            continue
        return pj.instruction_type
    return None


def score_requirement(c, prop):
    """How well one property answers one applicant's requirements.

    Returns (score, reasons), and (0, []) unless the property satisfies every
    requirement the applicant has actually set. A requirement the applicant
    has left blank is not checked; one they have set must be met, and a
    property with nothing recorded for that criterion cannot meet it.

    Both directions of matching use this, so an applicant's matched properties
    and a property's matched applicants can never disagree.
    """
    if c is None or prop is None:
        return 0, []

    # ── On the market at all? ──
    available_as = property_on_the_market(prop)
    if available_as is None:
        return 0, []
    reasons = [available_as]
    score = 2

    # ── To let or for sale, as the applicant's budget basis says ──
    if c.req_budget_unit:
        wanted = INSTRUCTION_FOR_SALE if c.req_budget_unit == 'sale' else INSTRUCTION_TO_LET
        if available_as != wanted:
            return 0, []

    def unmet(applicant_value, property_value):
        """A requirement is unmet if it is set and the property cannot meet it."""
        return bool(applicant_value) and not property_value

    # ── Category ──
    if c.req_category:
        if unmet(c.req_category, prop.website_category) or \
                c.req_category.lower() != (prop.website_category or '').lower():
            return 0, []
        score += 3; reasons.append('Category match')

    # ── Use class ──
    if c.req_use_class:
        if unmet(c.req_use_class, prop.use_class) or \
                c.req_use_class.lower() != (prop.use_class or '').lower():
            return 0, []
        score += 2; reasons.append('Use class match')

    # ── Property type ──
    if c.req_property_type:
        # Whole-value, so Industrial never answers a request for Light
        # Industrial, and neither answers a request for a Creative studio.
        if not same_property_type(c.req_property_type, prop.property_type):
            return 0, []
        score += 2; reasons.append('Type match')

    # ── Area ──
    if c.req_area:
        wanted = [a.strip().lower() for a in c.req_area.split(',') if a.strip()]
        where = f"{prop.area or ''} {prop.postcode or ''}".lower()
        hit = next((a for a in wanted if a in where), None)
        if not hit:
            return 0, []
        score += 2; reasons.append(f'Area match ({hit.title()})')

    # ── Size, as a range ──
    if c.req_size_min or c.req_size_max:
        if not prop.size:
            return 0, []
        if c.req_size_min and prop.size < c.req_size_min:
            return 0, []
        if c.req_size_max and prop.size > c.req_size_max:
            return 0, []
        score += 2; reasons.append('Size in range')

    # ── Budget, as a range ──
    if c.req_budget_min or c.req_budget_max:
        if not prop.listing_price:
            return 0, []
        if c.req_budget_min and prop.listing_price < c.req_budget_min:
            return 0, []
        if c.req_budget_max and prop.listing_price > c.req_budget_max:
            return 0, []
        score += 3; reasons.append('Within budget')

    return score, reasons


def match_contacts_to_property(prop):
    """Return [(score, reasons, contact)] best first, for a property."""
    if not prop:
        return []
    results = []
    for c in Contact.query.filter(Contact.req_category != None).all():
        score, reasons = score_requirement(c, prop)
        if score > 0:
            results.append((score, reasons, c))
    return sorted(results, key=lambda x: x[0], reverse=True)


def match_properties_to_contact(contact, limit=12):
    """Properties answering an applicant's requirements, best first.

    Read live from the property register, so the list follows whatever is
    currently saved against the applicant — change a requirement and this
    changes with it.
    """
    if contact is None:
        return []
    if not any([contact.req_category, contact.req_use_class, contact.req_property_type,
                contact.req_area, contact.req_size_min, contact.req_size_max,
                contact.req_budget_min, contact.req_budget_max]):
        return []                       # nothing asked for, so nothing to match
    results = []
    for prop in Property.query.all():
        score, reasons = score_requirement(contact, prop)
        if score > 0:
            results.append({'score': score, 'reasons': reasons, 'prop': prop})
    results.sort(key=lambda r: (-r['score'], r['prop'].address or ''))
    return results[:limit]


# ── Project Tasks ─────────────────────────────────────────────────────────────

# ── Property particulars ─────────────────────────────────────────────────────

def has_letting_terms(listing):
    """Whether a rent has actually been put on this listing."""
    if not listing or not listing.set_as_to_let:
        return False
    if listing.rent_on_application:
        return True
    if listing.listing_price_unit == 'sale':
        return False
    return bool(listing.listing_price or clean_strapline(listing.price_display))


def has_sale_terms(listing):
    """Whether a sale price has actually been put on this listing."""
    if not listing or not listing.set_as_for_sale:
        return False
    if listing.sale_price or clean_strapline(listing.sale_price_display):
        return True
    return listing.listing_price_unit == 'sale' and bool(
        listing.listing_price or clean_strapline(listing.price_display))


def marketing_instruction(project, listing=None):
    """How the property is being marketed: FOR SALE, TO LET, or both.

    Read from what is saved — the instruction type, and the listing's own
    for-sale and to-let switches — never from the words in the strapline. A
    brochure that inferred "to let" from a strapline would put the wrong thing
    on a sale.

    Returns None for an instruction that is not being marketed at all, such as
    a market appraisal, so nothing is claimed on its behalf.
    """
    kind = (project.instruction_type or '') if project else ''
    if kind == INSTRUCTION_FOR_SALE:
        # The instruction settles it. A property is only also advertised the
        # other way where a figure has actually been entered for it — the
        # to-let switch is on by default on every listing, so on its own it
        # would put TO LET on the cover of every sale.
        for_sale, to_let = True, has_letting_terms(listing)
    elif kind == INSTRUCTION_TO_LET:
        for_sale, to_let = has_sale_terms(listing), True
    else:
        for_sale = bool(listing and listing.set_as_for_sale and has_sale_terms(listing))
        to_let = bool(listing and listing.set_as_to_let and has_letting_terms(listing))
        if not (for_sale or to_let):
            return None
    if for_sale and to_let:
        return 'FOR SALE | TO LET'
    if for_sale:
        return 'FOR SALE'
    if to_let:
        return 'TO LET'
    return None


def cover_wording(strapline, instruction):
    """The line as it will appear on the brochure cover.

    A strapline that already says FOR SALE or TO LET is used exactly as it was
    written — the wording is never added twice. One that does not carries the
    instruction inserted for the brochure only; the saved strapline is left
    alone, so what was typed is what stays on the record.
    """
    line = clean_strapline(strapline)
    if not instruction:
        return line
    already = line.upper()
    wanted = [w.strip() for w in instruction.split('|')]
    if all(w in already for w in wanted):
        return line                       # it says it already
    if not line:
        return instruction
    # Any part it does say is not repeated, so FOR SALE | TO LET on a strapline
    # that already says TO LET adds only FOR SALE.
    missing = ' | '.join(w for w in wanted if w not in already)
    if not missing:
        return line
    parts = [p.strip() for p in line.split('|') if p.strip()]
    if len(parts) >= 2:
        return ' | '.join([parts[0], missing] + parts[1:])
    return f'{line} | {missing}'


def clean_strapline(value):
    """A strapline as it will be used: one line, spacing tidied, nothing else.

    The wording, the capitals and the vertical bars are left exactly as they
    were typed. This only collapses stray whitespace and newlines, which would
    otherwise break a single-line field.
    """
    return ' '.join(str(value or '').split())


# ── Business rates ──────────────────────────────────────────────────────────
# The arithmetic is in business_rates.py, which knows nothing about the CRM.
# What follows is the part that does: which councils exist, which multipliers
# are on record, and the exact words that go on a set of particulars.

import business_rates as br

app.jinja_env.globals['br'] = br
app.jinja_env.globals['RELIEF_TYPES'] = br.RELIEF_TYPES
app.jinja_env.globals['MULTIPLIER_TYPES'] = br.MULTIPLIER_TYPES


# The councils the office deals with today. Seeded once; from then on they are
# edited in the CRM, so correcting a telephone number is not a code change.
# Anything added here later appears everywhere without a change to the
# property table or to the particulars template.
COUNCIL_SEED = [
    {'name': 'London Borough of Hammersmith & Fulham',
     'short_name': 'Hammersmith & Fulham',
     'phone': '020 8753 6681',
     'website': 'https://www.lbhf.gov.uk/business/business-rates'},
    {'name': 'Royal Borough of Kensington and Chelsea',
     'short_name': 'Kensington and Chelsea',
     'phone': '020 7361 2828',
     'website': 'https://www.rbkc.gov.uk/business/business-rates'},
]


# England's published multipliers. Seeded so the calculator works out of the
# box, and left UNVERIFIED on purpose: `verified_on` stays empty until somebody
# in the office has checked the figure against gov.uk and said so. The screen
# says plainly when a multiplier has not been verified.
#
# rv_min / rv_max are in pence and rv_max is exclusive, so the small business
# multiplier covers rateable values below £51,000.
MULTIPLIER_SEED = [
    ('2023/24', 'Standard multiplier',       'Standard',       51200, 5_100_000, None),
    ('2023/24', 'Small business multiplier', 'Small business', 49900, None, 5_100_000),
    ('2024/25', 'Standard multiplier',       'Standard',       54600, 5_100_000, None),
    ('2024/25', 'Small business multiplier', 'Small business', 49900, None, 5_100_000),
    ('2025/26', 'Standard multiplier',       'Standard',       55500, 5_100_000, None),
    ('2025/26', 'Small business multiplier', 'Small business', 49900, None, 5_100_000),
]
MULTIPLIER_SOURCE = 'https://www.gov.uk/calculate-your-business-rates'


def seed_rates_reference():
    """Put the councils and multipliers on record if they are not already.

    Idempotent and non-destructive: a row that already exists is left exactly
    as it is, so an office correction is never overwritten by a later deploy.
    """
    added = {'councils': 0, 'multipliers': 0}
    for row in COUNCIL_SEED:
        if not Council.query.filter_by(name=row['name']).first():
            db.session.add(Council(**row))
            added['councils'] += 1
    for tax_year, name, kind, value, rv_min, rv_max in MULTIPLIER_SEED:
        exists = RatesMultiplier.query.filter_by(tax_year=tax_year, name=name).first()
        if exists:
            continue
        starts, ends = br.tax_year_bounds(tax_year)
        db.session.add(RatesMultiplier(
            tax_year=tax_year, name=name, multiplier_type=kind, value=value,
            rv_min=rv_min, rv_max=rv_max, starts_on=starts, ends_on=ends,
            source=MULTIPLIER_SOURCE, verified_on=None))
        added['multipliers'] += 1
    if added['councils'] or added['multipliers']:
        db.session.commit()
        app.logger.info('Rates reference seeded: %s', added)
    return added


def councils(include=None):
    """Every council that may be chosen, plus one that is no longer active if
    a property still points at it — so an old record still reads correctly."""
    rows = Council.query.filter_by(active=True).order_by(Council.name).all()
    if include and include not in rows:
        kept = Council.query.get(include) if isinstance(include, int) else include
        if kept:
            rows = sorted(rows + [kept], key=lambda c: c.name)
    return rows


def multiplier_rows(tax_year):
    """The multipliers on record for a tax year, as plain dictionaries."""
    rows = (RatesMultiplier.query
            .filter_by(tax_year=tax_year, active=True)
            .order_by(RatesMultiplier.name).all())
    return [r.as_row() for r in rows]


def suggest_multiplier_for(tax_year, rateable_value_pence, property_type=None):
    """What the CRM would propose for this property, or nothing.

    A proposal, shown with its reasoning and always overridable. It uses the
    rateable value and the property type only — never anything about the
    occupier, which the CRM does not know and must not assume.
    """
    return br.suggest_multiplier(multiplier_rows(tax_year),
                                 rateable_value_pence, property_type)


# ── What goes on a brochure ─────────────────────────────────────────────────

def brochure_money(pence):
    """A rates figure as a brochure writes it: £14,561, not £14,561.00.

    The pennies are kept everywhere they matter — in the calculation, in the
    saved record and on the screens — and dropped only here, where a qualified
    estimate does not pretend to that precision.
    """
    from decimal import Decimal, ROUND_HALF_UP
    if pence is None:
        return None
    pounds = (Decimal(int(pence)) / 100).quantize(Decimal('1'),
                                                  rounding=ROUND_HALF_UP)
    return f'£{pounds:,}'


def rates_audience(instruction):
    """Who a rates note addresses, from how the property is being marketed.

    Sale particulars must not talk only to tenants.
    """
    if instruction and 'FOR SALE' in instruction and 'TO LET' in instruction:
        return 'Prospective purchasers and tenants'
    if instruction and 'FOR SALE' in instruction:
        return 'Prospective purchasers and occupiers'
    return 'Prospective tenants'


def council_the(name):
    """A council's name with its article, so a sentence reads properly.

    "the London Borough of Hammersmith & Fulham" is right; "the Hounslow
    Council" is not. Rather than write "the" into each sentence and get it
    wrong for the next council added, the article belongs to the name.
    """
    text = (name or '').strip()
    if not text:
        return text
    lower = text.lower()
    if lower.startswith('the '):
        return text
    for prefix in ('london borough', 'royal borough', 'borough of', 'city of',
                   'city and', 'council of', 'corporation of', 'county of',
                   'district of', 'isles of'):
        if lower.startswith(prefix):
            return f'the {text}'
    return text


def rates_paragraph(prop, instruction=None):
    """The business rates note, exactly as it will be printed.

    The figure is always the CRM's own estimate and is always called one. The
    brochure never suggests the council has supplied or confirmed it, because
    the council has not: this is the CRM's arithmetic on a rateable value.

    Three cases:
      - there is an estimate, so it is quoted and qualified;
      - the council is known but there is no estimate, so the reader is sent to
        the council rather than shown a made-up figure or a nought;
      - the council is not known either, so nothing is claimed and no council's
        details are printed.

    Internal calculation notes never appear here.
    """
    audience = rates_audience(instruction)
    council = prop.council if prop else None
    amount = prop.rates_for_brochure if prop else None

    if council and amount is not None:
        name = council_the(council.name)
        phone = (council.phone or '').strip()
        line = (f'The estimated rates payable for the current year are '
                f'{brochure_money(amount)}, subject to the occupier’s circumstances '
                f'and any applicable reliefs or adjustments. {audience} are '
                f'advised to confirm this information with {name}')
        return f'{line} by telephoning {phone}.' if phone else f'{line}.'

    if council:
        # No estimate. A nought would read as "no rates are payable", which is
        # a different and much more damaging claim than "we have not worked it
        # out", so nothing is quoted at all.
        name = council_the(council.name)
        phone = (council.phone or '').strip()
        line = (f'{audience} are advised to contact {name} to confirm the '
                f'business rates payable.')
        return f'{line} Their business rates team can be reached on {phone}.' if phone else line

    # Guessing a council from a postcode would put another borough's telephone
    # number on a brochure. Nothing is guessed.
    return (f'{audience} are advised to make their own enquiries with the '
            f'relevant local authority to confirm the business rates payable.')


def rates_summary(prop):
    """Everything the rates screens and the particulars preview need to show.

    Read only. It reports what is on record, including that nothing is.
    """
    current = prop.current_rates if prop else None
    amount = prop.rates_for_brochure if prop else None
    multiplier = None
    if current and current.multiplier_value is not None:
        multiplier = {
            'value': br.multiplier_str(current.multiplier_value),
            'name': current.multiplier_name,
            'type': current.multiplier_type,
            'overridden': bool(current.multiplier_overridden),
            'reason': current.override_reason,
        }
    return {
        'council': prop.council if prop else None,
        'council_name': prop.council.name if prop and prop.council else None,
        'council_phone': (prop.council.phone if prop and prop.council else None),
        'tax_year': current.tax_year if current else None,
        'rateable_value': current.rateable_value if current else None,
        'rateable_value_display': br.money(current.rateable_value) if current else None,
        'multiplier': multiplier,
        'base': current.base_payable if current else None,
        'estimated': current.estimated_payable if current else None,
        'estimated_display': (br.money(current.estimated_payable)
                              if current and current.estimated_payable is not None else None),
        'monthly_display': (br.money(current.monthly_payable)
                            if current and current.estimated_payable is not None else None),
        'calculated_on': current.calculated_on if current else None,
        'calculated_by': current.calculated_by if current else None,
        'brochure_amount': brochure_money(amount) if amount is not None else None,
        'history': [c for c in (prop.rates_calculations if prop else []) if not c.is_current],
    }


def rates_form_context():
    """The years and multipliers the calculator may offer."""
    return {
        'tax_years': br.tax_year_options(date.today()),
        'multipliers': (RatesMultiplier.query.filter_by(active=True)
                        .order_by(RatesMultiplier.tax_year.desc(),
                                  RatesMultiplier.name).all()),
    }


app.jinja_env.globals['rates_summary'] = rates_summary
app.jinja_env.globals['rates_paragraph'] = rates_paragraph
app.jinja_env.globals['councils'] = councils


def property_address_line(prop):
    """The address as it should read on a brochure.

    The postcode is added only when the address does not already end with it —
    plenty of records have it typed into the address line, and repeating it
    reads as carelessness on a marketing document.
    """
    if not prop:
        return ''
    address = (prop.address or '').strip().rstrip(',').strip()
    postcode = (prop.postcode or '').strip()
    if not postcode:
        return address
    if not address:
        return postcode
    squash = lambda v: v.replace(' ', '').replace(',', '').upper()
    if squash(address).endswith(squash(postcode)):
        return address
    return f'{address}, {postcode}'


def particulars_data(project):
    """Everything the particulars need, gathered from the CRM.

    Read only. Nothing is written back, nothing is invented, and nothing
    confidential goes near a marketing document: no notes, no commission, no
    client contact details, no activity history.
    """
    import particulars as pp
    prop = project.property
    listing = (project.project_listings[0]
               if getattr(project, 'project_listings', None) else None)

    def first(*values):
        for v in values:
            if v not in (None, '', 0):
                return v
        return None

    # Rent and price are different things and are never merged into one line.
    # A custom wording — "Offers in excess of…", "£25 per sq ft" — is the
    # office's own and is used as written.
    instruction = marketing_instruction(project, listing)
    for_sale = bool(instruction and 'FOR SALE' in instruction)
    to_let = bool(instruction and 'TO LET' in instruction)

    def money_or_application(value, custom, on_application):
        if custom:
            return clean_strapline(custom)
        if value:
            return f'£{float(value):,.0f}'
        return 'Upon application' if on_application else None

    rent = price_to_buy = None
    if listing:
        if to_let:
            rent = money_or_application(
                listing.listing_price if listing.listing_price_unit != 'sale' else None,
                listing.price_display if listing.listing_price_unit != 'sale' else None,
                listing.rent_on_application)
            if rent and listing.listing_price and listing.listing_price_unit == 'pa' \
                    and not listing.price_display:
                rent = f'{rent} per annum'
            elif rent and listing.listing_price_unit == 'pcm' and not listing.price_display:
                rent = f'{rent} per calendar month'
        if for_sale:
            price_to_buy = money_or_application(
                listing.sale_price or (listing.listing_price
                                       if listing.listing_price_unit == 'sale' else None),
                listing.sale_price_display or (
                    listing.price_display if listing.listing_price_unit == 'sale' else None),
                False)

    size = first(getattr(listing, 'total_size', None),
                 getattr(listing, 'size', None),
                 getattr(prop, 'size', None))
    size_line = None
    if size:
        size_line = f'Approx {size:,.0f} sq ft – {size * 0.092903:,.0f} sq m'

    earner = User.query.get(project.fee_earner_id) if project.fee_earner_id else None
    contact_lines = []
    if earner:
        contact_lines.append(earner.display_name)
    contact_lines.append(f"T: {pp.COMPANY['phone']}")
    # Only a real address is printed. Making one up from a username would put
    # an address on a marketing document that nobody reads.
    if earner and getattr(earner, 'email', None):
        contact_lines.append(earner.email)
    contact_lines.append(pp.COMPANY['website'])

    # The strapline is the headline. It is the one line the office has chosen
    # for this property, and the same line Zoopla is sent, so the brochure and
    # the portal never say different things. With none written, the type and
    # the instruction stand in on the brochure only — Zoopla is sent nothing
    # rather than something invented.
    headline = clean_strapline(getattr(listing, 'strapline', None)) or ' / '.join(
        [p for p in [(prop.property_type if prop else None),
                     (project.instruction_type or '').replace(' – Available', '')]
         if p])

    return {
        'headline': cover_wording(
            getattr(listing, 'strapline', None) or headline, instruction
        ) or headline or 'Property',
        'address': property_address_line(prop),
        'size_line': size_line,
        'description': first(getattr(listing, 'blurb', None),
                             getattr(prop, 'description', None)),
        'location': first(getattr(listing, 'location_description', None),
                          getattr(project, 'location_description', None)),
        'terms': getattr(listing, 'key_terms', None),
        'instruction': instruction,
        'cover_line': cover_wording(getattr(listing, 'strapline', None), instruction),
        'rent': rent,
        'price_to_buy': price_to_buy,
        'for_sale': for_sale,
        'to_let': to_let,
        'rates': rates_paragraph(prop, instruction) if prop else None,
        'rates_council': (prop.council.name if prop and prop.council else None),
        'rates_phone': (prop.council.phone if prop and prop.council else None),
        'rates_amount': (brochure_money(prop.rates_for_brochure)
                         if prop and prop.rates_for_brochure is not None else None),
        'rateable_value_note': (
            'Included' if getattr(listing, 'rateable_value_na', False) else
            (f"Rateable value {money_gbp(listing.rateable_value)}"
             if listing and listing.rateable_value else None)),
        'service_charge': first(
            getattr(listing, 'service_charge_comment', None),
            ('Included' if getattr(listing, 'service_charge_na', False) else
             (f"{money_gbp(listing.service_charge)} per sq ft"
              if listing and listing.service_charge else None))),
        'epc': getattr(listing, 'epc_band', None),
        'accommodation': size_line,
        'specification': getattr(listing, 'build_status', None),
        'use_class': getattr(listing, 'use_class', None),
        'transport': None,
        'viewing': None,
        'map': None,
        'floorplan': (listing.floor_plan_data if listing and
                      getattr(listing, 'floor_plan_data', None) else None),
        'contact_lines': contact_lines,
        'fee_earner': earner.display_name if earner else None,
        'strapline': clean_strapline(getattr(listing, 'strapline', None)),
    }


PARTICULARS_ESSENTIALS = [
    ('address', 'Property address'), ('description', 'Description'),
    ('location', 'Location'), ('size_line', 'Floor area'),
    ('fee_earner', 'Fee earner'),
]


def particulars_gaps(data, photo_count):
    """What is missing that a brochure really ought to carry.

    Reported so somebody can decide, never filled in. A particulars document
    with an invented figure on it would be worse than one with a gap.
    """
    missing = [label for key, label in PARTICULARS_ESSENTIALS if not data.get(key)]
    # What a brochure needs depends on how the property is being marketed. A
    # letting wants a rent; a sale wants a price; a unit offered both ways
    # wants both, and neither figure ever stands in for the other.
    if not data.get('instruction'):
        missing.append('Marketing instruction (For Sale or To Let)')
    if data.get('to_let') and not data.get('rent'):
        missing.append('Rent')
    if data.get('for_sale') and not data.get('price_to_buy'):
        missing.append('Sale price')
    # Without a billing authority the brochure cannot name a council or print a
    # number, so it falls back to a general disclaimer. That is a decision for
    # somebody to make knowingly, not something to discover afterwards.
    if not data.get('rates_council'):
        missing.append('Local authority (the rates note will name no council)')
    if photo_count == 0:
        missing.append('Photographs')
    return missing


@app.route('/projects/<int:id>/particulars', methods=['GET'])
@requires('edit')
def particulars_start(id):
    """Choose a format, choose the photographs, see what is missing."""
    project = Project.query.get_or_404(id)
    listing = (project.project_listings[0]
               if getattr(project, 'project_listings', None) else None)
    photos = sorted(listing.photos, key=lambda p: (p.sort_order or 0, p.id)) \
        if listing else []
    data = particulars_data(project)
    return render_template(
        'projects/particulars.html', project=project, listing=listing,
        photos=photos, data=data,
        rates=rates_summary(project.property),
        gaps=particulars_gaps(data, len(photos)),
        have_font=__import__('particulars').HAVE_MUSTICA)


def _particulars_bytes(project, pages, photo_ids):
    """Render the document. One path, used by preview and by saving."""
    import particulars as pp
    listing = (project.project_listings[0]
               if getattr(project, 'project_listings', None) else None)
    chosen = []
    if listing:
        by_id = {p.id: p for p in listing.photos}
        seen = set()
        for raw in photo_ids:
            pid = int(raw) if str(raw).isdigit() else None
            # A photograph is never used twice, however the list arrives.
            if pid in by_id and pid not in seen:
                seen.add(pid)
                chosen.append(by_id[pid].file_data)
    data = particulars_data(project)
    return pp.build(data, chosen, pages), pp.filename_for(data['address'], pages)


@app.route('/projects/<int:id>/particulars/preview', methods=['POST'])
@requires('edit')
def particulars_preview(id):
    """The document itself, for looking at before it is kept."""
    project = Project.query.get_or_404(id)
    pages = 4 if request.form.get('pages') == '4' else 2
    try:
        pdf, name = _particulars_bytes(project, pages,
                                       request.form.getlist('photo_ids'))
    except Exception:
        app.logger.exception('Could not build particulars for project %s', id)
        abort(500, description='The particulars could not be produced.')
    from flask import send_file
    return send_file(io.BytesIO(pdf), mimetype='application/pdf',
                     download_name=name, as_attachment=False)


@app.route('/projects/<int:id>/particulars/download', methods=['POST'])
@requires('edit')
def particulars_download(id):
    project = Project.query.get_or_404(id)
    pages = 4 if request.form.get('pages') == '4' else 2
    pdf, name = _particulars_bytes(project, pages, request.form.getlist('photo_ids'))
    audit('export', entity='Project', entity_id=id,
          detail=f'{pages}-page particulars downloaded')
    from flask import send_file
    return send_file(io.BytesIO(pdf), mimetype='application/pdf',
                     download_name=name, as_attachment=True)


@app.route('/projects/<int:id>/particulars/save', methods=['POST'])
@requires('edit')
def particulars_save(id):
    """Keep the document against the instruction's brochure.

    An existing brochure is never replaced without being asked: the choice is
    made on the page and carried here, and either way the change is recorded.
    """
    project = Project.query.get_or_404(id)
    listing = (project.project_listings[0]
               if getattr(project, 'project_listings', None) else None)
    if listing is None:
        flash('This instruction has no website listing to attach a brochure to.',
              'error')
        return redirect(url_for('project_detail', id=id))

    pages = 4 if request.form.get('pages') == '4' else 2
    pdf, name = _particulars_bytes(project, pages, request.form.getlist('photo_ids'))

    replacing = bool(listing.brochure_filename)
    choice = (request.form.get('existing') or '').strip()
    if replacing and choice not in ('replace', 'keep'):
        flash('Choose whether to replace the current brochure or keep it.', 'error')
        return redirect(url_for('particulars_start', id=id))

    if replacing and choice == 'keep':
        # The previous document stays; this one is offered as a download so
        # nothing already attached is disturbed.
        audit('export', entity='Project', entity_id=id,
              detail=f'{pages}-page particulars kept alongside the existing brochure')
        from flask import send_file
        return send_file(io.BytesIO(pdf), mimetype='application/pdf',
                         download_name=name, as_attachment=True)

    was = listing.brochure_filename
    listing.brochure_data = pdf
    listing.brochure_filename = name
    listing.brochure_size = len(pdf)
    db.session.commit()
    audit('create', entity='Project', entity_id=id,
          detail=(f'{pages}-page particulars saved as the brochure'
                  + (f', replacing {was}' if was else '')))
    flash('Particulars saved to the brochure.', 'success')
    return redirect(url_for('project_detail', id=id) + '#tab-documents')


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
            fee_earner_id=_fid(request.form.get('fee_earner_id')) or project.fee_earner_id,
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
        s.fee_earner_id = _fid(request.form.get('fee_earner_id'))
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
    setf('strapline', 'strapline')    # Headline: particulars and Zoopla summary
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
            # A commercial unit is often offered both ways. The rent lives in
            # listing_price and the sale price in sale_price; mirroring the
            # sale price over the top used to destroy the rent, which was
            # recorded nowhere else. It is only mirrored when the unit is for
            # sale alone.
            sale_only = l.set_as_for_sale and not l.set_as_to_let
            if sale_only and l.sale_price:
                l.listing_price      = l.sale_price
                l.listing_price_unit = 'sale'
            elif l.listing_price:
                l.listing_price_unit = 'pa'    # commercial rent quoted per annum
            elif l.set_as_for_sale and l.sale_price:
                # To let as well, but with no rent quoted yet.
                l.listing_price      = l.sale_price
                l.listing_price_unit = 'sale'
            else:
                l.listing_price_unit = 'poa'

            # The custom wording is the user's to set and to clear. It was
            # falling back to whatever was there before, so an emptied box
            # quietly restored the old string.
            if 'sale_price_display' in form and sale_only:
                l.price_display = _ftext(form.get('sale_price_display'))
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


# ── Microsoft 365 ────────────────────────────────────────────────────────────

@app.route('/admin/microsoft')
@requires('admin')
def admin_microsoft():
    """What is connected to Microsoft 365, and what it is keeping in step."""
    from datetime import timedelta as _td
    mirrored = DiaryEvent.query.filter(DiaryEvent.ms_event_id.isnot(None)).count()
    from_outlook = DiaryEvent.query.filter_by(created_by='Outlook').count()
    contacts_shared = Contact.query.filter(Contact.ms_contact_id.isnot(None)).count()
    last = (DiaryEvent.query.filter(DiaryEvent.synced_at.isnot(None))
            .order_by(DiaryEvent.synced_at.desc()).first())
    return render_template(
        'admin/microsoft.html',
        status=ms_graph.connection_status(),
        mailbox=ms_graph.MAILBOX,
        mirrored=mirrored, from_outlook=from_outlook,
        contacts_shared=contacts_shared,
        contacts_total=Contact.query.count(),
        last_sync=last.synced_at if last else None,
        email_configured=_email_configured())


def _email_configured():
    try:
        from email_sync import check_configured
        return check_configured()
    except Exception:
        return False


@app.route('/admin/microsoft/calendar-pull', methods=['POST'])
@requires('admin')
def admin_ms_calendar_pull():
    """Bring appointments made in Outlook into the diary."""
    report = ms_sync.pull_events(app, db, DiaryEvent)
    if report['error']:
        flash(f"Could not read the calendar: {report['error']}", 'danger')
    else:
        flash(f"Calendar checked: {report['added']} new, "
              f"{report['updated']} updated.", 'success')
        audit('sync', entity='DiaryEvent', detail='pulled from Outlook')
    return redirect(url_for('admin_microsoft'))


@app.route('/admin/microsoft/calendar-push', methods=['POST'])
@requires('admin')
def admin_ms_calendar_push():
    """Put every CRM appointment into Outlook, for a first run."""
    sent = failed = 0
    for ev in DiaryEvent.query.filter(DiaryEvent.created_by != 'Outlook').all():
        ok, _ = ms_sync.push_event(app, db, ev)
        sent += 1 if ok else 0
        failed += 0 if ok else 1
    flash(f'{sent} appointment(s) sent to Outlook'
          + (f', {failed} could not be sent.' if failed else '.'),
          'success' if not failed else 'warning')
    audit('sync', entity='DiaryEvent', detail='pushed to Outlook')
    return redirect(url_for('admin_microsoft'))


@app.route('/admin/microsoft/contacts-push', methods=['POST'])
@requires('admin')
def admin_ms_contacts_push():
    """Mirror the address book into Outlook contacts."""
    report = ms_sync.push_all_contacts(app, db, Contact)
    if report['error']:
        flash(report['error'], 'danger')
    else:
        flash(f"{report['sent']} contact(s) shared with Outlook"
              + (f", {report['failed']} could not be." if report['failed'] else '.'),
              'success' if not report['failed'] else 'warning')
        audit('sync', entity='Contact', detail='pushed to Outlook')
    return redirect(url_for('admin_microsoft'))


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
    # What Zoopla will be told, and anything that would stop it being said.
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

    from markupsafe import escape as _esc

    def _summary_cell(listing):
        """What Zoopla will be sent as the summary, and why it might refuse."""
        problems, text = zf.summary_problems(getattr(listing, 'strapline', None))
        if problems:
            return ('<span style="color:#b3463c">'
                    + '<br>'.join(_esc(p) for p in problems) + '</span>')
        return f'<span style="color:#1f2333">{_esc(text)}</span>'

    live_rows = ''.join(
        f'<tr><td>CR-{l.id}</td><td>{_esc(_t(l))}</td>'
        f'<td>{_summary_cell(l)}</td>'
        f'<td>{l.listing_status or "available"}</td>'
        f'<td>{len(list(l.photos or []))}</td></tr>' for l in live) \
        or '<tr><td colspan=5 style="color:#6b7280">No listings toggled for Zoopla yet.</td></tr>'

    # Anything that would stop a listing going out, said before it is sent.
    blocked = [(l, zf.summary_problems(getattr(l, 'strapline', None))[0])
               for l in live]
    blocked = [(l, p) for l, p in blocked if p]
    warning = ''
    if blocked:
        items = ''.join(
            f'<li>CR-{l.id} {_esc(_t(l))}: ' + '; '.join(_esc(x) for x in p) + '</li>'
            for l, p in blocked)
        warning = (
            '<div style="background:#fff8e6;border:1px solid #e8d5a3;'
            'border-left:3px solid #b5762c;border-radius:3px;padding:12px 14px;'
            'margin:14px 0"><b>Zoopla summary missing or too long</b>'
            '<p style="margin:6px 0 8px;color:#4a5568">The summary comes from '
            'the Strapline on the instruction. Nothing is invented and the '
            'marketing description is never used instead — amend the strapline '
            f'in the Marketing section.</p><ul style="margin:0 0 0 18px">{items}</ul></div>')
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
{warning}
<h3 style="margin-bottom:4px">Listings going to Zoopla ({len(live)})</h3>
{takedown_note}
<table style="width:100%;border-collapse:collapse;font-size:14px">
<thead><tr style="text-align:left;border-bottom:2px solid #0e1f44">
<th>Ref</th><th>Listing</th><th>Zoopla Summary</th><th>Status</th><th>Photos</th></tr></thead>
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
    # A listing whose summary Zoopla will not take is held back rather than
    # sent in a form nobody chose. The rest still go.
    held = [(l, zf.summary_problems(getattr(l, 'strapline', None))[0]) for l in live]
    held = [(l, p) for l, p in held if p]
    sending = [l for l in live if l not in {h[0] for h in held}]

    blm_text, media_files = zf.generate_feed(sending + takedown, cfg['branch_id'])
    ok, msg = zf.upload_feed(blm_text, media_files, cfg)
    audit('publish' if ok else 'denied', entity='Listing',
          detail=(f'Zoopla feed: {len(sending)} sent, {len(held)} held back'
                  + ('' if ok else f' — {msg[:180]}')))
    if held:
        msg += (' ' + f'{len(held)} listing(s) were not sent because their '
                'Zoopla summary is missing or too long.')
    colour = '#1b7a3f' if ok else '#b91c1c'
    heading = 'Feed pushed to Zoopla' if ok else 'Push failed'
    return f'''<!doctype html><meta charset=utf-8>
<body style="font-family:system-ui,Arial;max-width:640px;margin:60px auto;padding:0 20px;color:#111">
<h2 style="color:{colour}">{heading}</h2>
<p>{msg}</p>
<p style="color:#6b7280;font-size:13px">Sent {len(sending)} live listing(s). Zoopla ingests the feed on its own schedule, so changes appear on the portal after their next pickup — not instantly.</p>
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
            ('source_contact',    'TEXT'), ('nda',              'BOOLEAN DEFAULT FALSE'),
            ('size_units',        'TEXT'), ('size_basis',       'TEXT'),
            ('demise_description','TEXT'), ('incentive_years',  'REAL'),
            ('headline_rate',     'REAL'), ('headline_rate_unit','TEXT'),
            ('net_rate',          'REAL'), ('next_break_date',  'TEXT'),
            ('no_break',          'BOOLEAN DEFAULT FALSE'),
            ('next_review_date',  'TEXT'), ('no_review',        'BOOLEAN DEFAULT FALSE'),
            ('review_type',       'TEXT'), ('repair',           'TEXT'),
            ('alienation',        'TEXT'), ('primary_use_class','TEXT'),
            ('lt_act',            'TEXT'), ('epc_rating',       'TEXT'),
            ('fitted',            'TEXT'),
            # Fee, invoicing and the parties behind them.
            ('reference',         'TEXT'), ('status',           'TEXT'),
            ('fee_earner',        'TEXT'), ('client',           'TEXT'),
            ('project_id',        'INTEGER'),
            ('agreed_value',      'REAL'), ('fee_type',         'TEXT'),
            ('fee_percent',       'REAL'), ('fixed_fee',        'REAL'),
            ('vat_rate',          'REAL'), ('invoice_number',   'TEXT'),
            ('invoice_date',      'DATE'), ('payment_due_date', 'DATE'),
            ('completion_date',   'DATE'), ('terms_agreed_date','DATE'),
            ('agreement_type',    'TEXT'),
            ('expected_completion_date', 'DATE'),
            ('solicitors_instructed_date', 'DATE'),
            ('client_solicitor',       'TEXT'),
            ('client_solicitor_firm',  'TEXT'),
            ('client_solicitor_email', 'TEXT'),
            ('client_solicitor_phone', 'TEXT'),
            ('other_solicitor',        'TEXT'),
            ('other_solicitor_firm',   'TEXT'),
            ('other_solicitor_email',  'TEXT'),
            ('other_solicitor_phone',  'TEXT'),
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
            conn.commit()

        # Give every transaction a reference. These are identifiers, not
        # figures: nothing about the money is guessed or filled in here.
        try:
            missing = Transaction.query.filter(
                db.or_(Transaction.reference.is_(None), Transaction.reference == '')
            ).order_by(Transaction.id).all()
            if missing:
                used = {r for (r,) in db.session.query(Transaction.reference)
                        .filter(Transaction.reference.isnot(None)).all()}
                nxt = 1
                for t in missing:
                    while f'TR-{nxt:04d}' in used:
                        nxt += 1
                    t.reference = f'TR-{nxt:04d}'
                    used.add(t.reference)
                db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception('Could not give transactions their references')

        # Organisations gain their commercial-agency fields. Every one is new
        # and nullable: nothing already recorded is read, moved or overwritten,
        # and the free-text landlord/client/tenant names on projects and
        # transactions are deliberately left exactly as they are.
        org_cols = [
            ('trading_name',    'TEXT'), ('legal_name',      'TEXT'),
            ('fee_earner',      'TEXT'), ('source',          'TEXT'),
            ('main_contact_id', 'INTEGER'),
            ('company_number',  'TEXT'), ('vat_number',      'TEXT'),
            ('registered_address', 'TEXT'), ('trading_address', 'TEXT'),
            ('companies_house_status', 'TEXT'),
            ('incorporated_on', 'DATE'), ('nature_of_business', 'TEXT'),
            ('aml_status',      'TEXT'), ('aml_reviewed_on', 'DATE'),
            ('beneficial_owners', 'TEXT'), ('verification_notes', 'TEXT'),
            ('marketing_consent', 'BOOLEAN DEFAULT FALSE'),
            ('accounts_contact', 'TEXT'), ('accounts_email',  'TEXT'),
            ('invoice_address', 'TEXT'), ('payment_terms',    'TEXT'),
            ('vat_status',      'TEXT'), ('accounts_notes',   'TEXT'),
        ]
        _add_columns('organisations', org_cols)

        # An organisation that already carried a single type keeps it, now as
        # one of the several it is allowed to hold. Nothing is invented: only
        # the type already on the record is carried across, and only once.
        try:
            for org in Organisation.query.filter(Organisation.org_type.isnot(None)).all():
                if org.org_type and org.org_type not in {t.name for t in org.types}:
                    db.session.add(OrganisationType(organisation_id=org.id,
                                                    name=org.org_type))
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception('Could not carry organisation types across')

        # ── Staff assignment ────────────────────────────────────────────
        # Records now point at a user rather than holding a typed name. The
        # typed name stays in its own column: it is the evidence for the link,
        # and the only record of anything that could not be identified.
        _add_columns('users', [('full_name', 'TEXT'), ('email', 'TEXT'),
                               ('active', 'BOOLEAN DEFAULT TRUE'),
                               ('can_earn_fees', 'BOOLEAN DEFAULT TRUE')])
        for table in ('transactions', 'organisations', 'enquiries', 'projects',
                      'project_services', 'contacts'):
            _add_columns(table, [('fee_earner_id', 'INTEGER')])
        for table in ('projects', 'properties'):
            _add_columns(table, [('client_contact_id', 'INTEGER')])
        _add_columns('listings', [('strapline', 'TEXT')])

        try:
            _restyle_transaction_statuses()
        except Exception:
            db.session.rollback()
            app.logger.exception('Could not bring transaction statuses to one casing')

        try:
            _name_the_users()
            _link_fee_earners()
        except Exception:
            db.session.rollback()
            app.logger.exception('Could not map the fee earners')

        cont_cols = [
            ('req_category', 'TEXT'),      ('req_property_type', 'TEXT'),
            ('req_use_class', 'TEXT'),     ('req_area', 'TEXT'),
            ('req_size_min', 'REAL'),      ('req_size_max', 'REAL'),
            ('req_budget_min', 'REAL'),    ('req_budget_max', 'REAL'),
            ('req_budget_unit', 'TEXT'),   ('req_notes', 'TEXT'),
        ]
        _add_columns('contacts', cont_cols)


def _migrate_listing_columns():
    """Add new listing columns to existing properties table if missing."""
    from sqlalchemy import text, inspect
    with app.app_context():
        insp = inspect(db.engine)
        existing = {col['name'] for col in insp.get_columns('properties')}
        new_cols = [
            ('website_listed',     'BOOLEAN DEFAULT FALSE'),
            ('website_category',   'TEXT'),
            ('listing_status',     'TEXT'),
            ('featured',           'BOOLEAN DEFAULT FALSE'),
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


def _add_columns(table, columns):
    """Add missing columns to a table that already exists.

    Each one runs on its own. Postgres aborts an entire transaction when a
    single statement fails, so a batch would lose every later column to one
    bad definition — and, because this runs at boot, would take the whole CRM
    down with it. A column that cannot be added is logged loudly and the rest
    still go in.
    """
    from sqlalchemy import inspect as _inspect, text as _text
    try:
        existing = {c['name'] for c in _inspect(db.engine).get_columns(table)}
    except Exception:
        app.logger.exception('Could not read the columns of %s', table)
        return []
    added, failed = [], []
    for name, ddl in columns:
        if name in existing:
            continue
        try:
            with db.engine.begin() as conn:
                conn.execute(_text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
            added.append(name)
        except Exception as e:
            failed.append(name)
            app.logger.error('Could not add %s.%s (%s): %s', table, name, ddl, e)
    if added:
        app.logger.info('Added to %s: %s', table, ', '.join(added))
    if failed:
        app.logger.error('MIGRATION INCOMPLETE on %s: %s', table, ', '.join(failed))
    return failed


def _restyle_transaction_statuses():
    """Bring stored statuses to the one casing the CRM now uses.

    Only these exact strings are rewritten, so a status nobody recognises is
    left exactly as it is rather than being guessed at. Nothing about a
    transaction's money or dates is touched.
    """
    renames = {
        'In progress': 'In Progress',
        'Terms agreed': 'Terms Agreed',
        'Solicitors instructed': 'Solicitors Instructed',
        'Commission billed': 'Commission Billed',
        'Part paid': 'Part Paid',
        'Fallen through': 'Fallen Through',
    }
    changed = 0
    for old, new in renames.items():
        rows = Transaction.query.filter(Transaction.status == old).all()
        for row in rows:
            row.status = new
            changed += 1
    if changed:
        db.session.commit()
        app.logger.info('Transaction statuses: %s brought to one casing.', changed)
    odd = {t.status for t in Transaction.query.all()
           if t.status and t.status not in TRANSACTION_STATUSES}
    if odd:
        app.logger.warning(
            'Transaction statuses the CRM does not recognise, left as they are — %s',
            ', '.join(sorted(odd)))
    return changed


def _name_the_users():
    """Give the office account the name a client would recognise.

    Only filled in where it is blank, so a name entered by hand is never
    replaced. With a single account, that account is Benjamin Cowan.
    """
    people = User.query.order_by(User.id).all()
    unnamed = [u for u in people if not (u.full_name or '').strip()]
    if len(people) == 1 and unnamed:
        unnamed[0].full_name = 'Benjamin Cowan'
    for user in people:
        if user.active is None:
            user.active = True
        if user.can_earn_fees is None:
            user.can_earn_fees = True
    db.session.commit()


def _fee_earner_aliases(user):
    """The written forms of a name that certainly mean this person.

    "Benjamin Cowan", "B Cowan", "B. Cowan", "BC" and the username. Anything
    else is somebody the CRM cannot identify, and is left alone.
    """
    forms = {user.username}
    full = (user.full_name or '').strip()
    if full:
        forms.add(full)
        parts = full.split()
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            forms.update({f'{first[0]} {last}', f'{first[0]}. {last}',
                          f'{first[0]}{last}', f'{last}, {first}',
                          ''.join(p[0] for p in parts)})
    return {re.sub(r'[^a-z0-9]', '', f.lower()) for f in forms if f}


def _link_fee_earners():
    """Point existing records at a user where the name certainly matches.

    A name matching exactly one person is linked. A name matching nobody, or
    more than one person, is left as it is and reported in the log — guessing
    would put somebody else's name on a client's instruction.
    """
    people = User.query.all()
    lookup = {}
    for user in people:
        for alias in _fee_earner_aliases(user):
            lookup.setdefault(alias, set()).add(user.id)

    pairs = [(Transaction, 'fee_earner'), (Organisation, 'fee_earner'),
             (Enquiry, 'fee_earner'), (Project, 'fee_earner'),
             (ProjectService, 'fee_earner'), (Contact, 'assigned_agent')]
    linked, unknown, ambiguous = 0, set(), set()
    for model, field in pairs:
        for row in model.query.filter(getattr(model, field).isnot(None)).all():
            if row.fee_earner_id:
                continue
            typed = getattr(row, field) or ''
            key = re.sub(r'[^a-z0-9]', '', typed.lower())
            if not key:
                continue
            found = lookup.get(key, set())
            if len(found) == 1:
                row.fee_earner_id = next(iter(found))
                linked += 1
            elif len(found) > 1:
                ambiguous.add(typed)
            else:
                unknown.add(typed)
    db.session.commit()
    if linked:
        app.logger.info('Fee earners: linked %s record(s) to a user.', linked)
    if unknown:
        app.logger.warning(
            'Fee earners: %s name(s) match no user account and were left as '
            'typed — %s', len(unknown), ', '.join(sorted(unknown)[:20]))
    if ambiguous:
        app.logger.warning(
            'Fee earners: %s name(s) match more than one user and were left as '
            'typed — %s', len(ambiguous), ', '.join(sorted(ambiguous)[:20]))
    return {'linked': linked, 'unknown': sorted(unknown),
            'ambiguous': sorted(ambiguous)}


def _migrate_rates_tables():
    """Business rates: the property's council and its confirmed figure.

    The three new tables come from db.create_all(). Only the columns added to
    the existing properties table need this. Booleans are given TRUE/FALSE
    rather than 1/0 — Postgres rejects an integer as a boolean default, and
    SQLite accepts it, so the wrong one passes every test and takes the live
    site down on deploy.
    """
    with app.app_context():
        _add_columns('properties', [
            ('council_id',             'INTEGER'),
            ('rates_confirmed',        'BOOLEAN DEFAULT FALSE'),
            ('rates_confirmed_amount', 'BIGINT'),
            ('rates_confirmed_on',     'DATE'),
            ('rates_confirmed_ref',    'TEXT'),
            ('rates_confirmed_by',     'TEXT'),
        ])
        try:
            seed_rates_reference()
        except Exception:
            db.session.rollback()
            app.logger.exception('Could not seed the councils and multipliers')


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
            ('ms_contact_id', 'TEXT'), ('ms_synced_at', 'TIMESTAMP'),
            ('lease_length',      'TEXT'),
            ('assigned_agent',    'TEXT'),
            ('last_contact_date', 'DATE'),
            ('next_follow_up',    'DATE'),
        ]
        _add_columns('contacts', cont_cols)
        _add_columns('organisations', [('status', "TEXT DEFAULT 'Prospect'")])


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
        _migrate_rates_tables()
        _ensure_default_user()
        if Property.query.count() == 0:
            import import_listings  # seeds the 32 website properties
            _seed_project_listings()
    app.run(debug=False, host='127.0.0.1', port=8080)

# GoHighLevel live sync (Cowan & Rutter sub-account)
import ghl_sync
ghl_sync.init(app, db, Contact=Contact, Enquiry=Enquiry, Project=Project)
