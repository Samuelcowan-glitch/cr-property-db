f = r'C:\Users\SamuelC\property-db\app.py'
with open(f, 'r', encoding='utf-8') as fh:
    txt = fh.read()

# 1. Add ListingPhoto model after Listing model
if 'class ListingPhoto' not in txt:
    insert_after = 'class ProjectDocument(db.Model):'
    photo_model = '''class ListingPhoto(db.Model):
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


'''
    txt = txt.replace(insert_after, photo_model + insert_after, 1)
    print('ListingPhoto model added.')
else:
    print('ListingPhoto already exists.')

# 2. Expand the Listing model with all new fields
old_listing_end = '''    project  = db.relationship('Project',  backref=db.backref('project_listings', lazy=True, cascade='all, delete-orphan'))
    prop     = db.relationship('Property', backref=db.backref('unit_listings', lazy=True))'''

new_listing_end = '''    # ── Commercial: Define the Space ──
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
    inside_1954_act    = db.Column(db.String(20))     # Inside / Outside / Contracted Out
    repair_insuring    = db.Column(db.String(30))     # FRI / IRI / Internal

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
    epc_band           = db.Column(db.String(5))      # A-G
    epc_band_potential = db.Column(db.String(5))
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

    # ── Brochure & Floor Plan ──
    brochure_data      = db.Column(db.LargeBinary)
    brochure_filename  = db.Column(db.String(255))
    brochure_size      = db.Column(db.Integer)
    floor_plan_data    = db.Column(db.LargeBinary)
    floor_plan_filename= db.Column(db.String(255))
    floor_plan_size    = db.Column(db.Integer)

    project  = db.relationship('Project',  backref=db.backref('project_listings', lazy=True, cascade='all, delete-orphan'))
    prop     = db.relationship('Property', backref=db.backref('unit_listings', lazy=True))'''

if old_listing_end in txt:
    txt = txt.replace(old_listing_end, new_listing_end, 1)
    print('Listing model expanded with all new fields.')
else:
    print('Listing relationship line not found - checking...')
    idx = txt.find("backref=db.backref('project_listings'")
    print(repr(txt[idx:idx+120]))

with open(f, 'w', encoding='utf-8') as fh:
    fh.write(txt)
print('Done.')
