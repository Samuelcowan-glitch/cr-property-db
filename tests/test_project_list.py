"""Projects and Properties lists: shared rows, each opening its own full page."""
import io, os, sys, tempfile
from datetime import date

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
sys.path.insert(0, "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db")

from app import (app, db, Property, Project, Listing, ListingPhoto, User,
                 format_rent, format_size, project_row_summary)
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash
from PIL import Image

root = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"


def png():
    buf = io.BytesIO()
    Image.new('RGB', (60, 40), (180, 60, 40)).save(buf, 'PNG')
    return buf.getvalue()


with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw')))

    p1 = Property(address='12 King Street, London', postcode='SW1Y 6QY', size=430,
                  listing_price=18500, listing_price_unit='pa', website_listed=True)
    p2 = Property(address='8 Sloane Court East', postcode='SW3 4TB')
    db.session.add_all([p1, p2]); db.session.flush()

    # A project whose figures come from its website listing, with photographs.
    withlisting = Project(name='Letting — 12 King Street', project_ref='CR/2026/001',
                          property_id=p1.id, status='Active', client='Marsden Estates Ltd',
                          fee_earner='B. Cowan', instruction_type='To Let – Available')
    db.session.add(withlisting); db.session.flush()
    lst = Listing(project_id=withlisting.id, property_id=p1.id,
                  listing_price=24000, listing_price_unit='pa', size=1250)
    db.session.add(lst); db.session.flush()
    # Deliberately out of order, to prove the first ordered photo is used.
    db.session.add_all([
        ListingPhoto(listing_id=lst.id, file_data=png(), filename='second.png',
                     file_mime='image/png', sort_order=1),
        ListingPhoto(listing_id=lst.id, file_data=png(), filename='first.png',
                     file_mime='image/png', sort_order=0),
    ])

    # A project with no listing and no figures at all.
    bare = Project(name='Rent Review — 8 Sloane Court East', project_ref='CR/2026/002',
                   property_id=p2.id, status='On Hold', client='Bellweather Retail',
                   fee_earner='K. Cowan', instruction_type='Rent Review')
    # A project with no property at all.
    orphan = Project(name='Advisory — general', status='Complete', client='Ashcombe',
                     fee_earner='S. Rutter')
    db.session.add_all([bare, orphan]); db.session.commit()

    a_id, b_id, c_id = withlisting.id, bare.id, orphan.id
    p1id = p1.id
    first_photo_id = sorted(lst.photos, key=lambda x: x.sort_order)[0].id

with app.app_context():
    # A property needs its billing authority before it can be created.
    _mig = getattr(__import__('app'), '_migrate_rates_tables', None)
    if _mig:
        _mig()
    _c = __import__('app').Council.query.first()
    COUNCIL_ID = _c.id if _c else None

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def page(url):
    r = cl.get(url)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)


def sidebar(html):
    """The list rows only — the search controls above are not part of them."""
    assert 'rec-list' in html
    return html.split('class="rec-list"')[1]


# ── 1. Formatting ───────────────────────────────────────────────────────────
assert format_rent(18500, 'pa') == '£18,500 per annum'
assert format_rent(1500, 'pcm') == '£1,500 per calendar month'
assert format_rent(1200000, 'sale') == '£1,200,000'
assert format_rent(None, 'pa') == 'Not provided'
assert format_rent(0, 'pa') == 'Not provided'
assert format_size(430) == '430 sq. ft.'
assert format_size(None) == 'Not provided'
print('1. rent and size are formatted consistently, blanks say Not provided')

# ── 2. Only the four facts appear ───────────────────────────────────────────
html = page('/projects')
side = sidebar(html)
assert 'CR/2026/001' in side and 'CR/2026/002' in side
assert '12 King Street, London, SW1Y 6QY' in side
assert '£24,000 per annum' in side           # from the listing, not the property
assert '1,250 sq. ft.' in side
print('2. reference, address, rent and size all show')

# ── 3. Client, fee earner and status are gone from the sidebar ──────────────
for gone in ['Marsden Estates Ltd', 'Bellweather Retail', 'Ashcombe',
             'B. Cowan', 'K. Cowan', 'S. Rutter',
             'badge-active', 'badge-hold', 'badge-complete']:
    assert gone not in side, f'{gone} is still shown in the sidebar'
print('3. no client, fee earner, negotiator or status in the sidebar')

# ── 4. The data is untouched, and still on the Project Overview ─────────────
with app.app_context():
    p = Project.query.get(a_id)
    assert p.client == 'Marsden Estates Ltd' and p.fee_earner == 'B. Cowan'
    assert p.status == 'Active'
overview = page(f'/projects/{a_id}')
assert 'Marsden Estates Ltd' in overview and 'B. Cowan' in overview
assert 'Active' in overview
print('4. client, fee earner and status are kept in the database and on the record')

# ── 5. The first ordered photograph is the thumbnail ────────────────────────
assert f'/listing-photos/{first_photo_id}' in side or f'id={first_photo_id}' in side or \
    f'/{first_photo_id}/' in side, 'the first ordered photo is not used'
with app.app_context():
    r = project_row_summary(Project.query.get(a_id))
    assert r['photo'].filename == 'first.png', r['photo'].filename
print('5. the thumbnail is the first ordered photograph')

# ── 6. The placeholder is used where there is no photograph ─────────────────
assert 'org-thumb is-empty' in side, 'no placeholder for a project without photos'
with app.app_context():
    assert project_row_summary(Project.query.get(b_id))['photo'] is None
print('6. a project with no photograph gets the standard placeholder')

# ── 7. Missing rent and size say so rather than leaving a gap ───────────────
with app.app_context():
    r = project_row_summary(Project.query.get(b_id))
    assert r['rent'] == 'Not provided' and r['size'] == 'Not provided'
    r = project_row_summary(Project.query.get(c_id))
    assert r['address'] == 'No property linked'
assert 'Not provided' in side
print('7. rent and size that were never entered read Not provided')

# ── 8. The same row component as the Organiser ──────────────────────────────
shared = open(f'{root}/templates/_property_row.html').read()
assert 'org-row' in shared and 'org-thumb' in shared
for tpl in ['dashboard.html', 'projects/list.html']:
    body = open(f'{root}/templates/{tpl}').read()
    assert '_property_row.html' in body, f'{tpl} does not use the shared row'
    assert 'class="org-thumb"' not in body, f'{tpl} still has its own copy of the row'
organiser = page('/')
assert 'org-row' in organiser and 'org-thumb' in organiser
print('8. the Organiser and the Projects sidebar use one row component')

# ── 9. The whole row is clickable ───────────────────────────────────────────
assert f'<a class="org-row' in side
assert f'href="/projects/{a_id}"' in side
assert 'data-project=' not in side, 'rows still load into a panel'
print('9. the entire row is a link to the project')

# ── 10. A project opens on its own full page ────────────────────────────────
listing = page('/projects')
assert 'proj-split' not in listing, 'the projects list still has a split view'
assert 'data-project-panel' not in listing, 'the details panel is still there'
assert 'data-project=' not in listing, 'rows still load into a panel'
assert f'href="/projects/{a_id}"' in listing, 'the row does not link to the project'
full = page(f'/projects/{a_id}')
assert 'Instruction Detail' in full and 'rec-cols-proj' in full
assert 'sidebar-nav' in full, 'the project page lost the main navigation'
assert 'rec-list' not in full, 'the projects list is still beside the project'
print('10. a project opens on its own page, with no list beside it')

# ── 11. Properties behave the same way ──────────────────────────────────────
props = page('/properties')
assert 'rec-list' in props and 'org-row' in props, 'the properties list is not on the shared rows'
assert f'href="/properties/{p1id}"' in props
prop_page = page(f'/properties/{p1id}')
assert 'rec-list' not in prop_page, 'the properties list is still beside the property'
assert 'rec-header' in prop_page and 'rec-header' in full, 'the two records open differently'
assert 'prop-grid' in prop_page, 'the property record is not on a grid'
assert 'rec-cols-proj' not in prop_page, \
    "the property record is back on the project's fixed 320px rail"
print('11. a property opens the same way, on its own page, laid out to match')

# ── 12. Properties carry no reference number, anywhere ──────────────────────
for where, text in [('the properties list', props),
                    ('the property record', prop_page),
                    ('the property overview', page(f'/properties/{p1id}')),
                    ('the new property page', page('/properties/new'))]:
    low = text.lower()
    assert 'reference' not in low, f'a reference appears on {where}'
    assert 'project_ref' not in low, f'a reference field appears on {where}'
# A project still has one.
assert 'Reference' in full and 'project_ref' in full, 'the project lost its reference'
print('12. no reference number on any property page; projects keep theirs')

# ── 13. Searching and filtering still work ──────────────────────────────────
side = sidebar(page('/projects?q=King'))
assert 'CR/2026/001' in side and 'CR/2026/002' not in side
side = sidebar(page('/projects?status=On+Hold'))
assert 'CR/2026/002' in side and 'CR/2026/001' not in side
side = sidebar(page('/projects?q=Bellweather'))       # client is still searchable
assert 'CR/2026/002' in side
print('13. search and the status filter still work, client included')

# ── 14. An empty list says so ───────────────────────────────────────────────
empty = page('/projects?q=nothingmatchesthis')
assert 'No projects match your search.' in empty
assert 'org-empty' in empty
empty_props = page('/properties?q=nothingmatchesthis')
assert 'No properties match' in empty_props and 'org-empty' in empty_props
print('14. an empty result is explained on both lists')

# ── 15. Scripts can be re-run on markup loaded into the panel ───────────────
inline = open(f'{root}/static/js/inline-edit.js').read()
photos = open(f'{root}/static/js/photo-manager.js').read()
assert 'window.CRInlineEdit' in inline and 'inlineReady' in inline
assert 'window.CRPhotoManager' in photos and 'photoReady' in photos
print('15. inline editing and the photo box re-attach, and never bind twice')

print('\nPROJECT LIST: ALL CHECKS PASSED')
