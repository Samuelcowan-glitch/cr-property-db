"""Particulars, as genuine PDFs built from live CRM data."""
import io, os, re, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)
from app import (ProjectDocument, app, db, User, Property, Project, Listing, ListingPhoto,
                 AuditLog, particulars_data, particulars_gaps)
import particulars as pp
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash
from PIL import Image
import pymupdf


def shot(seed=0):
    b = io.BytesIO()
    Image.new('RGB', (1600, 1200), (120 + seed, 140, 160)).save(b, 'JPEG')
    return b.getvalue()


LONG = ('Unit 2, Marlin House, 40 Peterborough Road, Parsons Green, '
        'London, Greater London, SW6 3BN')

with app.app_context():
    db.create_all()
    for who, role in (('admin', 'admin'), ('reader', 'viewer')):
        db.session.add(User(username=who, password_hash=generate_password_hash('pw'),
                            role=role, full_name='Benjamin Cowan',
                            email='Bc@cowanandrutter.co.uk'))
    def build(key, address, photos=6, **kw):
        p = Property(address=address, postcode='SW6 3BN', property_type='Office',
                     size=kw.pop('size', 237))
        db.session.add(p); db.session.commit()
        pr = Project(name=key, property_id=p.id, fee_earner_id=1,
                     instruction_type='To Let – Available')
        db.session.add(pr); db.session.commit()
        l = Listing(project_id=pr.id, property_id=p.id, listing_status='available',
                    listing_price=kw.pop('price', 12500), listing_price_unit='pa',
                    blurb=kw.pop('blurb', 'A bright fitted office in Fulham.'),
                    location_description=kw.pop('loc', 'Off the New Kings Road.'),
                    key_terms='A new lease for 1-4 years.', epc_band='C', **kw)
        db.session.add(l); db.session.commit()
        for i in range(photos):
            db.session.add(ListingPhoto(listing_id=l.id, file_data=shot(i),
                                        filename=f'{i}.jpg', file_mime='image/jpeg',
                                        file_size=1, sort_order=i))
        db.session.commit()
        return pr.id, l.id, [ph.id for ph in ListingPhoto.query.filter_by(
            listing_id=l.id).order_by(ListingPhoto.sort_order).all()]

    FULL, FULL_L, FULL_IDS = build('Marlin House', LONG)
    BARE, BARE_L, BARE_IDS = build('Bare', 'Nowhere Lane', photos=0,
                                   blurb=None, loc=None, price=None)
    ONE, ONE_L, ONE_IDS = build('One photo', 'Single Street', photos=1)

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def make(project_id, pages, ids, url='preview'):
    return cl.post(f'/projects/{project_id}/particulars/{url}',
                   data={'pages': str(pages), 'photo_ids': ids,
                         'no_floorplan_ok': '1'})


def opened(body):
    return pymupdf.open(stream=body, filetype='pdf')


# ─── 1. Both formats are genuine PDFs of the right size ─────────────────────
for pages in (2, 4):
    r = make(FULL, pages, FULL_IDS)
    assert r.status_code == 200, r.status_code
    body = r.get_data()
    assert body[:5] == b'%PDF-', 'not a PDF'
    doc = opened(body)
    assert doc.page_count == pages, f'{pages}-page produced {doc.page_count}'
    for page in doc:
        assert round(page.rect.width) == 842 and round(page.rect.height) == 595, \
            f'{page.rect} is not A4 landscape'
print('1. two and four page documents, all pages A4 landscape')


# ─── 2. The text is real text ───────────────────────────────────────────────
doc = opened(make(FULL, 2, FULL_IDS).get_data())
text = ' '.join(p.get_text() for p in doc)
assert 'Marlin House' in text, 'the description is not selectable text'
assert 'Peterborough Road' in text
assert 'Misrepresentation Act 1967' in text, 'the disclaimer is missing'
assert 'Benjamin Cowan' in text and 'Bc@cowanandrutter.co.uk' in text
assert '020 7349 6666' in text and 'cowanandrutter.co.uk' in text
fonts = {f[3] for page in doc for f in page.get_fonts()}
assert fonts, 'no fonts are embedded'
print('2. the words are selectable, searchable text with fonts embedded')


# ─── 3. Live CRM data, nothing invented ─────────────────────────────────────
with app.app_context():
    data = particulars_data(Project.query.get(FULL))
assert 'Marlin House' in data['address'] and 'SW6 3BN' in data['address']
assert data['rent'] == '£12,500 per annum', data['rent']
assert data['price_to_buy'] is None, data['price_to_buy']
assert 'price' not in data, 'the merged price line is still being produced'
assert '237 sq ft' in data['size_line'] and '22 sq m' in data['size_line']
assert data['fee_earner'] == 'Benjamin Cowan'
assert data['epc'] == 'C'
# Nothing the CRM does not hold.
assert data['map'] is None and data['transport'] is None
print('3. every figure comes from the CRM, and what it lacks stays empty')


# ─── 4. Nothing confidential reaches a marketing document ───────────────────
with app.app_context():
    pr = Project.query.get(FULL)
    pr.notes = 'INTERNAL: client is desperate, will take 10,000'
    db.session.commit()
text = ' '.join(p.get_text() for p in opened(make(FULL, 4, FULL_IDS).get_data()))
assert 'INTERNAL' not in text and 'desperate' not in text, \
    'an internal note reached the particulars'
for word in ('commission', 'Commission', 'fee earner', 'audit'):
    assert word not in text, f'{word} appears in a marketing document'
print('4. internal notes, commission and history stay out of the brochure')


# ─── 5. Missing information is reported, never filled in ────────────────────
with app.app_context():
    bare = particulars_data(Project.query.get(BARE))
    gaps = particulars_gaps(bare, 0)
for expected in ('Description', 'Location', 'Photographs'):
    assert expected in gaps, f'{expected} was not reported as missing'
# The rent and the sale price are now reported separately, by name.
assert any(w in gaps for w in ('Rent', 'Sale price', 'marketing instruction')), gaps
page = cl.get(f'/projects/{BARE}/particulars').get_data(as_text=True)
assert 'Some details are missing' in page
for expected in ('Description', 'Location', 'Photographs'):
    assert expected in page
print('5. what is missing is listed on the page before anything is made')


# ─── 6. It still produces a document with almost nothing ────────────────────
r = make(BARE, 2, [])
assert r.status_code == 200
doc = opened(r.get_data())
assert doc.page_count == 2, 'a sparse property produced the wrong page count'
text = ' '.join(p.get_text() for p in doc)
assert 'Nowhere Lane' in text
assert 'None' not in text and 'null' not in text, 'a missing value was printed'
print('6. a property with almost nothing recorded still produces two clean pages')


# ─── 7. Photograph order, selection and no duplicates ───────────────────────
with app.app_context():
    listing = Listing.query.get(FULL_L)
    first_bytes = sorted(listing.photos, key=lambda p: p.sort_order)[0].file_data
reversed_ids = list(reversed(FULL_IDS))
a = make(FULL, 2, FULL_IDS).get_data()
b = make(FULL, 2, reversed_ids).get_data()
assert a != b, 'reordering the photographs changed nothing'
# A photograph asked for twice is used once.
doubled = make(FULL, 2, FULL_IDS + FULL_IDS).get_data()
assert len(doubled) < len(a) * 1.6, 'a duplicate photograph was included again'
# Leaving one out changes the document.
fewer = make(FULL, 2, FULL_IDS[:2]).get_data()
assert fewer != a
print('7. photographs follow the chosen order, and none is used twice')


# ─── 8. One photograph, and none ────────────────────────────────────────────
assert opened(make(ONE, 2, ONE_IDS).get_data()).page_count == 2
assert opened(make(ONE, 4, ONE_IDS).get_data()).page_count == 4
none_doc = opened(make(BARE, 4, []).get_data())
assert none_doc.page_count == 4
text = ' '.join(p.get_text() for p in none_doc)
assert 'No photograph available' in text, 'a missing cover is not explained'
print('8. one photograph or none still produces a complete document')


# ─── 9. Long text does not overflow the page ────────────────────────────────
with app.app_context():
    l = Listing.query.get(FULL_L)
    l.blurb = 'Extremely well appointed. ' * 120
    l.location_description = 'Situated conveniently. ' * 120
    db.session.commit()
doc = opened(make(FULL, 2, FULL_IDS).get_data())
for page in doc:
    for block in page.get_text('blocks'):
        x0, y0, x1, y1 = block[:4]
        assert x0 >= -1 and y0 >= -1, f'text starts off the page: {block[:4]}'
        assert x1 <= page.rect.width + 1, 'text runs off the right edge'
        assert y1 <= page.rect.height + 1, 'text runs off the bottom'
print('9. a very long description stays inside the printable area')


# ─── 10. No blank pages ─────────────────────────────────────────────────────
for pid, ids in ((FULL, FULL_IDS), (BARE, []), (ONE, ONE_IDS)):
    for pages in (2, 4):
        doc = opened(make(pid, pages, ids).get_data())
        for i, page in enumerate(doc):
            has = page.get_text().strip() or page.get_images() or page.get_drawings()
            assert has, f'page {i + 1} of the {pages}-page document is blank'
print('10. no document contains a blank page')


# ─── 11. The filename says what it is ───────────────────────────────────────
name = pp.filename_for('Unit 2, Marlin House, SW6 3BN', 2)
assert name.startswith('Particulars - Unit 2, Marlin House, SW6 3BN - 2 Page - ')
assert name.endswith('.pdf')
assert '/' not in name and '\\' not in name, 'the filename could break a save'
r = make(FULL, 4, FULL_IDS, url='download')
assert r.status_code == 200
assert 'Particulars' in r.headers.get('Content-Disposition', '')
assert '4 Page' in r.headers['Content-Disposition']
print('11. the file is named for the property, the format and the date')


# ─── 12. Saving to the brochure, and not overwriting silently ───────────────
with app.app_context():
    Listing.query.get(FULL_L).brochure_filename = 'Existing brochure.pdf'
    Listing.query.get(FULL_L).brochure_data = b'%PDF-1.4 existing'
    db.session.commit()
r = cl.post(f'/projects/{FULL}/particulars/save',
            data={'pages': '2', 'photo_ids': FULL_IDS}, follow_redirects=True)
with app.app_context():
    assert Listing.query.get(FULL_L).brochure_filename == 'Existing brochure.pdf', \
        'the existing brochure was replaced without being asked'
assert 'Choose whether to replace' in r.get_data(as_text=True)

# Keeping both files the new one with the documents; neither is lost.
with app.app_context():
    before = ProjectDocument.query.filter_by(project_id=FULL).count()
r = cl.post(f'/projects/{FULL}/particulars/save',
            data={'pages': '2', 'photo_ids': FULL_IDS, 'existing': 'keep'},
            follow_redirects=True)
assert r.status_code == 200
with app.app_context():
    assert Listing.query.get(FULL_L).brochure_filename == 'Existing brochure.pdf'
    docs = ProjectDocument.query.filter_by(project_id=FULL).all()
    assert len(docs) == before + 1, 'the kept version was not filed anywhere'
    assert docs[-1].file_data[:5] == b'%PDF-'

# Replacing does replace, once asked.
cl.post(f'/projects/{FULL}/particulars/save',
        data={'pages': '2', 'photo_ids': FULL_IDS, 'existing': 'replace'},
        follow_redirects=True)
with app.app_context():
    saved = Listing.query.get(FULL_L)
    assert saved.brochure_filename.startswith('Particulars - '), saved.brochure_filename
    assert saved.brochure_data[:5] == b'%PDF-'
    assert saved.brochure_size == len(saved.brochure_data)
print('12. an existing brochure is never replaced without being asked')


# ─── 13. Everything is recorded ─────────────────────────────────────────────
with app.app_context():
    trail = [a.detail or '' for a in AuditLog.query.filter_by(
        entity='Project', entity_id=str(FULL)).all()]
    assert any('particulars saved as the brochure' in d for d in trail), trail[-4:]
    assert any('replacing Existing brochure.pdf' in d for d in trail)
    assert any('particulars downloaded' in d for d in trail)
    assert any('filed with the documents' in d for d in trail), trail[-4:]
    assert any('remains the brochure' in d for d in trail), \
        'the audit does not say which version is still current'
print('13. producing, downloading, keeping and replacing are all recorded')


# ─── 14. Existing brochure handling still works ─────────────────────────────
page = cl.get(f'/projects/{FULL}').get_data(as_text=True)
assert 'Create Particulars' in page, 'the button is missing from the Brochure box'
i = page.index('Create Particulars')
box = page[max(0, i - 400):i + 900]
assert 'Brochure' in box, 'the button is not in the Brochure box'
assert 'Upload' in box or 'Replace' in box, 'uploading was removed'
assert 'Download' in box and 'Remove' in box, 'the existing actions were removed'
print('14. the button sits in the Brochure box and nothing else was removed')


# ─── 15. Permission is checked on the server ────────────────────────────────
viewer = app.test_client()
viewer.post('/login', data={'username': 'reader', 'password': 'pw'}, follow_redirects=True)
for url in ('', '/preview', '/download', '/save'):
    method = viewer.get if url == '' else viewer.post
    r = method(f'/projects/{FULL}/particulars{url}',
               **({} if url == '' else {'data': {'pages': '2'}}))
    assert r.status_code == 403, f'a viewer reached particulars{url} ({r.status_code})'
print('15. a viewer cannot create, preview, download or save particulars')


# ─── 16. Text from a record cannot disturb the page ─────────────────────────
with app.app_context():
    l = Listing.query.get(FULL_L)
    # The very long location from the previous check is put back to normal, or
    # the panel fills before it reaches the description.
    l.location_description = 'Off the New Kings Road.'
    l.blurb = '<b>Ordinary</b>   spaced\n\ntext &amp; symbols'
    db.session.commit()
doc = opened(make(FULL, 2, FULL_IDS).get_data())
text = re.sub(r'\s+', ' ', ' '.join(p.get_text() for p in doc))
assert '<b>' not in text and '</b>' not in text, 'markup reached the PDF'
assert 'Ordinary spaced text' in text, 'the words themselves were lost'
print('16. markup is stripped from record text and the words survive')


# ─── 17. Both formats share one system ──────────────────────────────────────
src = open(f'{ROOT}/particulars.py').read()
assert src.count('def cover_page') == 1 and src.count('def detail_page') == 1
assert 'def build(' in src and src.count('def build(') == 1
assert 'reportlab' in open(f'{ROOT}/requirements.txt').read(), \
    'the PDF library is not in requirements'
assert os.path.exists(f'{ROOT}/static/img/cr-logo.png'), 'the logo is missing'
print('17. one generator, shared components, and the logo is in the repository')


# ─── 18. Mustica Pro is used when it is there ───────────────────────────────
assert 'MusticaPro-Regular' in src, 'the font is never looked for'
assert 'static/fonts' in src.replace(os.sep, '/') or 'FONT_DIR' in src
page = cl.get(f'/projects/{FULL}/particulars').get_data(as_text=True)
if not pp.HAVE_MUSTICA:
    assert 'Mustica Pro is not installed' in page, \
        'the fallback typeface is used without saying so'
print('18. Mustica Pro is used when present, and its absence is stated')

print('\nPARTICULARS: ALL CHECKS PASSED')
