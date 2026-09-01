"""Full workflow: open, edit, save, refresh, reopen, delete, navigate back."""
import io, os, re, sys, tempfile
from collections import Counter
from datetime import date

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
sys.path.insert(0, "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db")

from app import (app, db, Property, Project, Listing, ListingPhoto, Contact, Enquiry,
                 Transaction, ProjectTask, ProjectNote, User, INSTRUCTION_TYPES,
                 INSTRUCTION_APPRAISAL)
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash
from PIL import Image

root = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"


def png():
    b = io.BytesIO(); Image.new('RGB', (50, 40), (80, 110, 90)).save(b, 'PNG'); return b.getvalue()


with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw')))

    keep = Property(address='2A Britannia Way', postcode='SW6 1AA', size=1250,
                    property_type='Office')
    spare = Property(address='9 Spare Street', postcode='SW3 9ZZ')
    db.session.add_all([keep, spare]); db.session.flush()

    proj = Project(name='Letting — 2A Britannia Way', project_ref='CR/2026/001',
                   property_id=keep.id, status='Active',
                   instruction_type='To Let – Available', client='Marsden Estates Ltd',
                   fee_earner='B. Cowan', notes='Background worth keeping.')
    db.session.add(proj); db.session.flush()
    lst = Listing(project_id=proj.id, property_id=keep.id, listing_price=24000,
                  listing_price_unit='pa', size=1250, listing_status='available')
    db.session.add(lst); db.session.flush()
    db.session.add(ListingPhoto(listing_id=lst.id, file_data=png(), filename='a.png',
                                file_mime='image/png', sort_order=0))
    db.session.add_all([
        ProjectTask(project_id=proj.id, title='Chase references'),
        ProjectNote(project_id=proj.id, content='Called the landlord.', author='BC'),
    ])
    jane = Contact(first_name='Jane', last_name='Fairfax', email='jane@example.com')
    db.session.add(jane); db.session.flush()
    db.session.add(Enquiry(subject='Enquiry', project_id=proj.id, contact_id=jane.id,
                           status='Open', received_date=date.today()))
    db.session.add(Transaction(property_id=keep.id, transaction_type='Leasehold',
                               transaction_date=date.today()))

    doomed_proj = Project(name='Doomed instruction', property_id=spare.id, status='Active',
                          instruction_type='Prospect')
    db.session.add(doomed_proj); db.session.flush()
    db.session.add(Listing(project_id=doomed_proj.id, property_id=spare.id))
    db.session.commit()
    pid, prop_id, spare_id, doomed_id = proj.id, keep.id, spare.id, doomed_proj.id

with app.app_context():
    # A property needs its billing authority before it can be created.
    _mig = getattr(__import__('app'), '_migrate_rates_tables', None)
    if _mig:
        _mig()
    _c = __import__('app').Council.query.first()
    COUNCIL_ID = _c.id if _c else None

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def page(url, code=200):
    r = cl.get(url)
    assert r.status_code == code, f'{url} returned {r.status_code}'
    return r.get_data(as_text=True)


def form_fields(html, form_id):
    """Every control the named form will submit."""
    return re.findall(
        r'<(?:input|select|textarea)[^>]*name="([a-z_]+)"[^>]*form="' + form_id + r'"', html)


def wrapped_fields(html):
    """Controls inside the property record's wrapping form."""
    body = html.split('data-inline-edit')[1].split('</form>')[0]
    return re.findall(r'<(?:input|select|textarea)[^>]*name="([a-z_]+)"', body)


# ══ 1. Only one Save on every record page ═══════════════════════════════════
for name, url in [('property record', f'/properties/{prop_id}'),
                  ('property details', f'/properties/{prop_id}'),
                  ('new property', '/properties/new'),
                  ('project details', f'/projects/{pid}/edit'),
                  ('new project', '/projects/new')]:
    html = page(url)
    saves = len(re.findall(r'>\s*(?:Save|Save Changes|Create Property|Create Project)\s*<', html))
    assert saves == 1, f'{name} has {saves} save buttons'
    cancels = len(re.findall(r'>\s*Cancel\s*<', html))
    assert cancels <= 1, f'{name} has {cancels} cancel buttons'
print('1. one Save button on every record page, and at most one Cancel')

# ══ 2. No field is claimed twice by the same form ═══════════════════════════
checks = [
    ('project record', page(f'/projects/{pid}'), 'project-form'),
    ('project details', page(f'/projects/{pid}/edit'), 'project-form'),
    ('new project', page('/projects/new'), 'project-form'),
    ('property details', page(f'/properties/{prop_id}'), 'property-form'),
    ('new property', page('/properties/new'), 'property-form'),
]
for name, html, fid in checks:
    dupes = {k: v for k, v in Counter(form_fields(html, fid)).items()
             if v > 1 and k not in ('property_mode',)}
    assert not dupes, f'{name}: these fields would overwrite each other — {dupes}'
dupes = {k: v for k, v in Counter(wrapped_fields(page(f'/properties/{prop_id}'))).items() if v > 1}
assert not dupes, f'property record: duplicate fields — {dupes}'
print('2. no form claims the same field twice')

# ══ 3. Property: open → edit → save → refresh → reopen ══════════════════════
r = cl.post(f'/properties/{prop_id}/edit', data={
    'address': '2A Britannia Way, Parsons Green', 'postcode': 'sw6 1bb',
    'property_type': 'Retail', 'area': 'Parsons Green', 'use_class': 'Class E',
    'size': '1400', 'measurement_type': 'NIA', 'description': 'Corner unit.',
    'residential_use': '', 'beds': '', 'baths': ''}, follow_redirects=True)
assert r.status_code == 200
with app.app_context():
    p = Property.query.get(prop_id)
    assert p.address == '2A Britannia Way, Parsons Green'
    assert p.postcode == 'SW6 1BB', f'postcode not tidied: {p.postcode}'
    assert p.property_type == 'Retail' and p.area == 'Parsons Green'
    assert p.use_class == 'Class E' and p.size == 1400
    assert p.measurement_type == 'NIA' and p.description == 'Corner unit.'
html = page(f'/properties/{prop_id}')
for shown in ['2A Britannia Way, Parsons Green', 'SW6 1BB', 'Retail', 'Parsons Green',
              'Corner unit.']:
    assert shown in html, f'{shown} is not shown after saving'
print('3. a property saves everything on the details page, and shows it again')

# ══ 4. The property record page saves what it shows ════════════════════════
r = cl.post(f'/properties/{prop_id}/edit', data={
    'address': '2A Britannia Way, Parsons Green', 'postcode': 'SW6 1BB',
    'property_type': 'Office', 'area': 'Fulham', 'size': '1500',
    'measurement_type': 'GIA', 'description': 'Now an office.'},
    follow_redirects=True)
with app.app_context():
    p = Property.query.get(prop_id)
    assert p.property_type == 'Office' and p.area == 'Fulham'
    assert p.description == 'Now an office.' and p.size == 1500
    # Not on that form, so left alone rather than blanked.
    assert p.use_class == 'Class E', 'a field the page does not show was blanked'
print('4. the record page saves what it shows and leaves the rest alone')

# ══ 5. Project: open → edit → save → refresh → reopen ══════════════════════
r = cl.post(f'/projects/{pid}/edit', data={
    'instruction_type': 'For Sale – Available', 'project_ref': 'CR/2026/009',
    'status': 'On Hold', 'fee_earner': 'K. Cowan', 'client': 'Bellweather Retail',
    'instruction_date': date.today().isoformat(),
    'available_from': date.today().isoformat()}, follow_redirects=True)
assert r.status_code == 200
with app.app_context():
    p = Project.query.get(pid)
    assert p.instruction_type == 'For Sale – Available'
    assert p.project_ref == 'CR/2026/009' and p.status == 'On Hold'
    assert p.client == 'Bellweather Retail' and p.fee_earner == 'K. Cowan'
    assert p.name == 'Letting — 2A Britannia Way', 'the project was renamed'
    assert p.notes == 'Background worth keeping.', 'the notes were wiped by a save'
html = page(f'/projects/{pid}')
for shown in ['CR/2026/009', 'Bellweather Retail', 'K. Cowan', 'For Sale – Available']:
    assert shown in html, f'{shown} is not shown after saving'
print('5. a project saves and shows it again, without losing its name or notes')

# ══ 6. Neither record page shows the other records' list ═══════════════════
for name, url in [('project', f'/projects/{pid}'), ('property', f'/properties/{prop_id}')]:
    html = page(url)
    assert 'class="rec-list"' not in html, f'the {name} page still shows a list beside it'
    assert 'proj-split' not in html and 'data-project-panel' not in html
assert 'class="rec-list"' in page('/projects') and 'class="rec-list"' in page('/properties')
print('6. lists appear on the list pages only, never beside a record')

# ══ 7. Navigation: every link on a record page resolves ════════════════════
for name, url in [('project', f'/projects/{pid}'), ('property', f'/properties/{prop_id}'),
                  ('project details', f'/projects/{pid}/edit'),
                  ('property details', f'/properties/{prop_id}')]:
    html = page(url)
    links = set(re.findall(r'href="(/[^"#?]*)"', html))
    for href in sorted(links):
        code = cl.get(href).status_code
        assert code in (200, 302), f'{name}: {href} returned {code}'
print('7. every link on both record pages and both details pages opens')

# ══ 8. Breadcrumbs go back where they should ═══════════════════════════════
assert 'href="/projects"' in page(f'/projects/{pid}')
assert 'href="/properties"' in page(f'/properties/{prop_id}')
assert page(f'/properties/{prop_id}')
assert f'href="/projects/{pid}"' in page(f'/projects/{pid}/edit')
print('8. breadcrumbs and Cancel lead back to the record they came from')

# ══ 9. Deleting does not fail ══════════════════════════════════════════════
r = cl.post(f'/projects/{doomed_id}/delete', follow_redirects=False)
assert r.status_code in (302, 303), f'deleting a project returned {r.status_code}'
with app.app_context():
    assert Project.query.get(doomed_id) is None
    assert Listing.query.filter_by(project_id=doomed_id).count() == 0, 'its listing was orphaned'
r = cl.post(f'/properties/{spare_id}/delete', follow_redirects=False)
assert r.status_code in (302, 303), f'deleting a property returned {r.status_code}'
with app.app_context():
    assert Property.query.get(spare_id) is None
print('9. a project and a property both delete cleanly, with nothing orphaned')

# ══ 10. Instruction types are the five, everywhere they are offered ════════
for name, url in [('project record', f'/projects/{pid}'),
                  ('project details', f'/projects/{pid}/edit'),
                  ('new project', '/projects/new'),
                  ('projects list filter', '/projects')]:
    html = page(url)
    sel = re.search(r'<select name="(?:instruction_)?type"[^>]*>(.*?)</select>', html, re.S)
    assert sel, f'{name} has no instruction type control'
    offered = [o for o in re.findall(r'<option value="([^"]*)"', sel.group(1)) if o]
    extra = [o for o in offered if o not in INSTRUCTION_TYPES]
    # A record may also offer the legacy value it already holds.
    with app.app_context():
        held = Project.query.get(pid).instruction_type
    assert all(o == held for o in extra), f'{name} offers {extra}'
    for t in INSTRUCTION_TYPES:
        assert t in offered, f'{name} is missing {t}'
print('10. exactly the five instruction types are offered everywhere')

# ══ 11. Market Appraisals on the Organiser ════════════════════════════════
with app.app_context():
    Project.query.get(pid).instruction_type = INSTRUCTION_APPRAISAL
    Project.query.get(pid).status = 'Active'
    db.session.commit()
org = page('/')
left = org.split('org-col')[1]
assert 'Market Appraisals' in left
assert '2A Britannia Way' in left.split('Market Appraisals')[1]
order = [left.index('For Sale &ndash; Available'), left.index('To Let &ndash; Available'),
         left.index('Market Appraisals')]
assert order == sorted(order), 'the organiser panels are out of order'
print('11. Market Appraisals lists the right projects, third in the column')

# ══ 12. No reference number anywhere on a property ════════════════════════
for name, url in [('list', '/properties'), ('record', f'/properties/{prop_id}'),
                  ('details', f'/properties/{prop_id}'), ('new', '/properties/new')]:
    low = page(url).lower()
    assert 'reference' not in low, f'a reference appears on the property {name}'
    assert 'project_ref' not in low, f'a reference field appears on the property {name}'
assert 'project_ref' in page(f'/projects/{pid}'), 'the project lost its reference'
print('12. properties carry no reference number; projects keep theirs')


def instruction_details_only(html):
    """Just the Instruction Details box, so a field in the next box is not
    mistaken for one that was supposed to be cleared out of this one.

    Sliced to the next box heading rather than by a character count, which
    quietly swallowed the following box as the page grew.
    """
    if 'Instruction Detail' not in html:
        return ''
    part = html.split('Instruction Detail', 1)[1]
    nxt = re.search(r'<div class="box-head"', part)
    return part[:nxt.start()] if nxt else part


# ══ 13. Removed fields are gone, and no gap is left ═══════════════════════
rec = page(f'/projects/{pid}')
for gone in ['name="landlord_name"', 'Fees &amp; Rent', 'Rent p.a.']:
    assert gone not in rec, f'{gone} is still on the project page'
# The fee is back, but in its own box rather than Instruction Details.
assert 'name="fee_percent"' in rec, 'there is nowhere to enter a fee'
assert 'name="fee_percent"' not in instruction_details_only(rec), \
    'the fee is back inside Instruction Details'
# The website's own location paragraph belongs to the listing and stays, but
# it must submit with the listing form, not the project's.
loc = re.search(r'<textarea name="location_description"[^>]*>', rec)
assert loc, 'the listing lost its location paragraph'
assert 'form="project-form"' in loc.group(0), \
    'the listing paragraph is not saved by the one Save button'
src = open(f'{ROOT}/app.py').read() if 'ROOT' in dir() else open(
    "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db/app.py").read()
assert "LISTING_OWNED = ('location_description',)" in src, \
    'nothing stops the listing paragraph overwriting the project copy'
assert 'name="location_description"' not in ' '.join(form_fields(rec, 'project-form'))
box = rec.split('<div class="box-head">Instruction Detail</div>')[1].split('</div>\n</div>')[0]
cells = re.findall(r'<div class="(fcell[^"]*)"', box)
assert len(cells) % 2 == 0, f'{len(cells)} cells is not whole label/value pairs'
for i, c in enumerate(cells):
    assert ('fcell--label' in c) == (i % 2 == 0), f'cell {i} is out of step'
print('13. removed fields are gone and the rows that remain are complete')

# ══ 14. Searchable pickers are still wired up ════════════════════════════
new_proj = page('/projects/new')
assert 'name="property_id" data-search' in new_proj
enq = page('/enquiries')
assert 'data-search' in enq, 'the enquiry filters lost their searchable picker'
js = open(f'{root}/static/js/searchable-select.js').read()
assert 'MutationObserver' in js and 'window.CRSearchableSelect' in js
print('14. searchable pickers are present and still enhance new markup')

# ══ 15. Nothing on these pages 500s ══════════════════════════════════════
for url in ['/', '/projects', '/properties', f'/projects/{pid}', f'/properties/{prop_id}',
            f'/projects/{pid}/edit', f'/properties/{prop_id}', '/projects/new',
            '/properties/new', '/enquiries', '/diary', '/contacts', '/transactions']:
    r = cl.get(url)
    assert r.status_code == 200, f'{url} returned {r.status_code}'
print('15. every page in the workflow returns cleanly')

print('\nWORKFLOW: ALL CHECKS PASSED')
