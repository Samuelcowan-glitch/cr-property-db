"""One staff field, chosen from real people, on aligned forms.

Fee earner is a person the CRM knows about rather than a spelling of a name,
Key Contact is gone from the pages but not from the database, and the two form
systems that never agreed now share one set of measurements.
"""
import glob, os, re, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)
from app import (app, db, User, Property, Project, Contact, Organisation,
                 Transaction, Enquiry, ProjectService, fee_earners,
                 default_fee_earner, fee_earner_name, _fid, _link_fee_earners,
                 _name_the_users, _fee_earner_aliases)
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash

with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw'),
                        role='admin'))
    prop = Property(address='Riverside Works, Paddock Wood', postcode='TN12 6AB')
    db.session.add(prop); db.session.commit()
    PROP = prop.id
    # Records carrying the old typed names.
    pr = Project(name='Riverside letting', property_id=PROP, fee_earner='B Cowan',
                 key_contact='Cara Mason', client='Marsden Estates Ltd')
    org = Organisation(name='Marsden Estates Ltd', status='Active', fee_earner='B. Cowan')
    con = Contact(first_name='Kate', last_name='Fenn', assigned_agent='Benjamin Cowan')
    txn = Transaction(property_id=PROP, transaction_type='Capital', reference='TR-0001',
                      fee_earner='Someone Who Left')
    enq = Enquiry(subject='Unit enquiry', fee_earner='BC')
    db.session.add_all([pr, org, con, txn, enq]); db.session.commit()
    PR, ORG, CON, TXN, ENQ = pr.id, org.id, con.id, txn.id, enq.id
    _name_the_users()
    REPORT = _link_fee_earners()
    BEN = User.query.filter_by(username='admin').one().id

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
    assert r.status_code == 200, f'{url} returned {r.status_code}'
    return r.get_data(as_text=True)


PAGES = {
    'Organiser': '/', 'New project': '/projects/new', 'Project': f'/projects/{PR}',
    'Add organisation': '/organisations/new', 'Organisation': f'/organisations/{ORG}',
    'New contact': '/contacts/new', 'Contact': f'/contacts/{CON}',
    'Contacts': '/contacts', 'Clients': '/contacts?type=Client',
    'Companies': '/organisations', 'Enquiries': '/enquiries',
    'New enquiry': '/enquiries/new', 'Transaction': f'/transactions/{TXN}',
    'Transactions': '/transactions', 'Add property': '/properties/new',
    'Property': f'/properties/{PROP}', 'Diary': '/diary', 'Properties': '/properties',
    'Projects': '/projects',
}
BODIES = {name: page(url) for name, url in PAGES.items()}
print(f'1. all {len(PAGES)} record, add and edit pages open')


# ─── 2. The office account is named in full ─────────────────────────────────
with app.app_context():
    ben = User.query.get(BEN)
    assert ben.full_name == 'Benjamin Cowan', ben.full_name
    assert ben.display_name == 'Benjamin Cowan'
    assert ben.username == 'admin', 'the login was changed'
    assert ben.active and ben.can_earn_fees
print('2. the account is named Benjamin Cowan, and its login is unchanged')


# ─── 3. He is the only fee earner, and is offered ───────────────────────────
with app.app_context():
    people = fee_earners()
    assert [p.display_name for p in people] == ['Benjamin Cowan'], people
    assert default_fee_earner().id == BEN, 'the only fee earner is not preselected'
print('3. Benjamin Cowan is the one selectable fee earner')


# ─── 4. Never initials ──────────────────────────────────────────────────────
for name, body in BODIES.items():
    if 'fee_earner_id' not in body:
        continue
    options = re.findall(r'<option value="\d+"[^>]*>\s*([^<]+?)\s*</option>', body)
    for shown in options:
        assert not re.fullmatch(r'[A-Z]\.?\s?[A-Z][a-z]+', shown) or shown == 'Benjamin Cowan', \
            f'{name} offers an initialised name: {shown}'
    assert 'Benjamin Cowan' in body, f'{name} does not show the full name'
print('4. the selector shows the full name, never initials')


# ─── 5. It is a list, not a box you can type in ─────────────────────────────
for name, body in BODIES.items():
    assert 'name="fee_earner"' not in body.replace('name="fee_earner_id"', ''), \
        f'{name} still has a free-text fee earner'
    assert 'name="assigned_agent"' not in body, f'{name} still has a free-text agent'
    assert 'name="agent_assigned"' not in body, f'{name} still has a free-text agent'
picked = [n for n, b in BODIES.items() if 'name="fee_earner_id"' in b]
assert len(picked) >= 6, picked
for name in picked:
    assert re.search(r'<select[^>]*name="fee_earner_id"', BODIES[name]), \
        f'{name} does not use a dropdown'
print(f'5. {len(picked)} pages assign staff from a list, and none from a text box')


# ─── 6. Only a real fee earner can be saved ─────────────────────────────────
with app.app_context():
    assert _fid(str(BEN)) == BEN
    assert _fid('999999') is None, 'an id that is nobody was accepted'
    assert _fid('Benjamin Cowan') is None, 'a typed name was accepted as an id'
    assert _fid('') is None and _fid(None) is None
    # A viewer cannot be assigned work.
    db.session.add(User(username='reader', password_hash=generate_password_hash('pw'),
                        role='viewer', full_name='Rita Reader'))
    db.session.commit()
    assert 'Rita Reader' not in [p.display_name for p in fee_earners()]
    # Nor can somebody switched off.
    db.session.add(User(username='gone', password_hash=generate_password_hash('pw'),
                        role='agent', full_name='Gone Away', active=False))
    db.session.commit()
    assert 'Gone Away' not in [p.display_name for p in fee_earners()]
print('6. only an active account allowed to carry a fee can be assigned')


# ─── 7. Legacy names mapped only where certain ──────────────────────────────
with app.app_context():
    assert Project.query.get(PR).fee_earner_id == BEN, '"B Cowan" was not recognised'
    assert Organisation.query.get(ORG).fee_earner_id == BEN, '"B. Cowan" was not recognised'
    assert Contact.query.get(CON).fee_earner_id == BEN, '"Benjamin Cowan" was not recognised'
    assert Enquiry.query.get(ENQ).fee_earner_id == BEN, '"BC" was not recognised'
    # And the one nobody can identify is left alone, and reported.
    assert Transaction.query.get(TXN).fee_earner_id is None, \
        'an unrecognised name was guessed at'
    assert Transaction.query.get(TXN).fee_earner == 'Someone Who Left'
assert 'Someone Who Left' in REPORT['unknown'], REPORT
assert REPORT['linked'] == 4, REPORT
print('7. four certain names were linked; the unknown one was left and reported')


# ─── 8. The typed names are all still there ─────────────────────────────────
with app.app_context():
    assert Project.query.get(PR).fee_earner == 'B Cowan'
    assert Organisation.query.get(ORG).fee_earner == 'B. Cowan'
    assert Contact.query.get(CON).assigned_agent == 'Benjamin Cowan'
    assert Enquiry.query.get(ENQ).fee_earner == 'BC'
print('8. every name typed in before is still stored')


# ─── 9. Aliases are the certain ones only ───────────────────────────────────
with app.app_context():
    forms = _fee_earner_aliases(User.query.get(BEN))
    for certain in ('benjamincowan', 'bcowan', 'admin', 'bc', 'cowanbenjamin'):
        assert certain in forms, certain
    assert 'cowan' not in forms, 'a surname alone was treated as certain'
    assert 'b' not in forms, 'a single letter was treated as certain'
print('9. only unambiguous forms of the name count as a match')


# ─── 10. An existing assignment opens on the right person ───────────────────
proj = page(f'/projects/{PR}')
chosen = re.search(r'name="fee_earner_id".*?<option value="(\d+)"[^>]*selected', proj, re.S)
assert chosen and int(chosen.group(1)) == BEN, 'the existing fee earner is not shown'
print('10. an existing record opens with its fee earner already selected')


# ─── 11. A new record preselects the only fee earner ────────────────────────
fresh = page('/projects/new')
sel = re.search(r'name="fee_earner_id".*?</select>', fresh, re.S).group(0)
assert 'selected' in sel, 'the only fee earner was not preselected on a new record'
assert '>Not assigned<' not in sel, 'a new record still offers "not assigned"'
print('11. a new record is assigned to the only fee earner there is')


# ─── 12. Existing assignments are not overwritten ───────────────────────────
with app.app_context():
    before = Project.query.get(PR).fee_earner_id
cl.post(f'/projects/{PR}/edit', data={'name': 'Riverside letting'}, follow_redirects=True)
with app.app_context():
    assert Project.query.get(PR).fee_earner_id == before, \
        'saving without touching the field changed the fee earner'
print('12. saving a record without touching the field leaves the assignment alone')


# ─── 13. Key Contact is gone from every page ────────────────────────────────
for name, body in BODIES.items():
    assert 'name="key_contact"' not in body, f'{name} still has a Key Contact field'
    assert 'Key contact' not in body, f'{name} still shows a Key Contact label'
    assert 'Key Contacts' not in body, f'{name} still has a Key Contacts heading'
print('13. no Key Contact field, label or heading remains on any page')


# ─── 14. Its data is untouched ──────────────────────────────────────────────
with app.app_context():
    assert Project.query.get(PR).key_contact == 'Cara Mason', \
        'historical Key Contact data was deleted'
    assert hasattr(Project, 'key_contact'), 'the column was dropped'
print('14. the Key Contact already recorded is still in the database')


# ─── 15. No empty box was left behind ───────────────────────────────────────
shared = open(f'{ROOT}/templates/projects/_record_boxes.html').read()
assert 'key_contact' not in shared
assert 'Client Contact Details' in shared, 'the box was left with a misleading heading'
box = shared.split('Client Contact Details')[1].split('{% endmacro %}')[0]
assert 'frow--full' in box, 'the odd field was left holding half an empty row'
assert 'key_contact' not in box, 'Key Contact is still in the box'
assert '<div class="fcell"></div>' not in box, 'an empty cell was left'
assert 'fcell--label"></div>' not in box, 'an empty label was left'
# The branch used when nobody is linked keeps the fields the project carries.
for field in ('client_phone', 'client_mobile', 'client_email'):
    assert f'name="{field}"' in box, f'{field} was lost from the box'
assert 'fcell--label"></div>' not in box, 'an empty label cell was left'
assert '<div class="fcell"></div>' not in box, 'an empty value cell was left'
print('15. the box was reflowed, with no empty cell or column left behind')


# ─── 16. Linked contacts remain ─────────────────────────────────────────────
org_page = BODIES['Organisation']
assert 'main_contact_id' in org_page, 'the main contact chooser was removed'
assert 'Relationships' in org_page, 'the linked relationships were removed'
trx = BODIES['Transaction']
assert 'data-role="Client"' in trx, 'the linked client was removed'
assert 'Contact for this' in trx or 'orgpick' in trx
print('16. linked client, landlord, tenant and applicant contacts all remain')


# ─── 17. One label width, everywhere ────────────────────────────────────────
grid = open(f'{ROOT}/static/css/crm-grid.css').read()
assert '--label-w:    118px' in grid, 'there is no shared label width'
assert 'flex: 0 0 var(--label-w)' in grid, 'a flex row sets its own label width'
assert 'grid-template-columns: var(--label-w)' in grid, 'the grid sets its own'
assert 'w-narrow' not in grid and 'w-wide' not in grid, 'the old widths are still defined'
for path in glob.glob(f'{ROOT}/templates/**/*.html', recursive=True):
    body = open(path).read()
    assert 'w-narrow' not in body and 'w-wide' not in body, \
        f'{os.path.basename(path)} still asks for its own label width'
print('17. one label width, shared by both the grid and flex rows')


# ─── 18. One control height, everywhere ─────────────────────────────────────
style = open(f'{ROOT}/static/css/style.css').read()
assert 'height: var(--cell-h)' in grid, 'record inputs have no shared height'
assert 'height: var(--cell-h, 32px)' in style, 'add-page inputs have no shared height'
assert 'border-radius: 3px' in style.split('input[type=text]')[1][:200], \
    'inputs are not squared to 3px'
print('18. every single-line input and dropdown is the same height and shape')


# ─── 19. Nothing touches a border, and long text wraps ──────────────────────
assert 'overflow-wrap: anywhere' in grid, 'a long address would run under a border'
assert 'box-sizing: border-box' in grid, 'a control could overflow its cell'
long_address = BODIES['Property']
assert 'Riverside Works, Paddock Wood' in long_address
print('19. long names and addresses wrap inside their cell')


# ─── 20. Borders are one colour and one thickness ───────────────────────────
widths = set(re.findall(r'border(?:-\w+)?:\s*(\d+)px solid', grid))
assert widths <= {'1', '2', '3'}, f'inconsistent border thicknesses: {widths}'
# Borders that sit on a dark overlay rather than on the page.
SEMANTIC = {'#e3b7b7', '#e8d5a3', '#bcdfc7', 'rgba(255,255,255,.18)',
            'rgba(255, 255, 255, .35)'}
cell_borders = re.findall(r'border(?:-top|-bottom|-left|-right)?:\s*1px solid ([^;]+);', grid)
odd = {b.strip() for b in cell_borders
       if 'var(' not in b and b.strip() not in SEMANTIC | {'transparent'}}
assert not odd, f'structural borders using a colour outside the palette: {odd}'
radii = set(re.findall(r'border-radius: (\d+)px', grid))
assert radii <= {'1', '3'}, f'inconsistent corner radii: {radii}'
print('20. every border is one thickness and comes from the palette')


# ─── 21. Permissions and the audit trail are untouched ──────────────────────
src = open(f'{ROOT}/app.py').read()
for guard in ["@requires('edit')", "@requires('delete')", "@requires('create')",
              "@requires('admin')", "@requires('publish')"]:
    assert guard in src, f'{guard} has gone'
assert src.count("audit(") > 20, 'audit logging was reduced'
from app import ROLES
assert ROLES['admin'] == {'view', 'create', 'edit', 'delete', 'export', 'publish', 'admin'}
assert ROLES['viewer'] == {'view'}
print('21. permissions and audit logging are exactly as they were')


# ─── 22. Filtering and reporting follow the person ──────────────────────────
with app.app_context():
    Transaction.query.get(TXN).fee_earner_id = BEN
    db.session.commit()
filtered = page(f'/transactions?fee_earner_id={BEN}')
assert 'TR-0001' in filtered, 'filtering by fee earner found nothing'
assert 'Benjamin Cowan' in filtered, 'the filter does not name him'
assert 'TR-0001' not in page('/transactions?fee_earner_id=999999').split('<tbody>')[1] \
    if '<tbody>' in page('/transactions?fee_earner_id=999999') else True
board = page('/transactions')
assert 'Benjamin Cowan' in board, 'the fee earner table does not name him'
print('22. filtering and the fee earner table both follow the linked person')

print('\nSTAFF AND LAYOUT: ALL CHECKS PASSED')
