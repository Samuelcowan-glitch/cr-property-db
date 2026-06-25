f = r'C:\Users\SamuelC\property-db\app.py'
with open(f, 'r', encoding='utf-8') as fh:
    txt = fh.read()

if 'class Listing(db.Model)' in txt:
    print('Listing model already exists')
else:
    insert_before = 'class ProjectService(db.Model):'
    listing_model = r'''class Listing(db.Model):
    """Website listing for a unit/floor/whole building, attached to a project."""
    __tablename__ = 'listings'
    id                = db.Column(db.Integer, primary_key=True)
    project_id        = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    unit_name         = db.Column(db.String(100))
    website_listed    = db.Column(db.Boolean, default=False)
    featured          = db.Column(db.Boolean, default=False)
    website_category  = db.Column(db.String(20))
    listing_status    = db.Column(db.String(20), default='available')
    use_class         = db.Column(db.String(30))
    residential_use   = db.Column(db.String(30))
    size              = db.Column(db.Float)
    size_unit         = db.Column(db.String(20), default='sq ft')
    size_basis        = db.Column(db.String(20))
    listing_price     = db.Column(db.Float)
    listing_price_unit= db.Column(db.String(10), default='pa')
    price_display     = db.Column(db.String(100))
    area              = db.Column(db.String(100))
    beds              = db.Column(db.Integer)
    baths             = db.Column(db.Integer)
    blurb             = db.Column(db.Text)
    photo_id          = db.Column(db.String(100))
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship('Project', backref=db.backref('project_listings', lazy=True, cascade='all, delete-orphan'))

    @property
    def display_price(self):
        if self.price_display:
            return self.price_display
        if not self.listing_price or self.listing_price_unit == 'poa':
            return 'Price on application'
        n = '£' + '{:,.0f}'.format(self.listing_price)
        if self.listing_price_unit == 'pa':  return n + ' per annum'
        if self.listing_price_unit == 'pcm': return n + ' pcm'
        return n

    @property
    def title(self):
        if self.project and self.project.property:
            addr = self.project.property.address
            return (self.unit_name + ' — ' + addr) if self.unit_name else addr
        return self.unit_name or 'Listing'


'''
    txt = txt.replace(insert_before, listing_model + insert_before, 1)
    with open(f, 'w', encoding='utf-8') as fh:
        fh.write(txt)
    print('Listing model added.')
