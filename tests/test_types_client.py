"""Two new property types, and a client whose details are read not copied.

Light Industrial is its own category: matching, filtering and reporting must
never fold it into Industrial. And a property or instruction linked to a client
shows that client's live details, so changing their record changes what the
property shows.
"""
import glob, os, re, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)
from app import (app, db, User, Property, Project, Contact, Organisation, Listing,
                 PROPERTY_TYPES, ALL_PROPERTY_TYPES, property_type_options,
                 same_property_type, score_requirement, match_properties_to_contact,
                 contact_label, INSTRUCTION_TO_LET)
import zoopla_feed as zf
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash

STUDIO, LIGHT = 'Creative / Art Studio', 'Light Industrial'

with app.app_context():
    db.create_all()
    for who, role in (('admin', 'admin'), ('reader', 'viewer')):
        db.session.add(User(username=who, password_hash=generate_password_hash('pw'),
                            role=role, full_name='Benjamin Cowan'))
    org = Organisation(name='Example Holdings Ltd', status='Active',
                       address='12 Bank Street, Tonbridge')
    db.session.add(org); db.session.commit()
    jane = Contact(first_name='Jane', last_name='Smith', organisation_id=org.id,
                   email='jane@example.co.uk', mobile='07700 900 100',
                   phone='01732 555 100', job_title='Estates Director',
                   contact_type='Client')
    solo = Contact(first_name='Peter', last_name='Vance', contact_type='Client')
    db.session.add_all([jane, solo]); db.session.commit()
    props = {}
    for label, kind in (('studio', STUDIO), ('light', LIGHT), ('heavy', 'Industrial')):
        p = Property(address=f'{label.title()} Unit, Tonbridge', postcode='TN9 1AA',
                     property_type=kind, size=1200, area='Tonbridge',
                     website_listed=True, listing_status='available',
                     listing_price=24000, listing_price_unit='pa')
        db.session.add(p); db.session.commit()
        pr = Project(name=f'{label} instruction', property_id=p.id,
                     instruction_type=INSTRUCTION_TO_LET)
        db.session.add(pr); db.session.commit()
        db.session.add(Listing(project_id=pr.id, property_id=p.id,
                               listing_status='available', website_listed=True,
                               listing_price=24000, listing_price_unit='pa'))
        db.session.commit()
        props[label] = p.id
    legacy = Property(address='Old Barn', postcode='TN9 2BB', property_type='Warehouse')
    db.session.add(legacy); db.session.commit()
    LEGACY, JANE, SOLO, ORG = legacy.id, jane.id, solo.id, org.id
    PROJ = Project.query.first().id

with app.app_context():
    # A property needs its billing authority before it can be created.
    _mig = getattr(__import__('app'), '_migrate_rates_tables', None)
    if _mig:
        _mig()
    _c = __import__('app').Council.query.first()
    COUNCIL_ID = _c.id if _c else None

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def page(url, client=None):
    r = (client or cl).get(url)
    assert r.status_code == 200, f'{url} returned {r.status_code}'
    return r.get_data(as_text=True)


# ─── 1. Both types exist, alongside the others ──────────────────────────────
assert PROPERTY_TYPES == ['Office', 'Retail', 'Industrial', STUDIO, LIGHT], PROPERTY_TYPES
assert STUDIO in ALL_PROPERTY_TYPES and LIGHT in ALL_PROPERTY_TYPES
print('1. the five primary types are offered, in order')


# ─── 2. One internal value, used everywhere ─────────────────────────────────
src = open(f'{ROOT}/app.py').read()
assert src.count("'Creative / Art Studio'") == 1, 'the label is written out more than once'
assert src.count("'Light Industrial'") == 1
for path in glob.glob(f'{ROOT}/templates/**/*.html', recursive=True):
    body = open(path).read()
    if 'Creative' in body or ('Light Industrial' in body and 'listing_form' not in path):
        assert 'property_type_options' in body or 'PROPERTY_TYPES' in body, \
            f'{os.path.basename(path)} writes a type out by hand'
print('2. each type is stored as one value, from one list')


# ─── 3. Legacy values are kept and still offered ────────────────────────────
with app.app_context():
    assert Property.query.get(LEGACY).property_type == 'Warehouse', \
        'a legacy type was changed'
    assert 'Warehouse' in property_type_options()
    assert 'Anything Odd' in property_type_options('Anything Odd'), \
        'a record with an unusual type would lose it'
print('3. legacy types are preserved and stay selectable')


# ─── 4. Both appear wherever a type is chosen ───────────────────────────────
for name, url in [('Add property', '/properties/new'),
                  ('Property', f'/properties/{props["studio"]}'),
                  ('Add instruction', '/projects/new'),
                  ('New contact', '/contacts/new'),
                  ('Properties', '/properties')]:
    body = page(url)
    for kind in (STUDIO, LIGHT):
        assert kind in body, f'{kind} is missing from {name}'
print('4. both types are offered on every page that asks for one')


# ─── 5. Light Industrial is never folded into Industrial ────────────────────
assert same_property_type(LIGHT, LIGHT)
assert not same_property_type('Industrial', LIGHT), \
    'Industrial is being treated as Light Industrial'
assert not same_property_type(LIGHT, 'Industrial'), \
    'Light Industrial is being treated as Industrial'
assert not same_property_type(STUDIO, 'Office')
print('5. Industrial and Light Industrial are separate categories')


# ─── 6. Filtering keeps them apart ──────────────────────────────────────────
def listed(ptype):
    body = page(f'/properties?property_type={ptype.replace(" ", "+").replace("/", "%2F")}')
    rows = body.split('class="rec-list"')[1].split('</div>\n</div>')[0]
    return {m for m in re.findall(r'(\w+) Unit, Tonbridge', rows)}


assert listed('Industrial') == {'Heavy'}, listed('Industrial')
assert listed(LIGHT) == {'Light'}, listed(LIGHT)
assert listed(STUDIO) == {'Studio'}, listed(STUDIO)
print('6. filtering by Industrial does not return Light Industrial, or the reverse')


# ─── 7. Matching keeps them apart too ───────────────────────────────────────
with app.app_context():
    seeker = Contact(first_name='Ada', last_name='Quill', req_property_type=LIGHT,
                     req_budget_unit='pa', req_size_min=500, req_size_max=2000,
                     req_area='Tonbridge')
    db.session.add(seeker); db.session.commit()
    matched = {m['prop'].property_type for m in match_properties_to_contact(seeker)}
    assert matched == {LIGHT}, f'a Light Industrial search returned {matched}'
    # And somebody after a studio is not offered a warehouse.
    seeker.req_property_type = STUDIO
    db.session.commit()
    matched = {m['prop'].property_type for m in match_properties_to_contact(seeker)}
    assert matched == {STUDIO}, f'a studio search returned {matched}'
print('7. an applicant asking for one type is never offered the other')


# ─── 8. Every other requirement still has to be met ─────────────────────────
with app.app_context():
    seeker = Contact.query.filter_by(last_name='Quill').one()
    seeker.req_property_type = LIGHT
    db.session.commit()
    light = Property.query.get(props['light'])

    # Size out of range.
    seeker.req_size_min, seeker.req_size_max = 5000, 9000
    db.session.commit()
    assert score_requirement(seeker, light) == (0, []), 'size was ignored'
    seeker.req_size_min, seeker.req_size_max = 500, 2000

    # Wrong area.
    seeker.req_area = 'Maidstone'
    db.session.commit()
    assert score_requirement(seeker, light) == (0, []), 'location was ignored'
    seeker.req_area = 'Tonbridge'

    # Wanting to buy, when it is only to let.
    seeker.req_budget_unit = 'sale'
    db.session.commit()
    assert score_requirement(seeker, light) == (0, []), 'to let or for sale was ignored'
    seeker.req_budget_unit = 'pa'

    # Budget below the rent.
    seeker.req_budget_max = 5000
    db.session.commit()
    assert score_requirement(seeker, light) == (0, []), 'budget was ignored'
    seeker.req_budget_max = None
    db.session.commit()
    assert score_requirement(seeker, light)[0] > 0, 'nothing matches at all'
print('8. type, size, location, tenure and budget must all be met')


# ─── 9. Not on the market means no match ────────────────────────────────────
with app.app_context():
    light = Property.query.get(props['light'])
    for l in Listing.query.filter_by(property_id=light.id).all():
        l.listing_status = 'let'
    db.session.commit()
    seeker = Contact.query.filter_by(last_name='Quill').one()
    assert score_requirement(seeker, light) == (0, []), 'a let property still matched'
    for l in Listing.query.filter_by(property_id=light.id).all():
        l.listing_status = 'available'
    db.session.commit()
print('9. availability is checked before anything is offered')


# ─── 10. Zoopla mapping, documented and non-blocking ────────────────────────
assert zf.zoopla_category_for(LIGHT) == 'Industrial', zf.zoopla_category_for(LIGHT)
assert zf.zoopla_category_for(STUDIO) == 'Office', zf.zoopla_category_for(STUDIO)
assert zf.zoopla_category_for('Office') == 'Office'
assert zf.zoopla_category_for('Warehouse') is None, 'an unmapped type was given a category'
feed_src = open(f'{ROOT}/zoopla_feed.py').read()
assert 'ZOOPLA_TYPE_MAP' in feed_src and 'nearest supported' in feed_src, \
    'the mapping is not documented'


class _P:
    property_type = LIGHT


assert zf._prop_sub_id(None, _P()) == 0, 'an unmapped code should not block a listing'
with app.app_context():
    # The CRM keeps its own type whatever the portal is told.
    assert Property.query.get(props['light']).property_type == LIGHT
assert LIGHT in page(f'/properties/{props["light"]}'), \
    "the portal's category is being shown instead of the CRM type"
print('10. each type maps to a Zoopla category without changing the CRM type')


# ─── 11. A client is chosen on a property, and shown live ───────────────────
prop_page = page(f'/properties/{props["studio"]}')
assert 'data-client-picker' in prop_page, 'there is no client selector on the property'
assert 'name="client_contact_id"' in prop_page
assert 'Client details' in prop_page, 'there is no client details panel'
assert 'No client selected' in prop_page, 'an empty state is not shown'
cl.post(f'/properties/{props["studio"]}/edit',
        data={'address': 'Studio Unit, Tonbridge', 'postcode': 'TN9 1AA',
              'client_contact_id': JANE}, follow_redirects=True)
with app.app_context():
    assert Property.query.get(props['studio']).client_contact_id == JANE
print('11. a property links a client, and shows an empty state until it does')


# ─── 12. Their details are read, never copied ───────────────────────────────
body = page(f'/properties/{props["studio"]}')
assert 'Jane Smith (Example Holdings Ltd)' in body
assert 'mailto:jane@example.co.uk' in body, 'the email is not clickable'
assert 'tel:07700900100' in body, 'the mobile is not clickable'
assert 'tel:01732555100' in body, 'the office number is not clickable'
assert 'Estates Director' in body and '12 Bank Street, Tonbridge' in body
with app.app_context():
    columns = {c.name for c in Property.__table__.columns}
    assert 'client_contact_id' in columns
    assert not [c for c in columns if 'client_email' in c or 'client_company' in c], \
        'the client details were copied onto the property'
print('12. the client details shown are read from their own record')


# ─── 13. Changing the client record changes what the property shows ─────────
with app.app_context():
    Contact.query.get(JANE).email = 'j.smith@newmail.co.uk'
    Organisation.query.get(ORG).name = 'Example Holdings (Kent) Ltd'
    db.session.commit()
body = page(f'/properties/{props["studio"]}')
assert 'j.smith@newmail.co.uk' in body, 'the property is showing a stale email'
assert 'Example Holdings (Kent) Ltd' in body, 'the property is showing a stale company'
assert 'jane@example.co.uk' not in body
print('13. updating the client record updates what the property shows')


# ─── 14. Switching and removing the client ──────────────────────────────────
cl.post(f'/properties/{props["studio"]}/edit',
        data={'address': 'Studio Unit, Tonbridge', 'postcode': 'TN9 1AA',
              'client_contact_id': SOLO}, follow_redirects=True)
body = page(f'/properties/{props["studio"]}')
assert 'Peter Vance' in body and 'Not linked to a company' in body
assert 'j.smith@newmail.co.uk' not in body, "the previous client's details are still shown"
cl.post(f'/properties/{props["studio"]}/edit',
        data={'address': 'Studio Unit, Tonbridge', 'postcode': 'TN9 1AA',
              'client_contact_id': ''}, follow_redirects=True)
assert 'No client selected' in page(f'/properties/{props["studio"]}')
with app.app_context():
    assert Contact.query.get(SOLO) is not None, 'removing the link deleted the person'
    assert Contact.query.count() >= 3
print('14. switching replaces the details at once; removing leaves an empty state')


# ─── 15. Somebody with no company shows no empty brackets ───────────────────
with app.app_context():
    assert contact_label(Contact.query.get(SOLO)) == 'Peter Vance'
body = page(f'/properties/{props["studio"]}')
panel = body.split('Client details')[1].split('</div>\n</div>')[0]
assert '()' not in panel, 'empty brackets appeared in the client details'
assert 'None' not in panel and 'No company' not in panel, \
    'a missing company was written out rather than left off'
print('15. a client with no company shows their name alone')


# ─── 16. No duplicate organisation section came back ────────────────────────
for name, url in [('Instruction', f'/projects/{PROJ}'),
                  ('Property', f'/properties/{props["studio"]}')]:
    body = page(url)
    assert 'Linked organisation' not in body, f'{name} has a Linked Organisation section'
    assert 'data-target-kind="project"' not in body
    assert 'name="organisation_id"' not in body, f'{name} has a duplicate organisation field'
    assert 'name="company_name"' not in body
print('16. no Linked Organisation section and no duplicate company field')


# ─── 17. Sensitive details respect permission ───────────────────────────────
cl.post(f'/properties/{props["studio"]}/edit',
        data={'address': 'Studio Unit, Tonbridge', 'postcode': 'TN9 1AA',
              'client_contact_id': JANE}, follow_redirects=True)
viewer = app.test_client()
viewer.post('/login', data={'username': 'reader', 'password': 'pw'}, follow_redirects=True)
seen = page(f'/properties/{props["studio"]}', client=viewer)
assert 'Jane Smith' in seen, 'a viewer cannot see who the client is'
assert 'j.smith@newmail.co.uk' not in seen, "a viewer can read the client's email"
assert 'tel:07700900100' not in seen, "a viewer can read the client's mobile"
assert 'may edit records' in seen, 'nothing explains why the details are hidden'
print('17. a viewer sees who the client is but not their direct details')


# ─── 18. Creating a property of each new type ───────────────────────────────
for kind in (STUDIO, LIGHT):
    cl.post('/properties/new', data={'council_id': COUNCIL_ID, 'council_id': COUNCIL_ID, 'address': f'New {kind} Unit', 'postcode': 'TN9 3CC',
                                     'property_type': kind}, follow_redirects=True)
with app.app_context():
    for kind in (STUDIO, LIGHT):
        made = Property.query.filter_by(address=f'New {kind} Unit').one()
        assert made.property_type == kind, made.property_type
print('18. a property of each new type can be created and keeps its type')

print('\nPROPERTY TYPES AND CLIENT DETAILS: ALL CHECKS PASSED')
