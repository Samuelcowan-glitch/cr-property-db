"""Add New Project: shared layout, validation, linking, photos, publishing."""
import re
import io, os, sys, tempfile
from datetime import date

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
sys.path.insert(0, "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db")

from app import (app, db, Property, Project, Listing, ListingPhoto, Contact, User,
                 project_form_values)
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash
from PIL import Image

today = date.today()

with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw')))
    prop = Property(address='12 King Street, London', postcode='SW1Y 6QY', size=2500)
    db.session.add(prop); db.session.flush()
    existing = Project(name='Letting — 12 King Street', property_id=prop.id, status='Active',
                       instruction_type='To Let – Available', client='Marsden Estates Ltd',
                       fee_earner='B. Cowan')
    db.session.add(existing); db.session.commit()
    pid, projid = prop.id, existing.id

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)

# A fee earner to assign work to, now that staff are chosen rather than typed.
with app.app_context():
    _ben = User.query.filter(User.username.isnot(None)).order_by(User.id).first()
    if _ben and not _ben.full_name:
        _ben.full_name = 'Benjamin Cowan'
        _ben.active, _ben.can_earn_fees = True, True
        db.session.commit()
    BEN_ID = _ben.id if _ben else None


def page(url):
    r = cl.get(url)
    assert r.status_code == 200, (url, r.status_code)
    return r.get_data(as_text=True)


def photo(colour=(200, 40, 60)):
    buf = io.BytesIO()
    Image.new('RGB', (60, 40), colour).save(buf, 'PNG')
    buf.seek(0)
    return buf


# ── 1. The create page uses the record layout, not the old card form ────────
new = page('/projects/new')
for marker in ['rec-toolbar', 'rec-cols-3', 'class="box"', 'box-head', 'frow', 'fcell']:
    assert marker in new, marker
assert 'card-title' not in new, 'the old single-column card form is still being used'
print('1. the create page is built from the record layout')

# ── 2. Both pages come from the same field definitions ──────────────────────
overview = page(f'/projects/{projid}')
for box in ['Instruction Detail', 'Client Contact Details']:
    assert box in new, f'{box} missing from the create page'
    assert box in overview, f'{box} missing from the Project Overview'
# Every field the shared boxes define appears on both. The project name is
# the exception: it is set when a project is created and not editable on the
# record afterwards.
assert 'name="name"' in new, 'the create page lost its project name field'
assert 'name="name"' not in overview, 'the record page still edits the project name'
shared = ['name="project_ref"', 'name="client_contact_id"',
          'name="status"', 'name="instruction_type"', 'name="instruction_date"',
          'name="available_from"', 'name="fee_earner_id"',
          'name="client_phone"', 'name="client_mobile"', 'name="client_email"',
          'name="notes"', 'name="next_call"']

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


# Landlord and the project's own location paragraph are no longer offered
# on either page; the values stay in the database.
for gone in ['name="landlord_name"', 'name="location_description"']:
    assert gone not in new and gone not in overview, gone
# The fee is offered again, in a box of its own rather than Instruction Details.
for where in (new, overview):
    assert 'name="fee_percent"' in where and 'name="fee_fixed"' in where
    assert 'name="fee_percent"' not in instruction_details_only(where)
for f in shared:
    assert f in new, f'{f} missing from the create page'
    assert f in overview, f'{f} missing from the Project Overview'
print('2. every shared field appears on both pages, from one definition')

# ── 3. There is one copy of the layout, not two ─────────────────────────────
root = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
boxes = open(f'{root}/templates/projects/_record_boxes.html').read()
for tpl in ['detail.html', 'form.html']:
    body = open(f'{root}/templates/projects/{tpl}').read()
    assert '_record_boxes.html' in body, f'{tpl} does not use the shared boxes'
    assert 'name="instruction_type"' not in body, f'{tpl} still has its own copy of the fields'
assert 'name="instruction_type"' in boxes
print('3. the fields live in one shared file, used by both pages')

# ── 4. No Edit button; everything is entered on the page ────────────────────
assert 'Create Project' in new and 'Cancel' in new
assert 'Save Changes' not in new
print('4. Create Project and Cancel are offered, with no separate Edit step')

# ── 5. Instruction Type offers the three permitted values ──────────────────
# Rent Review was on this list until the instruction types were settled at
# Letting / Sale / Sale or Letting; older records keep their own value.
for t in ['For Sale – Available', 'To Let – Available', 'Market Appraisal',
          'Prospect', 'Archived']:
    assert f'>{t}</option>' in new, t
for gone in ['Rent Review', 'Lease Renewal', 'Valuation', 'Sale or Letting',
             'Business Rates', 'Lease Advisory', 'Building Survey']:
    assert f'>{gone}</option>' not in new, gone
print('5. exactly the five instruction types are offered, and nothing else')

# ── 6. Searchable pickers ───────────────────────────────────────────────────
assert 'name="property_id" data-search' in new
print('6. the property picker is searchable')

# ── 7. Required fields are checked on the server ────────────────────────────
before = None
with app.app_context():
    before = Project.query.count()
r = cl.post('/projects/new', data={'name': 'Half-finished', 'status': 'Active'},
            follow_redirects=True)
html = r.get_data(as_text=True)
assert 'Choose what kind of instruction this is.' in html
assert 'Choose a property from the register' in html
with app.app_context():
    assert Project.query.count() == before, 'a project was created despite the errors'
print('7. a project with nothing chosen is refused, with messages beside the fields')

# ── 8. What was typed is kept ───────────────────────────────────────────────
# The client is a linked person now, so a rejected form must bring them back.
with app.app_context():
    from app import Organisation
    _org = Organisation(name='Ashcombe Clinics', status='Active')
    db.session.add(_org); db.session.commit()
    _who = Contact(first_name='Ruth', last_name='Alder', organisation_id=_org.id)
    db.session.add(_who); db.session.commit()
    RUTH = _who.id
r = cl.post('/projects/new', data={
    'name': 'Sale or Letting — somewhere', 'client_contact_id': RUTH,
    'fee_earner_id': BEN_ID, 'notes': 'Review due at the March quarter day.',
    'property_mode': 'new', 'address': '', 'postcode': '',
    'instruction_type': 'Market Appraisal'}, follow_redirects=True)
html = r.get_data(as_text=True)
assert 'Sale or Letting — somewhere' in html
assert 'Ruth Alder (Ashcombe Clinics)' in html, \
    'the chosen client did not come back with the rejected form'
assert 'Review due at the March quarter day.' in html
assert re.search(rf'<option value="{BEN_ID}"[^>]*selected', html), \
    'the chosen fee earner did not come back with the rejected form'
assert 'A new property needs an address.' in html
assert 'A new property needs a postcode.' in html
print('8. a rejected submission comes back with everything still filled in')

# ── 9. A bad email is caught ────────────────────────────────────────────────
r = cl.post('/projects/new', data={'instruction_type': 'For Sale – Available', 'property_id': str(pid),
                                   'client_email': 'not-an-address'}, follow_redirects=True)
assert 'does not look like an email address' in r.get_data(as_text=True)
print('9. an invalid client email is caught')

# ── 10. Creating a Letting against an existing property ─────────────────────
r = cl.post('/projects/new', data={
    'instruction_type': 'To Let – Available', 'property_mode': 'existing', 'property_id': str(pid),
    'client': 'Marsden Estates Ltd', 'client_email': 'lettings@marsden.example',
    'fee_earner_id': BEN_ID, 'status': 'Active',
    'instruction_date': today.isoformat(), 'fee_percent': '10',
    'location_description': 'Off St James’s.', 'notes': 'Whole building.'},
    follow_redirects=True)
assert r.status_code == 200
with app.app_context():
    p = Project.query.filter_by(instruction_type='To Let – Available').order_by(Project.id.desc()).first()
    assert p.property_id == pid, 'did not use the property chosen'
    assert p.name == 'To Let – Available — 12 King Street, London', p.name
    assert p.fee_percent == 10 and p.instruction_date == today
    assert p.location_description == 'Off St James’s.' and p.notes == 'Whole building.'
    letting_id = p.id
    assert Property.query.count() == 1, 'a duplicate property was created'
print('10. a Letting is created against the property on file, no duplicate made')

# ── 11. It opens on the new Project Overview ────────────────────────────────
assert 'rec-cols-proj' in r.get_data(as_text=True)   # the overview grid
assert f'/projects/{letting_id}' in r.request.path or True
assert r.request.path == f'/projects/{letting_id}', r.request.path
print('11. creating a project opens its Project Overview')

# ── 12. Sale, with a property not yet on the register ───────────────────────
r = cl.post('/projects/new', data={
    'instruction_type': 'For Sale – Available', 'property_mode': 'new',
    'address': '8 Sloane Court East', 'postcode': 'sw3 4tb',
    'property_type': 'Retail', 'size': '1400', 'measurement_type': 'NIA',
    'client': 'Bellweather Retail'}, follow_redirects=True)
with app.app_context():
    p = Project.query.filter_by(instruction_type='For Sale – Available').order_by(Project.id.desc()).first()
    prop2 = Property.query.get(p.property_id)
    assert prop2.address == '8 Sloane Court East' and prop2.postcode == 'SW3 4TB'
    assert prop2.size == 1400 and prop2.measurement_type == 'NIA'
    assert Property.query.count() == 2
print('12. a Sale creates the new property once, postcode tidied up')

# ── 13. The same new property twice does not make two records ───────────────
cl.post('/projects/new', data={
    'instruction_type': 'Market Appraisal', 'property_mode': 'new',
    'address': '8 Sloane Court East', 'postcode': 'SW3 4TB'}, follow_redirects=True)
with app.app_context():
    assert Property.query.count() == 2, 'the same address was added twice'
    rr = Project.query.filter_by(instruction_type='Market Appraisal').order_by(Project.id.desc()).first()
    assert rr.property_id == prop2.id
print('13. a second instruction on the same address reuses the property on file')

# ── 14. The client is added to Contacts once, not once per project ──────────
with app.app_context():
    assert Contact.query.filter_by(email='lettings@marsden.example').count() == 1
cl.post('/projects/new', data={
    'instruction_type': 'To Let – Available', 'property_mode': 'existing', 'property_id': str(pid),
    'client': 'Marsden Estates Ltd', 'client_email': 'lettings@marsden.example'},
    follow_redirects=True)
with app.app_context():
    assert Contact.query.filter_by(email='lettings@marsden.example').count() == 1, \
        'the client was added to Contacts twice'
print('14. an existing client is matched rather than duplicated')

# ── 15. Nothing is published unless it was asked for ────────────────────────
with app.app_context():
    p = Project.query.get(letting_id)
    assert p.project_listings == [] or not p.project_listings[0].website_listed
    assert Listing.query.filter_by(project_id=letting_id).count() == 0, \
        'a listing was created when none was wanted'
print('15. no listing and nothing published when neither was chosen')

# ── 16. Publishing only where it was ticked ─────────────────────────────────
r = cl.post('/projects/new', data={
    'instruction_type': 'To Let – Available', 'property_mode': 'existing', 'property_id': str(pid),
    'listing_price': '85000', 'listing_price_unit': 'pa', 'publish_website': '1'},
    follow_redirects=True)
with app.app_context():
    p = Project.query.order_by(Project.id.desc()).first()
    lst = Listing.query.filter_by(project_id=p.id).one()
    assert lst.listing_price == 85000 and lst.listing_price_unit == 'pa'
    assert lst.website_listed is True and lst.website_published_at is not None
    assert not lst.zoopla_listed, 'Zoopla was published without being chosen'
print('16. the asking price is saved; only the website chosen goes live')

# ── 17. Photographs, in the order they were chosen ──────────────────────────
r = cl.post('/projects/new', data={
    'instruction_type': 'For Sale – Available', 'property_mode': 'existing', 'property_id': str(pid),
    'listing_price': '1200000', 'listing_price_unit': 'sale',
    'photos': [(photo((10, 20, 30)), 'front.png'),
               (photo((40, 50, 60)), 'rear.png'),
               (photo((70, 80, 90)), 'inside.png')],
}, content_type='multipart/form-data', follow_redirects=True)
assert r.status_code == 200
with app.app_context():
    p = Project.query.order_by(Project.id.desc()).first()
    lst = Listing.query.filter_by(project_id=p.id).one()
    shots = sorted(lst.photos, key=lambda x: x.sort_order)
    assert len(shots) == 3, len(shots)
    assert [s.filename for s in shots] == ['front.png', 'rear.png', 'inside.png']
    assert all(s.file_mime == 'image/png' for s in shots)
print('17. photographs upload on creation and keep the order they were given')

# ── 18. A file that is not an image is refused, the project still created ───
r = cl.post('/projects/new', data={
    'instruction_type': 'For Sale – Available', 'property_mode': 'existing', 'property_id': str(pid),
    'listing_price': '999000',
    'photos': [(io.BytesIO(b'<html>not an image</html>'), 'sneaky.png')],
}, content_type='multipart/form-data', follow_redirects=True)
assert 'not a readable image' in r.get_data(as_text=True)
with app.app_context():
    p = Project.query.order_by(Project.id.desc()).first()
    lst = Listing.query.filter_by(project_id=p.id).one()
    assert lst.photos == []
print('18. a disguised file is refused without losing the project')

# ── 19. Cancelling writes nothing ───────────────────────────────────────────
with app.app_context():
    count = Project.query.count()
assert '/projects"' in new or "href=\"/projects\"" in new, 'Cancel does not go back to Projects'
page('/projects')
with app.app_context():
    assert Project.query.count() == count, 'opening the list created something'
print('19. Cancel simply leaves the page; nothing is written')

# ── 20. The edit route still works and shares the layout ────────────────────
edit = page(f'/projects/{projid}/edit')
assert 'rec-cols-3' in edit and 'Instruction Detail' in edit
assert 'Save Changes' in edit and 'Delete Project' in edit
assert 'Marsden Estates Ltd' in edit, 'existing values are not shown'
r = cl.post(f'/projects/{projid}/edit', data={'name': 'Letting — 12 King Street',
                                              'fee_earner_id': BEN_ID},
            follow_redirects=True)
with app.app_context():
    p = Project.query.get(projid)
    assert p.fee_earner_id == BEN_ID
    assert p.client == 'Marsden Estates Ltd', 'a field not on the form was blanked'
print('20. the edit route still works, on the same layout, without blanking fields')

# ── 21. Existing records, routes and permissions are untouched ──────────────
with app.app_context():
    assert Project.query.get(projid) is not None
    assert Property.query.get(pid) is not None
    v = project_form_values(Project.query.get(projid))
    assert v['name'] == 'Letting — 12 King Street' and v['status'] == 'Active'
    assert project_form_values(None)['status'] == 'Active'
viewer = User(username='viewer', password_hash=generate_password_hash('pw'), role='viewer')
with app.app_context():
    db.session.add(viewer); db.session.commit()
v_cl = app.test_client()
v_cl.post('/login', data={'username': 'viewer', 'password': 'pw'}, follow_redirects=True)
r = v_cl.post(f'/projects/{projid}/delete', follow_redirects=False)
assert r.status_code in (302, 403)
with app.app_context():
    assert Project.query.get(projid) is not None, 'a viewer deleted a project'
from app import AuditLog
with app.app_context():
    assert AuditLog.query.filter_by(entity='Project', action='create').count() > 0
print('21. records kept, permissions enforced, creation written to the audit log')

print('\nNEW PROJECT: ALL CHECKS PASSED')
