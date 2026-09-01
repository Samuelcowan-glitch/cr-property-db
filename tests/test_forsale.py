"""FOR SALE on a sale, TO LET on a letting, and no price on the cover."""
import io, os, re, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)
from app import (app, db, User, Property, Project, Listing, ListingPhoto,
                 particulars_data, particulars_gaps, marketing_instruction,
                 cover_wording, INSTRUCTION_FOR_SALE, INSTRUCTION_TO_LET)
import pymupdf
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash
from PIL import Image


def shot():
    b = io.BytesIO()
    Image.new('RGB', (900, 700), (150, 160, 175)).save(b, 'JPEG')
    return b.getvalue()


with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw'),
                        role='admin', full_name='Benjamin Cowan',
                        email='Bc@cowanandrutter.co.uk'))
    ids = {}

    def build(key, instruction, **kw):
        p = Property(address=f'{key.title()} House, Chelsea', postcode='SW6 3BN',
                     property_type='Office', size=1200)
        db.session.add(p); db.session.commit()
        pr = Project(name=key, property_id=p.id, fee_earner_id=1,
                     instruction_type=instruction)
        db.session.add(pr); db.session.commit()
        l = Listing(project_id=pr.id, property_id=p.id, listing_status='available',
                    website_category='commercial', blurb='A fine unit.',
                    location_description='Off the Kings Road.', **kw)
        db.session.add(l); db.session.commit()
        db.session.add(ListingPhoto(listing_id=l.id, file_data=shot(), filename='a.jpg',
                                    file_mime='image/jpeg', file_size=1, sort_order=0))
        db.session.commit()
        ids[key] = {'proj': pr.id, 'listing': l.id,
                    'photo': ListingPhoto.query.filter_by(listing_id=l.id).first().id}

    build('sale', INSTRUCTION_FOR_SALE, set_as_for_sale=True, sale_price=750000,
          strapline='GROUND FLOOR COMMERCIAL UNIT | CHELSEA')
    build('letting', INSTRUCTION_TO_LET, set_as_to_let=True, listing_price=25000,
          listing_price_unit='pa', strapline='SELF-CONTAINED OFFICE | FULHAM SW6')
    build('both', INSTRUCTION_FOR_SALE, set_as_for_sale=True, set_as_to_let=True,
          sale_price=750000, listing_price=45000, listing_price_unit='pa',
          strapline='OFFICE AND STUDIO SPACE')
    build('already', INSTRUCTION_FOR_SALE, set_as_for_sale=True, sale_price=750000,
          strapline='OFFICE SPACE | FOR SALE | CHELSEA')
    build('poa', INSTRUCTION_TO_LET, set_as_to_let=True, rent_on_application=True,
          strapline='WORKSHOP | ACTON')
    build('nothing', INSTRUCTION_FOR_SALE, set_as_for_sale=True,
          strapline='UNIT | CHELSEA')

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def data_for(key):
    with app.app_context():
        return particulars_data(Project.query.get(ids[key]['proj']))


def pdf_text(key, pages):
    r = cl.post(f"/projects/{ids[key]['proj']}/particulars/preview",
                data={'pages': str(pages), 'photo_ids': [ids[key]['photo']]})
    assert r.status_code == 200, r.status_code
    doc = pymupdf.open(stream=r.get_data(), filetype='pdf')
    return re.sub(r'\s+', ' ', ' '.join(p.get_text() for p in doc)), doc


# ─── 1. The instruction comes from what is saved ────────────────────────────
assert data_for('sale')['instruction'] == 'FOR SALE'
assert data_for('letting')['instruction'] == 'TO LET'
assert data_for('both')['instruction'] == 'FOR SALE | TO LET'
with app.app_context():
    appraisal = Project(name='appraisal', instruction_type='Market Appraisal')
    assert marketing_instruction(appraisal, None) is None, \
        'a market appraisal claimed a marketing instruction'
print('1. sale, letting and both read from the instruction type and the listing')


# ─── 2. A sale says FOR SALE, on both formats ───────────────────────────────
for pages in (2, 4):
    text, _ = pdf_text('sale', pages)
    assert 'FOR SALE' in text, f'the {pages}-page sale brochure does not say FOR SALE'
    assert 'TO LET' not in text, f'the {pages}-page sale brochure says TO LET'
print('2. a sale says FOR SALE and never TO LET, on both formats')


# ─── 3. A letting says TO LET ───────────────────────────────────────────────
for pages in (2, 4):
    text, _ = pdf_text('letting', pages)
    assert 'TO LET' in text, f'the {pages}-page letting does not say TO LET'
    assert 'FOR SALE' not in text, f'the {pages}-page letting says FOR SALE'
print('3. a letting says TO LET and never FOR SALE')


# ─── 4. Both ways says both ─────────────────────────────────────────────────
for pages in (2, 4):
    text, _ = pdf_text('both', pages)
    assert 'FOR SALE' in text and 'TO LET' in text, text[:200]
print('4. a unit offered both ways says FOR SALE and TO LET')


# ─── 5. The wording is never doubled ────────────────────────────────────────
line = data_for('already')['cover_line']
assert line == 'OFFICE SPACE | FOR SALE | CHELSEA', line
assert line.count('FOR SALE') == 1, 'the instruction was added twice'
text, _ = pdf_text('already', 2)
assert text.count('FOR SALE') >= 1
assert 'FOR SALE | FOR SALE' not in text, 'the cover repeats the instruction'
print('5. a strapline that already says it is used exactly as written')


# ─── 6. It is inserted where it is missing, and only in the brochure ────────
line = data_for('sale')['cover_line']
assert line == 'GROUND FLOOR COMMERCIAL UNIT | FOR SALE | CHELSEA', line
with app.app_context():
    saved = Listing.query.get(ids['sale']['listing']).strapline
    assert saved == 'GROUND FLOOR COMMERCIAL UNIT | CHELSEA', \
        'the saved strapline was rewritten'
print('6. the instruction is added for the brochure only; the record is unchanged')


# ─── 7. Only the missing half is added ──────────────────────────────────────
assert cover_wording('OFFICE | TO LET | SW6', 'FOR SALE | TO LET') == \
    'OFFICE | FOR SALE | TO LET | SW6'
assert cover_wording('STUDIO | FOR SALE | TO LET', 'FOR SALE | TO LET') == \
    'STUDIO | FOR SALE | TO LET'
assert cover_wording('', 'FOR SALE') == 'FOR SALE'
assert cover_wording('UNIT', None) == 'UNIT', 'wording appeared with no instruction'
print('7. only the part a strapline is missing is added')


# ─── 8. No price beside the strapline ───────────────────────────────────────
src = open(f'{ROOT}/particulars.py').read()
cover = src.split('def cover_page(')[1].split('\ndef ')[0]
assert "data.get('price')" not in cover, 'the cover still draws a price'
assert "data.get('rent')" not in cover and "price_to_buy" not in cover, \
    'the cover reads a rent or a sale price'
for key in ('sale', 'letting', 'both'):
    _, doc = pdf_text(key, 2)
    front = re.sub(r'\s+', ' ', doc[0].get_text())
    assert '£' not in front, f'a price is on the {key} cover: {front[:160]}'
    assert 'application' not in front.lower(), 'Price on application is on the cover'
print('8. no rent, price or "on application" appears on the cover')


# ─── 9. The cover reflows into the space, and long lines wrap ───────────────
with app.app_context():
    l = Listing.query.get(ids['sale']['listing'])
    l.strapline = ('EXCEPTIONALLY WELL APPOINTED GROUND AND LOWER GROUND FLOOR '
                   'COMMERCIAL SHOWROOM PREMISES | CHELSEA SW3')
    db.session.commit()
text, doc = pdf_text('sale', 2)
front = doc[0]
assert 'EXCEPTIONALLY WELL APPOINTED' in re.sub(r'\s+', ' ', front.get_text())
for block in front.get_text('blocks'):
    assert block[2] <= front.rect.width + 1, 'the cover wording runs off the page'
    assert block[1] >= -1, 'the cover wording runs off the top'
assert 'FOR SALE' in re.sub(r'\s+', ' ', front.get_text()), \
    'the instruction was lost from a long strapline'
with app.app_context():
    Listing.query.get(ids['sale']['listing']).strapline = \
        'GROUND FLOOR COMMERCIAL UNIT | CHELSEA'
    db.session.commit()
print('9. a long strapline wraps inside the cover and keeps its instruction')


# ─── 10. Rent for a letting, Price for a sale ───────────────────────────────
text, _ = pdf_text('letting', 2)
assert 'Rent' in text and '£25,000 per annum' in text, text[:300]
assert 'Price' not in text.replace('Price on application', ''), \
    'a letting shows a Price box'
text, _ = pdf_text('sale', 2)
assert 'Price' in text and '£750,000' in text, text[:300]
assert re.search(r'\bRent\b', text) is None, 'a sale shows a Rent box'
print('10. a letting shows Rent; a sale shows Price')


# ─── 11. Both ways shows both, never merged ─────────────────────────────────
d = data_for('both')
assert d['rent'] == '£45,000 per annum', d['rent']
assert d['price_to_buy'] == '£750,000', d['price_to_buy']
text, _ = pdf_text('both', 2)
assert 'Rent' in text and 'Price' in text
assert '£45,000 per annum' in text and '£750,000' in text
assert 'to buy' not in text, 'the two figures were merged into one line'
print('11. both ways shows Rent and Price separately, never combined')


# ─── 12. A rent is never shown as a price ───────────────────────────────────
d = data_for('letting')
assert d['price_to_buy'] is None, 'a letting produced a sale price'
d = data_for('sale')
assert d['rent'] is None, 'a sale produced a rent'
print('12. a rent is never shown as a price, nor a price as a rent')


# ─── 13. Upon application, only where it was chosen ─────────────────────────
d = data_for('poa')
assert d['rent'] == 'Upon application', d['rent']
d = data_for('nothing')
assert d['price_to_buy'] is None, 'a missing price became "upon application"'
print('13. "Upon application" appears only where it was deliberately set')


# ─── 14. Missing figures are warned about ───────────────────────────────────
gaps = particulars_gaps(data_for('nothing'), 1)
assert 'Sale price' in gaps, gaps
with app.app_context():
    bare = Project(name='bare', instruction_type=INSTRUCTION_TO_LET, fee_earner_id=1,
                   property_id=Property.query.first().id)
    db.session.add(bare); db.session.commit()
    l = Listing(project_id=bare.id, property_id=bare.property_id, set_as_to_let=True)
    db.session.add(l); db.session.commit()
    gaps = particulars_gaps(particulars_data(Project.query.get(bare.id)), 0)
assert 'Rent' in gaps, gaps
with app.app_context():
    both = particulars_data(Project.query.get(ids['both']['proj']))
assert 'Rent' not in particulars_gaps(both, 1) and \
    'Sale price' not in particulars_gaps(both, 1)
page = cl.get(f"/projects/{ids['nothing']['proj']}/particulars").get_data(as_text=True)
assert 'Sale price' in page, 'the review screen does not warn about the missing price'
print('14. a missing rent or sale price is warned about before generating')


# ─── 15. The review screen shows the final cover wording ────────────────────
page = cl.get(f"/projects/{ids['sale']['proj']}/particulars").get_data(as_text=True)
assert 'Cover wording' in page, 'the review screen does not show the cover'
assert 'GROUND FLOOR COMMERCIAL UNIT | FOR SALE | CHELSEA' in page, \
    'the review screen does not show the final wording'
assert 'Marketed as' in page and 'FOR SALE' in page
assert 'saved strapline is unchanged' in page, \
    'nothing says the saved strapline is left alone'
print('15. the review screen shows exactly what the cover will say')


# ─── 16. The type is never guessed from the words ───────────────────────────
with app.app_context():
    misleading = Project(name='misleading', instruction_type=INSTRUCTION_FOR_SALE,
                         fee_earner_id=1, property_id=Property.query.first().id)
    db.session.add(misleading); db.session.commit()
    l = Listing(project_id=misleading.id, property_id=misleading.property_id,
                set_as_for_sale=True, sale_price=500000,
                strapline='OFFICE TO LET NEARBY | CHELSEA')
    db.session.add(l); db.session.commit()
    d = particulars_data(Project.query.get(misleading.id))
assert d['instruction'] == 'FOR SALE', d['instruction']
assert 'FOR SALE' in d['cover_line'], d['cover_line']
print('16. the instruction comes from the record, whatever the strapline says')


# ─── 17. Downloading still works, and nothing was lost ──────────────────────
r = cl.post(f"/projects/{ids['sale']['proj']}/particulars/download",
            data={'pages': '4', 'photo_ids': [ids['sale']['photo']],
                  'no_floorplan_ok': '1'})
assert r.status_code == 200 and r.get_data()[:5] == b'%PDF-'
with app.app_context():
    l = Listing.query.get(ids['sale']['listing'])
    assert l.sale_price == 750000 and l.strapline
    assert Project.query.get(ids['sale']['proj']).instruction_type == INSTRUCTION_FOR_SALE
print('17. downloading still works and the project record is untouched')

print('\nFOR SALE PARTICULARS: ALL CHECKS PASSED')
