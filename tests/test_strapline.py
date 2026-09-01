"""The strapline: one line, feeding the particulars and Zoopla's summary.

It replaces a fallback that sent the marketing description as the Zoopla
summary and silently cut it at 2,000 characters. Neither happens now: the
summary is the strapline as written, or nothing at all.
"""
import io, os, re, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)
from app import (app, db, User, Property, Project, Listing, ListingPhoto,
                 AuditLog, particulars_data, clean_strapline, zoopla_summary_limit)
import zoopla_feed as zf
import pymupdf
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash
from PIL import Image

STRAP = 'GROUND FLOOR OFFICE SPACE | TO LET | FULHAM SW6'
BLURB = ('Marlin House provides high-quality fully serviced and fitted office '
         'accommodation in the heart of Fulham SW6.')


def shot():
    b = io.BytesIO()
    Image.new('RGB', (1200, 900), (140, 150, 165)).save(b, 'JPEG')
    return b.getvalue()


with app.app_context():
    db.create_all()
    for who, role in (('admin', 'admin'), ('reader', 'viewer')):
        db.session.add(User(username=who, password_hash=generate_password_hash('pw'),
                            role=role, full_name='Benjamin Cowan',
                            email='Bc@cowanandrutter.co.uk'))
    prop = Property(address='Unit 2, Marlin House', postcode='SW6 3BN',
                    property_type='Office', size=237)
    db.session.add(prop); db.session.commit()
    proj = Project(name='Marlin House', property_id=prop.id, fee_earner_id=1,
                   instruction_type='To Let – Available')
    db.session.add(proj); db.session.commit()
    lst = Listing(project_id=proj.id, property_id=prop.id, listing_status='available',
                  website_listed=True, zoopla_listed=True, strapline=STRAP,
                  blurb=BLURB, listing_price=12500, listing_price_unit='pa',
                  location_description='Off the New Kings Road.')
    db.session.add(lst); db.session.commit()
    db.session.add(ListingPhoto(listing_id=lst.id, file_data=shot(), filename='a.jpg',
                                file_mime='image/jpeg', file_size=1, sort_order=0))
    db.session.commit()
    PROP, PROJ, LST = prop.id, proj.id, lst.id
    PHOTO = ListingPhoto.query.first().id

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def listing():
    return Listing.query.get(LST)


def page(url, client=None):
    r = (client or cl).get(url)
    assert r.status_code == 200, f'{url} returned {r.status_code}'
    return r.get_data(as_text=True)


# ─── 1. The strapline goes to the Zoopla summary, exactly as written ────────
with app.app_context():
    summary = zf._summary(listing(), Property.query.get(PROP))
assert summary == STRAP, repr(summary)
assert '|' in summary, 'the vertical bars were removed'
assert summary == summary.upper() or 'TO LET' in summary, 'the capitals changed'
print('1. the strapline is sent as the Zoopla summary, bars and capitals intact')


# ─── 2. The description is never the summary ────────────────────────────────
with app.app_context():
    l, p = listing(), Property.query.get(PROP)
    assert BLURB not in zf._summary(l, p), 'the description is being sent as the summary'
    # And with no strapline it sends nothing rather than falling back.
    l.strapline = None
    db.session.commit()
    assert zf._summary(listing(), p) == '', 'an empty strapline fell back to something'
    l = listing(); l.strapline = STRAP; db.session.commit()
src = open(f'{ROOT}/zoopla_feed.py').read()
summary_fn = src.split('def _summary(')[1].split('\ndef ')[0]
body = re.sub(r'""".*?"""', '', summary_fn, flags=re.S)
for reads in ('.blurb', '.description', 'key_terms', 'summary_text'):
    assert reads not in body, f'the summary still reads {reads}'
print('2. the marketing description is never used as the summary')


# ─── 3. The description still goes, on its own ──────────────────────────────
with app.app_context():
    described = zf._description(listing(), Property.query.get(PROP))
assert BLURB in described, 'the marketing description is no longer sent'
assert STRAP not in described, 'the strapline was repeated at the top of the description'
print('3. the full description is still sent, and does not repeat the strapline')


# ─── 4. Nothing is combined ─────────────────────────────────────────────────
with app.app_context():
    l, p = listing(), Property.query.get(PROP)
    assert zf._summary(l, p) != zf._description(l, p)
    assert len(zf._summary(l, p)) < len(zf._description(l, p))
print('4. the two fields carry different things')


# ─── 5. The limit is applied, and nothing is cut quietly ────────────────────
limit = zf.SUMMARY_LIMIT
assert zoopla_summary_limit() == limit
long_one = 'A' * (limit + 40)
problems, text = zf.summary_problems(long_one)
assert problems and 'characters' in problems[0], problems
assert len(text) == len(long_one), 'the strapline was shortened rather than reported'
with app.app_context():
    l = listing(); l.strapline = long_one; db.session.commit()
    assert len(zf._summary(listing(), Property.query.get(PROP))) == len(long_one), \
        'the summary was silently truncated'
    l = listing(); l.strapline = STRAP; db.session.commit()
print(f'5. over {limit} characters is reported, never silently cut')


# ─── 6. A missing strapline is a warning, not a substitution ────────────────
problems, text = zf.summary_problems(None)
assert problems and 'No strapline' in problems[0]
assert text == ''
with app.app_context():
    l = listing(); l.strapline = None; db.session.commit()
feed = page('/admin/zoopla')
assert 'Zoopla summary missing' in feed, 'nothing warns that the summary is missing'
assert 'never used instead' in feed, 'the page does not say the description is not used'
assert BLURB not in feed.split('Zoopla summary missing')[1][:600], \
    'the description was offered as a substitute'
with app.app_context():
    l = listing(); l.strapline = STRAP; db.session.commit()
print('6. a missing strapline warns clearly and nothing is invented')


# ─── 7. The vertical bar is kept ────────────────────────────────────────────
assert '|' not in zf.SUMMARY_FORBIDDEN, 'the vertical bar is treated as forbidden'
assert zf.summary_problems(STRAP)[0] == [], zf.summary_problems(STRAP)[0]
# A character the feed really cannot take is reported, not replaced.
bad = 'OFFICE ^ TO LET'
problems, text = zf.summary_problems(bad)
assert problems and 'amend' in problems[0].lower()
assert '^' in text, 'the character was removed instead of being reported'
print('7. bars pass through; a character the feed cannot take is reported')


# ─── 8. The summary appears in the publishing preview ───────────────────────
feed = page('/admin/zoopla')
assert STRAP in feed, 'the summary is not shown before submission'
assert 'Zoopla Summary' in feed, 'the preview does not name the field'
print('8. the summary is shown in the publishing preview')


# ─── 9. A listing Zoopla would refuse is held back, not mangled ─────────────
with app.app_context():
    other = Listing(project_id=PROJ, property_id=PROP, listing_status='available',
                    website_listed=True, zoopla_listed=True, strapline=None,
                    blurb='Another unit.')
    db.session.add(other); db.session.commit()
    OTHER = other.id
r = cl.post('/admin/zoopla/push', follow_redirects=True)
body = r.get_data(as_text=True)
assert 'not sent because their' in body or 'not configured' in body, body[:300]
with app.app_context():
    trail = [a.detail or '' for a in AuditLog.query.filter_by(entity='Listing').all()]
    assert any('held back' in d for d in trail), trail[-3:]
    Listing.query.get(OTHER).zoopla_listed = False
    db.session.commit()
print('9. a listing with no summary is held back, and the push is recorded')


# ─── 10. One field, feeding the particulars too ─────────────────────────────
with app.app_context():
    data = particulars_data(Project.query.get(PROJ))
assert data['strapline'] == STRAP
assert data['headline'] == STRAP, 'the particulars do not head with the strapline'
for pages in (2, 4):
    r = cl.post(f'/projects/{PROJ}/particulars/preview',
                data={'pages': str(pages), 'photo_ids': [PHOTO]})
    assert r.status_code == 200
    doc = pymupdf.open(stream=r.get_data(), filetype='pdf')
    text = re.sub(r'\s+', ' ', ' '.join(p.get_text() for p in doc))
    assert 'GROUND FLOOR OFFICE SPACE' in text, f'{pages}-page is not headed by it'
    assert 'TO LET' in text and 'FULHAM SW6' in text
print('10. the same strapline heads both the two and four page particulars')


# ─── 11. There is only one of it ────────────────────────────────────────────
with app.app_context():
    columns = {c.name for c in Listing.__table__.columns}
    assert 'strapline' in columns
    assert 'zoopla_strapline' not in columns and 'zoopla_summary' not in columns, \
        'a portal-only copy was created'
app_src = open(f'{ROOT}/app.py').read()
assert app_src.count("db.Column(db.String(400))") >= 1
assert 'strapline' in app_src
print('11. one field, with no separate Zoopla-only copy')


# ─── 12. Changing it changes both outputs ───────────────────────────────────
NEW = 'FIRST FLOOR STUDIO | FOR SALE | PUTNEY SW15'
cl.post(f'/listings/{LST}/edit',
        data={'strapline': NEW, 'blurb': BLURB}, follow_redirects=True)
with app.app_context():
    assert listing().strapline == NEW, listing().strapline
    assert zf._summary(listing(), Property.query.get(PROP)) == NEW
    d = particulars_data(Project.query.get(PROJ))
    assert NEW.split(' | ')[0] in d['cover_line'], d['cover_line']
    assert d['instruction'] in d['cover_line'], d['cover_line']
    assert listing().strapline == NEW, 'the brochure rewrote the saved strapline'
    assert listing().blurb == BLURB, 'saving the strapline disturbed the description'
print('12. changing it changes the particulars and the portal together')


# ─── 13. Whitespace is tidied; wording is not ───────────────────────────────
assert clean_strapline('  OFFICE   |  TO LET \n SW6 ') == 'OFFICE | TO LET SW6'
assert clean_strapline(None) == ''
assert clean_strapline(STRAP) == STRAP, 'the strapline was altered'
print('13. only stray whitespace is tidied; the wording is left alone')


# ─── 14. The field and its counter are on the instruction ───────────────────
proj_page = page(f'/projects/{PROJ}')
assert 'name="strapline"' in proj_page, 'there is nowhere to write a strapline'
assert 'data-strapline' in proj_page and 'data-strapline-count' in proj_page, \
    'there is no character counter'
assert f'data-limit="{limit}"' in proj_page, 'the counter does not know the limit'
assert 'Marketing Description' in proj_page, 'the description field was removed'
i, j = proj_page.index('name="strapline"'), proj_page.index('name="blurb"')
assert i < j, 'the strapline should sit above the description'
js = open(f'{ROOT}/static/js/strapline.js').read()
assert 'too long for Zoopla' in js, 'the counter never warns'
assert 'substring' not in js and '.slice(' not in js, 'the counter shortens the text'
print('14. the field, its counter and the limit are all on the instruction')


# ─── 15. Existing Zoopla behaviour is untouched ─────────────────────────────
for name in ('ZOOPLA_TYPE_MAP', 'zoopla_category_for', 'generate_feed',
             'upload_feed', 'feed_config'):
    assert name in src, f'{name} has gone from the feed'
assert "@requires('publish')" in app_src and "@requires('export')" in app_src
viewer = app.test_client()
viewer.post('/login', data={'username': 'reader', 'password': 'pw'}, follow_redirects=True)
assert viewer.get('/admin/zoopla').status_code == 403, 'a viewer reached the feed page'
assert viewer.post('/admin/zoopla/push').status_code == 403
print('15. the feed, its mappings and its permissions are unchanged')

print('\nSTRAPLINE: ALL CHECKS PASSED')
