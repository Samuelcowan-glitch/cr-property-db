f = r'C:\Users\SamuelC\property-db\app.py'
with open(f, 'r', encoding='utf-8') as fh:
    txt = fh.read()

# 1. Update Listing model: add project_id, keep property_id for reference
old_model = '''class Listing(db.Model):
    """Website-facing unit listing within a Property (building)."""
    __tablename__ = 'listings'
    id               = db.Column(db.Integer, primary_key=True)
    property_id      = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False)
    unit_name        = db.Column(db.String(100))   # e.g. "Unit 202-203" — blank = whole building
    website_listed   = db.Column(db.Boolean, default=True)
    listing_status   = db.Column(db.String(20), default='available')
    featured         = db.Column(db.Boolean, default=False)
    website_category = db.Column(db.String(20))
    use_class        = db.Column(db.String(30))
    area             = db.Column(db.String(100))
    listing_price    = db.Column(db.Float)
    listing_price_unit = db.Column(db.String(10))
    price_display    = db.Column(db.String(100))
    size             = db.Column(db.Float)
    measurement_type = db.Column(db.String(10))
    beds             = db.Column(db.Integer)
    baths            = db.Column(db.Integer)
    lat              = db.Column(db.Float)
    lng              = db.Column(db.Float)
    photo_id         = db.Column(db.String(100))
    blurb            = db.Column(db.Text)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    prop = db.relationship('Property', backref=db.backref('unit_listings', lazy=True,
                           cascade='all, delete-orphan'))

    @property
    def display_title(self):
        if self.unit_name:
            return f"{self.unit_name}, {self.prop.address}"
        return self.prop.address'''

new_model = '''class Listing(db.Model):
    """Website listing for a unit/floor/whole building — managed via a Project instruction."""
    __tablename__ = 'listings'
    id                 = db.Column(db.Integer, primary_key=True)
    project_id         = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    property_id        = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True)
    unit_name          = db.Column(db.String(100))    # "Unit 3", "Ground Floor", blank=whole building
    website_listed     = db.Column(db.Boolean, default=True)
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
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)

    project  = db.relationship('Project',  backref=db.backref('project_listings', lazy=True, cascade='all, delete-orphan'))
    prop     = db.relationship('Property', backref=db.backref('unit_listings', lazy=True))

    @property
    def display_title(self):
        addr = ''
        if self.project and self.project.property:
            addr = self.project.property.address
        elif self.prop:
            addr = self.prop.address
        return (self.unit_name + ', ' + addr) if self.unit_name and addr else (addr or self.unit_name or 'Listing')

    @property
    def display_price(self):
        if self.price_display: return self.price_display
        if not self.listing_price or self.listing_price_unit == 'poa': return 'Price on application'
        n = chr(163) + '{:,.0f}'.format(self.listing_price)
        if self.listing_price_unit == 'pa':  return n + ' per annum'
        if self.listing_price_unit == 'pcm': return n + ' pcm'
        return n'''

if old_model in txt:
    txt = txt.replace(old_model, new_model, 1)
    print('Model updated.')
else:
    print('Old model not found verbatim — checking...')
    idx = txt.find('class Listing(db.Model)')
    print(repr(txt[idx:idx+100]))

# 2. Add listing routes before the api_listings route
listing_routes = '''
# ── Listing CRUD (via Project) ────────────────────────────────────────────────

@app.route('/projects/<int:proj_id>/listings/new', methods=['GET', 'POST'])
def listing_new(proj_id):
    project = Project.query.get_or_404(proj_id)
    if request.method == 'POST':
        def pf(v): return float(v.replace(',','')) if v and v.strip() else None
        l = Listing(
            project_id=proj_id,
            property_id=project.property_id,
            unit_name=request.form.get('unit_name') or None,
            website_listed=bool(request.form.get('website_listed')),
            listing_status=request.form.get('listing_status','available'),
            featured=bool(request.form.get('featured')),
            website_category=request.form.get('website_category') or None,
            use_class=request.form.get('use_class') or None,
            residential_use=request.form.get('residential_use') or None,
            area=request.form.get('area') or None,
            listing_price=pf(request.form.get('listing_price','')),
            listing_price_unit=request.form.get('listing_price_unit','pa'),
            price_display=request.form.get('price_display') or None,
            size=pf(request.form.get('size','')),
            measurement_type=request.form.get('measurement_type') or None,
            beds=int(request.form.get('beds')) if request.form.get('beds','').strip() else None,
            baths=int(request.form.get('baths')) if request.form.get('baths','').strip() else None,
            photo_id=request.form.get('photo_id') or None,
            blurb=request.form.get('blurb') or None,
        )
        db.session.add(l)
        db.session.commit()
        flash('Listing added.', 'success')
        return redirect(url_for('project_detail', id=proj_id) + '#website')
    return render_template('projects/listing_form.html', project=project, listing=None)


@app.route('/listings/<int:id>/edit', methods=['GET', 'POST'])
def listing_edit(id):
    l = Listing.query.get_or_404(id)
    project = l.project
    if request.method == 'POST':
        def pf(v): return float(v.replace(',','')) if v and v.strip() else None
        l.unit_name          = request.form.get('unit_name') or None
        l.website_listed     = bool(request.form.get('website_listed'))
        l.listing_status     = request.form.get('listing_status','available')
        l.featured           = bool(request.form.get('featured'))
        l.website_category   = request.form.get('website_category') or None
        l.use_class          = request.form.get('use_class') or None
        l.residential_use    = request.form.get('residential_use') or None
        l.area               = request.form.get('area') or None
        l.listing_price      = pf(request.form.get('listing_price',''))
        l.listing_price_unit = request.form.get('listing_price_unit','pa')
        l.price_display      = request.form.get('price_display') or None
        l.size               = pf(request.form.get('size',''))
        l.measurement_type   = request.form.get('measurement_type') or None
        l.beds               = int(request.form.get('beds')) if request.form.get('beds','').strip() else None
        l.baths              = int(request.form.get('baths')) if request.form.get('baths','').strip() else None
        l.photo_id           = request.form.get('photo_id') or None
        l.blurb              = request.form.get('blurb') or None
        db.session.commit()
        flash('Listing updated.', 'success')
        return redirect(url_for('project_detail', id=l.project_id) + '#website')
    return render_template('projects/listing_form.html', project=project, listing=l)


@app.route('/listings/<int:id>/toggle', methods=['POST'])
def listing_toggle(id):
    l = Listing.query.get_or_404(id)
    l.website_listed = not l.website_listed
    db.session.commit()
    return redirect(url_for('project_detail', id=l.project_id) + '#website')


@app.route('/listings/<int:id>/delete', methods=['POST'])
def listing_delete(id):
    l = Listing.query.get_or_404(id)
    proj_id = l.project_id
    db.session.delete(l)
    db.session.commit()
    flash('Listing removed.', 'info')
    return redirect(url_for('project_detail', id=proj_id) + '#website')

'''

if 'def listing_new' not in txt:
    marker = '# ── Website → DB: inbound enquiry webhook'
    txt = txt.replace(marker, listing_routes + marker, 1)
    print('Listing routes added.')
else:
    print('Listing routes already exist.')

with open(f, 'w', encoding='utf-8') as fh:
    fh.write(txt)
print('Done.')
