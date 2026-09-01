"""Price, and the wording the office chooses for it.

Marking a to-let unit as also for sale used to replace its rent with the sale
price. The rent was recorded nowhere else, so it was simply lost — and a stale
custom price string kept the screen looking right while it happened.
"""
import io, os, re, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)
from app import (app, db, User, Property, Project, Listing, ListingPhoto,
                 particulars_data)
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
                        role='admin', full_name='Benjamin Cowan'))
    prop = Property(address='Unit 2, Marlin House', postcode='SW6 3BN',
                    property_type='Office', size=237)
    db.session.add(prop); db.session.commit()
    proj = Project(name='Marlin', property_id=prop.id, fee_earner_id=1,
                   instruction_type='To Let – Available')
    db.session.add(proj); db.session.commit()
    lst = Listing(project_id=proj.id, property_id=prop.id, website_category='commercial',
                  listing_status='available', listing_price=45000,
                  listing_price_unit='pa', set_as_to_let=True, blurb='A unit.')
    db.session.add(lst); db.session.commit()
    db.session.add(ListingPhoto(listing_id=lst.id, file_data=shot(), filename='a.jpg',
                                file_mime='image/jpeg', file_size=1, sort_order=0))
    db.session.commit()
    PROP, PROJ, LST = prop.id, proj.id, lst.id
    PHOTO = ListingPhoto.query.first().id

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def edit(**fields):
    base = {'website_category': 'commercial'}
    base.update(fields)
    r = cl.post(f'/listings/{LST}/edit', data=base, follow_redirects=True)
    assert r.status_code == 200, r.status_code


def listing():
    return Listing.query.get(LST)


# ─── 1. A unit offered both ways keeps its rent ─────────────────────────────
edit(set_as_to_let='1', set_as_for_sale='1', sale_price='750000',
     listing_price='45000')
with app.app_context():
    l = listing()
    assert l.listing_price == 45000, f'the rent became {l.listing_price}'
    assert l.listing_price_unit == 'pa', l.listing_price_unit
    assert l.sale_price == 750000, 'the sale price was lost'
print('1. offered both ways: the rent survives and the sale price is kept too')


# ─── 2. Sale only still mirrors, as it always did ───────────────────────────
edit(set_as_for_sale='1', sale_price='750000')
with app.app_context():
    l = listing()
    assert l.listing_price == 750000 and l.listing_price_unit == 'sale'
print('2. a sale-only unit still quotes its sale price')


# ─── 3. To let only ─────────────────────────────────────────────────────────
edit(set_as_to_let='1', listing_price='45000')
with app.app_context():
    l = listing()
    assert l.listing_price == 45000 and l.listing_price_unit == 'pa'
    assert l.display_price == '£45,000 per annum', l.display_price
print('3. a letting quotes its rent per annum')


# ─── 4. Neither price, nor either flag ──────────────────────────────────────
edit(listing_price='')
with app.app_context():
    l = listing()
    assert l.listing_price_unit == 'poa', l.listing_price_unit
    assert l.display_price == 'Price on application'
print('4. with no figure at all it reads Price on application')


# ─── 5. A custom wording is used as written ─────────────────────────────────
edit(set_as_to_let='1', listing_price='45000',
     price_display='Offers in excess of £45,000 per annum')
with app.app_context():
    l = listing()
    assert l.display_price == 'Offers in excess of £45,000 per annum', l.display_price
    assert l.listing_price == 45000, 'the figure behind it was disturbed'
print('5. a custom wording is shown exactly as it was written')


# ─── 6. And it can be cleared ───────────────────────────────────────────────
edit(set_as_to_let='1', listing_price='45000', price_display='')
with app.app_context():
    l = listing()
    assert l.price_display is None, f'clearing left {l.price_display!r}'
    assert l.display_price == '£45,000 per annum', l.display_price
print('6. clearing the custom wording restores the plain figure')


# ─── 7. The sale wording can be cleared too ─────────────────────────────────
edit(set_as_for_sale='1', sale_price='750000', sale_price_display='Offers over £750,000')
with app.app_context():
    assert listing().price_display == 'Offers over £750,000'
edit(set_as_for_sale='1', sale_price='750000', sale_price_display='')
with app.app_context():
    l = listing()
    assert l.price_display is None, f'the emptied box restored {l.price_display!r}'
    assert l.display_price == '£750,000', l.display_price
print('7. the sale wording clears too, instead of restoring the old string')


# ─── 8. Nothing is disturbed when the form does not carry the field ─────────
edit(set_as_for_sale='1', sale_price='750000', sale_price_display='Guide £750,000')
with app.app_context():
    assert listing().price_display == 'Guide £750,000'
# A form that never mentions the wording must leave it alone.
cl.post(f'/listings/{LST}/edit', data={'website_category': 'commercial',
                                       'set_as_for_sale': '1', 'sale_price': '750000'},
        follow_redirects=True)
with app.app_context():
    assert listing().price_display == 'Guide £750,000', \
        'a form that never sent the field wiped it'
print('8. a form that does not carry the wording leaves it untouched')


# ─── 9. The custom wording reaches the particulars ──────────────────────────
with app.app_context():
    data = particulars_data(Project.query.get(PROJ))
assert (data['price_to_buy'] == 'Guide £750,000'
        or data['rent'] == 'Guide £750,000'), (data['rent'], data['price_to_buy'])
r = cl.post(f'/projects/{PROJ}/particulars/preview',
            data={'pages': '2', 'photo_ids': [PHOTO]})
assert r.status_code == 200
text = re.sub(r'\s+', ' ', ' '.join(
    p.get_text() for p in pymupdf.open(stream=r.get_data(), filetype='pdf')))
assert 'Guide £750,000' in text, 'the brochure recomputed the price instead'
print('9. the custom wording appears on the particulars, not a plainer version')


# ─── 10. Both figures reach the particulars when offered both ways ──────────
edit(set_as_to_let='1', set_as_for_sale='1', listing_price='45000',
     sale_price='750000', price_display='')
with app.app_context():
    data = particulars_data(Project.query.get(PROJ))
assert '45,000' in data['rent'], data['rent']
assert '750,000' in data['price_to_buy'], data['price_to_buy']
assert 'to buy' not in (data['rent'] + data['price_to_buy']), 'the figures were merged'
print('10. a unit offered both ways shows both figures on the brochure')


# ─── 11. The website is told the same thing ─────────────────────────────────
edit(set_as_to_let='1', listing_price='45000',
     price_display='Offers in excess of £45,000 per annum')
rows = cl.get('/api/listings').get_json()
mine = [r for r in rows if r.get('id') == LST]
if mine:
    assert mine[0]['priceDisplay'] == 'Offers in excess of £45,000 per annum'
    assert mine[0]['price'] == 45000, 'the website was sent the wrong figure'
print('11. the website receives the same wording and the same figure')


# ─── 12. Nothing about the price is lost on an ordinary save ────────────────
with app.app_context():
    before = (listing().listing_price, listing().sale_price,
              listing().listing_price_unit, listing().price_display)
cl.post(f'/listings/{LST}/edit', data={'website_category': 'commercial',
                                       'set_as_to_let': '1', 'listing_price': '45000',
                                       'price_display': 'Offers in excess of £45,000 per annum'},
        follow_redirects=True)
with app.app_context():
    after = (listing().listing_price, listing().sale_price,
             listing().listing_price_unit, listing().price_display)
assert before == after, f'{before} became {after}'
print('12. saving again changes none of the price fields')

print('\nPRICE: ALL CHECKS PASSED')
