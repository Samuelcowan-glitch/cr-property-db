import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, date

app = Flask(__name__)
# Use DATABASE_URL from environment (Railway/Postgres) or fall back to local SQLite
_db_url = os.environ.get('DATABASE_URL', 'sqlite:///property.db')
# Railway gives postgres:// but SQLAlchemy needs postgresql://
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'cowan-rutter-property-db-2026')
db = SQLAlchemy(app)
CORS(app, resources={r'/api/*': {'origins': '*'}})


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

class Organisation(db.Model):
    __tablename__ = 'organisations'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    org_type = db.Column(db.String(50))   # Client, Agent, Solicitor, Developer, Investor, Other
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
    req_notes         = db.Column(db.Text)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class Enquiry(db.Model):
    __tablename__ = 'enquiries'
    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(255), nullable=False)
    enquiry_type = db.Column(db.String(50))
    status = db.Column(db.String(20), default='Open')  # Open, Won, Lost, On Hold
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
    # Follow-up tracking
    last_contact_date = db.Column(db.Date)
    next_follow_up    = db.Column(db.Date)

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


class EnquiryNote(db.Model):
    __tablename__ = 'enquiry_notes'
    id = db.Column(db.Integer, primary_key=True)
    enquiry_id = db.Column(db.Integer, db.ForeignKey('enquiries.id'), nullable=False)
    direction  = db.Column(db.String(20))  # inbound / outbound / note
    subject    = db.Column(db.String(255))
    body       = db.Column(db.Text, nullable=False)
    author     = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    enquiry = db.relationship('Enquiry', backref=db.backref('notes_chain', lazy=True,
                              cascade='all, delete-orphan',
                              order_by='EnquiryNote.created_at'))


FOLDER_LABELS = {
    'basis_of_appointment':  '06.01 Basis of Appointment',
    'correspondence':        '06.02 Correspondence',
    'documentation':         '06.03 Documentation',
    'financial_calculations':'06.05 Financial & Calculations',
    'photographs_images':    '06.07 Photographs & Images',
    'reports':               '06.09 Reports',
    'key_documents':         '06.11 Project Key Documents',
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
    key_contact       = db.Column(db.String(100))
    landlord_name     = db.Column(db.String(255))
    agent_assigned    = db.Column(db.String(100))

    documents = db.relationship('ProjectDocument', backref='project', lazy=True, cascade='all, delete-orphan')

    def docs_in_folder(self, folder):
        return [d for d in self.documents if d.folder == folder]


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


# ── Properties ─────────────────────────────────────────────────────────────

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

    # Follow-ups due today or overdue
    due_today = [e for e in open_enquiries if e.next_follow_up and e.next_follow_up <= today]

    recent_transactions = Transaction.query.order_by(Transaction.created_at.desc()).limit(10).all()
    enq_count = Enquiry.query.filter(Enquiry.status == 'Open').count()
    contact_count = Contact.query.count()

    return render_template('dashboard.html',
                           prop_count=prop_count,
                           trans_count=trans_count,
                           proj_count=proj_count,
                           contact_count=contact_count,
                           enq_count=enq_count,
                           active_listings=active_listings,
                           contacts=contacts,
                           open_enquiries=open_enquiries,
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
        def _parse_listing(form):
            lp = form.get('listing_price', '').strip()
            return dict(
                website_listed=bool(form.get('website_listed')),
                website_category=form.get('website_category') or None,
                listing_status=form.get('listing_status') or 'available',
                featured=bool(form.get('featured')),
                area=form.get('area') or None,
                use_class=form.get('use_class') or None,
                listing_price=float(lp) if lp else None,
                listing_price_unit=form.get('listing_price_unit') or 'poa',
                price_display=form.get('price_display') or None,
                beds=int(form.get('beds')) if form.get('beds','').strip() else None,
                baths=int(form.get('baths')) if form.get('baths','').strip() else None,
                lat=float(form.get('lat')) if form.get('lat','').strip() else None,
                lng=float(form.get('lng')) if form.get('lng','').strip() else None,
                photo_id=form.get('photo_id') or None,
                blurb=form.get('blurb') or None,
            )
        prop = Property(
            address=request.form['address'],
            postcode=request.form['postcode'].upper(),
            property_type=request.form.get('property_type'),
            size=float(size_raw) if size_raw else None,
            measurement_type=request.form.get('measurement_type'),
            description=request.form.get('description'),
            **_parse_listing(request.form),
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
        prop.address = request.form['address']
        prop.postcode = request.form['postcode'].upper()
        prop.property_type = request.form.get('property_type')
        size_raw = request.form.get('size', '').strip()
        prop.size = float(size_raw) if size_raw else None
        prop.measurement_type = request.form.get('measurement_type')
        prop.description = request.form.get('description')
        lp = request.form.get('listing_price', '').strip()
        prop.website_listed     = bool(request.form.get('website_listed'))
        prop.website_category   = request.form.get('website_category') or None
        prop.listing_status     = request.form.get('listing_status') or 'available'
        prop.featured           = bool(request.form.get('featured'))
        prop.area               = request.form.get('area') or None
        prop.use_class          = request.form.get('use_class') or None
        prop.listing_price      = float(lp) if lp else None
        prop.listing_price_unit = request.form.get('listing_price_unit') or 'poa'
        prop.price_display      = request.form.get('price_display') or None
        prop.beds               = int(request.form.get('beds')) if request.form.get('beds','').strip() else None
        prop.baths              = int(request.form.get('baths')) if request.form.get('baths','').strip() else None
        prop.lat                = float(request.form.get('lat')) if request.form.get('lat','').strip() else None
        prop.lng                = float(request.form.get('lng')) if request.form.get('lng','').strip() else None
        prop.photo_id           = request.form.get('photo_id') or None
        prop.blurb              = request.form.get('blurb') or None
        prop.residential_use    = request.form.get('residential_use') or None
        ls = request.form.get('listing_size', '').strip()
        prop.listing_size       = float(ls) if ls else None
        prop.listing_size_unit  = request.form.get('listing_size_unit') or None
        db.session.commit()
        flash('Property updated.', 'success')
        return redirect(url_for('property_detail', id=prop.id))
    return render_template('properties/form.html', prop=prop)


@app.route('/properties/<int:id>/delete', methods=['POST'])
def property_delete(id):
    prop = Property.query.get_or_404(id)
    db.session.delete(prop)
    db.session.commit()
    flash('Property deleted.', 'info')
    return redirect(url_for('properties_list'))


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
def transaction_delete(id):
    t = Transaction.query.get_or_404(id)
    prop_id = t.property_id
    db.session.delete(t)
    db.session.commit()
    flash('Transaction deleted.', 'info')
    return redirect(url_for('property_detail', id=prop_id))


# ── Projects ────────────────────────────────────────────────────────────────

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
    return render_template('projects/list.html', projects=projects, q=q, status=status)


@app.route('/projects/new', methods=['GET', 'POST'])
def project_new():
    properties = Property.query.order_by(Property.address).all()
    if request.method == 'POST':
        def parse_date(val):
            return datetime.strptime(val, '%Y-%m-%d').date() if val else None
        prop_id_raw = request.form.get('property_id')
        p = Project(
            property_id=int(prop_id_raw) if prop_id_raw else None,
            name=request.form['name'],
            project_ref=request.form.get('project_ref'),
            status=request.form.get('status', 'Active'),
            fee_earner=request.form.get('fee_earner'),
            client=request.form.get('client'),
            instruction_date=parse_date(request.form.get('instruction_date')),
            notes=request.form.get('notes'),
        )
        db.session.add(p)
        db.session.commit()
        flash('Project created.', 'success')
        return redirect(url_for('project_detail', id=p.id))
    prop_id = request.args.get('property_id')
    return render_template('projects/form.html', properties=properties, prop_id=prop_id, project=None)


@app.route('/projects/<int:id>')
def project_detail(id):
    project = Project.query.get_or_404(id)
    matches = match_contacts_to_property(project.property) if project.property else []
    return render_template('projects/detail.html', project=project,
                           folder_labels=FOLDER_LABELS, today=date.today(),
                           matches=matches)


@app.route('/projects/<int:id>/edit', methods=['GET', 'POST'])
def project_edit(id):
    project = Project.query.get_or_404(id)
    properties = Property.query.order_by(Property.address).all()
    if request.method == 'POST':
        def parse_date(val):
            return datetime.strptime(val, '%Y-%m-%d').date() if val else None
        prop_id_raw = request.form.get('property_id')
        project.property_id = int(prop_id_raw) if prop_id_raw else None
        project.name = request.form['name']
        project.project_ref = request.form.get('project_ref')
        project.status = request.form.get('status', 'Active')
        project.fee_earner = request.form.get('fee_earner')
        project.client = request.form.get('client')
        project.instruction_date  = parse_date(request.form.get('instruction_date'))
        project.notes             = request.form.get('notes')
        project.instruction_type  = request.form.get('instruction_type') or None
        project.available_from    = parse_date(request.form.get('available_from'))
        project.next_call         = parse_date(request.form.get('next_call'))
        project.client_phone      = request.form.get('client_phone') or None
        project.client_mobile     = request.form.get('client_mobile') or None
        project.client_email      = request.form.get('client_email') or None
        project.key_contact       = request.form.get('key_contact') or None
        project.landlord_name     = request.form.get('landlord_name') or None
        project.agent_assigned    = request.form.get('agent_assigned') or None
        fp = request.form.get('fee_percent', '').strip()
        ff = request.form.get('fee_fixed', '').strip()
        project.fee_percent       = float(fp) if fp else None
        project.fee_fixed         = float(ff) if ff else None
        db.session.commit()
        flash('Project updated.', 'success')
        return redirect(url_for('project_detail', id=project.id))
    return render_template('projects/form.html', properties=properties, prop_id=project.property_id, project=project)


@app.route('/projects/<int:id>/delete', methods=['POST'])
def project_delete(id):
    project = Project.query.get_or_404(id)
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
        return redirect(url_for('project_detail', id=id))
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
    return redirect(url_for('project_detail', id=id))


@app.route('/documents/<int:id>/delete', methods=['POST'])
def document_delete(id):
    doc = ProjectDocument.query.get_or_404(id)
    project_id = doc.project_id
    db.session.delete(doc)
    db.session.commit()
    flash('Document removed.', 'info')
    return redirect(url_for('project_detail', id=project_id))


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
    if q:
        query = query.filter(
            db.or_(Organisation.name.ilike(f'%{q}%'), Organisation.org_type.ilike(f'%{q}%'))
        )
    orgs = query.order_by(Organisation.name).all()
    return render_template('crm/organisations_list.html', orgs=orgs, q=q)


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
        )
        db.session.add(org)
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
        db.session.commit()
        flash('Organisation updated.', 'success')
        return redirect(url_for('organisation_detail', id=org.id))
    return render_template('crm/organisation_form.html', org=org)


@app.route('/organisations/<int:id>/delete', methods=['POST'])
def organisation_delete(id):
    org = Organisation.query.get_or_404(id)
    db.session.delete(org)
    db.session.commit()
    flash('Organisation deleted.', 'info')
    return redirect(url_for('organisations_list'))


# ── Contacts ─────────────────────────────────────────────────────────────────

@app.route('/contacts')
def contacts_list():
    q = request.args.get('q', '')
    query = Contact.query
    if q:
        query = query.filter(
            db.or_(Contact.first_name.ilike(f'%{q}%'), Contact.last_name.ilike(f'%{q}%'),
                   Contact.email.ilike(f'%{q}%'), Contact.contact_type.ilike(f'%{q}%'))
        )
    contacts = query.order_by(Contact.last_name, Contact.first_name).all()
    return render_template('crm/contacts_list.html', contacts=contacts, q=q)


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
        )
        db.session.add(c)
        db.session.commit()
        flash('Contact added.', 'success')
        return redirect(url_for('contact_detail', id=c.id))
    return render_template('crm/contact_form.html', contact=None, organisations=organisations)


@app.route('/contacts/<int:id>')
def contact_detail(id):
    contact = Contact.query.get_or_404(id)
    return render_template('crm/contact_detail.html', contact=contact)


@app.route('/contacts/<int:id>/edit', methods=['GET', 'POST'])
def contact_edit(id):
    contact = Contact.query.get_or_404(id)
    organisations = Organisation.query.order_by(Organisation.name).all()
    if request.method == 'POST':
        org_id_raw = request.form.get('organisation_id')
        contact.first_name = request.form['first_name']
        contact.last_name = request.form['last_name']
        contact.job_title = request.form.get('job_title')
        contact.organisation_id = int(org_id_raw) if org_id_raw else None
        contact.phone = request.form.get('phone')
        contact.mobile = request.form.get('mobile')
        contact.email = request.form.get('email')
        contact.contact_type = request.form.get('contact_type')
        contact.notes             = request.form.get('notes')
        contact.req_category      = request.form.get('req_category') or None
        contact.req_property_type = request.form.get('req_property_type') or None
        contact.req_use_class     = request.form.get('req_use_class') or None
        contact.req_area          = request.form.get('req_area') or None
        contact.req_size_min      = float(request.form.get('req_size_min')) if request.form.get('req_size_min','').strip() else None
        contact.req_size_max      = float(request.form.get('req_size_max')) if request.form.get('req_size_max','').strip() else None
        contact.req_budget_min    = float(request.form.get('req_budget_min')) if request.form.get('req_budget_min','').strip() else None
        contact.req_budget_max    = float(request.form.get('req_budget_max')) if request.form.get('req_budget_max','').strip() else None
        contact.req_budget_unit   = request.form.get('req_budget_unit') or 'pa'
        contact.req_notes         = request.form.get('req_notes') or None
        db.session.commit()
        flash('Contact updated.', 'success')
        return redirect(url_for('contact_detail', id=contact.id))
    return render_template('crm/contact_form.html', contact=contact, organisations=organisations)


@app.route('/contacts/<int:id>/delete', methods=['POST'])
def contact_delete(id):
    contact = Contact.query.get_or_404(id)
    db.session.delete(contact)
    db.session.commit()
    flash('Contact deleted.', 'info')
    return redirect(url_for('contacts_list'))


# ── Enquiries ─────────────────────────────────────────────────────────────────

@app.route('/enquiries')
def enquiries_list():
    q = request.args.get('q', '')
    status = request.args.get('status', '')
    query = Enquiry.query
    if q:
        query = query.filter(
            db.or_(Enquiry.subject.ilike(f'%{q}%'), Enquiry.enquiry_type.ilike(f'%{q}%'),
                   Enquiry.fee_earner.ilike(f'%{q}%'))
        )
    if status:
        query = query.filter(Enquiry.status == status)
    enquiries = query.order_by(Enquiry.created_at.desc()).all()
    return render_template('crm/enquiries_list.html', enquiries=enquiries, q=q, status=status)


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
    return render_template('crm/enquiry_detail.html', e=e, matched_props=matched_props, today=date.today())


@app.route('/enquiries/<int:id>/edit', methods=['GET', 'POST'])
def enquiry_edit(id):
    e = Enquiry.query.get_or_404(id)
    properties = Property.query.order_by(Property.address).all()
    contacts = Contact.query.order_by(Contact.last_name).all()
    organisations = Organisation.query.order_by(Organisation.name).all()
    projects = Project.query.order_by(Project.name).all()
    if request.method == 'POST':
        _parse_enquiry_form(request.form, e)
        db.session.commit()
        flash('Enquiry updated.', 'success')
        return redirect(url_for('enquiry_detail', id=e.id))
    return render_template('crm/enquiry_form.html', enquiry=e,
                           properties=properties, contacts=contacts,
                           organisations=organisations, projects=projects)


@app.route('/enquiries/<int:id>/delete', methods=['POST'])
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
def enquiry_note_delete(id):
    n = EnquiryNote.query.get_or_404(id)
    enq_id = n.enquiry_id
    db.session.delete(n)
    db.session.commit()
    return redirect(url_for('enquiry_detail', id=enq_id))


# ── Property-Contact Matching ─────────────────────────────────────────────────

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
                        created_by=request.form.get('created_by', '').strip() or 'Unknown')
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
def task_delete(id):
    task = ProjectTask.query.get_or_404(id)
    project_id = task.project_id
    db.session.delete(task)
    db.session.commit()
    return redirect(url_for('project_detail', id=project_id))


# ── Project Notes ────────────────────────────────────────────────────────────

@app.route('/projects/<int:id>/notes/add', methods=['POST'])
def note_add(id):
    project = Project.query.get_or_404(id)
    content = request.form.get('content', '').strip()
    author  = request.form.get('author', '').strip() or 'Unknown'
    if content:
        note = ProjectNote(project_id=id, content=content, author=author)
        db.session.add(note)
        db.session.commit()
        flash('Note added.', 'success')
    return redirect(url_for('project_detail', id=id))


@app.route('/notes/<int:id>/delete', methods=['POST'])
def note_delete(id):
    note = ProjectNote.query.get_or_404(id)
    project_id = note.project_id
    db.session.delete(note)
    db.session.commit()
    flash('Note deleted.', 'info')
    return redirect(url_for('project_detail', id=project_id))


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


# ── Website → DB: inbound enquiry webhook ────────────────────────────────────

@app.route('/api/enquiry', methods=['POST', 'OPTIONS'])
def api_enquiry():
    if request.method == 'OPTIONS':
        return '', 204

    data = request.get_json(force=True, silent=True) or request.form.to_dict()

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

    # Try to match a property from the property name passed in the form
    prop = None
    if property_ref:
        prop = Property.query.filter(
            Property.address.ilike(f'%{property_ref[:40]}%')
        ).first()

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

    enq = Enquiry(
        subject=subject,
        enquiry_type=etype_map.get(interest, 'Other'),
        status='Open',
        contact_id=contact.id if contact else None,
        property_id=prop.id if prop else None,
        project_id=proj.id if proj else None,
        notes=message or None,
        received_date=date.today(),
    )
    db.session.add(enq)
    db.session.commit()
    return jsonify({
        'ok': True,
        'enquiry_id': enq.id,
        'contact_id': contact.id if contact else None,
        'contact_type': contact_type,
    })


# ── Public API (consumed by website) ─────────────────────────────────────────

@app.route('/api/listings')
def api_listings():
    props = Property.query.filter_by(website_listed=True).all()
    listings = []
    for p in props:
        price = p.listing_price or 0
        unit  = p.listing_price_unit or 'poa'
        obj = {
            'id':            f'cr-db-{p.id}',
            'featured':      bool(p.featured),
            'category':      p.website_category or 'commercial',
            'status':        'sale' if unit == 'sale' else 'let',
            'listingStatus': p.listing_status or 'available',
            'type':          p.property_type or 'Property',
            'use':           p.use_class or 'office',
            'title':         p.address,
            'area':          p.area or p.postcode,
            'postcode':      p.postcode,
            'address':       p.address,
            'price':         price,
            'priceUnit':     unit,
            'priceDisplay':  p.price_display or None,
            'sqft':          int(p.size) if p.size else 0,
            'lat':           p.lat,
            'lng':           p.lng,
            'added':         p.created_at.strftime('%Y-%m-%d'),
            'photo':         p.photo_id or 'photo-1497366216548-37526070297c',
            'blurb':         p.blurb or p.description or '',
            'beds':          p.beds,
            'baths':         p.baths,
        }
        listings.append(obj)
    return jsonify(listings)


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


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        _migrate_project_columns()
        _migrate_listing_columns()
        _migrate_document_columns()
        _migrate_enquiry_columns()
    app.run(debug=False, host='127.0.0.1', port=8080)
