"""One fee earner, so nobody is asked to choose one.

There is a single fee earner in the office. Every page that recorded one still
offered a dropdown of one name, with "Not assigned" above it — a question with
one answer, and a way to get it wrong. Wherever a fee earner is recorded it is
now filled in and shown, not chosen.

Not by writing the name into the pages: by asking one place for the control
and one place for the id. A second account brings the list back everywhere at
once, which this checks too, because a CRM that has to be rewritten to hire
somebody is not built right.
"""
import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/fee.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db

with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    A._migrate_enquiry_columns()
    A._migrate_crm_columns()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          email='bc@cowanandrutter.co.uk', active=True,
                          can_earn_fees=True,
                          password_hash=generate_password_hash('pw')))
    db.session.commit()
    prop = A.Property(address='42 Peterborough Road, London SW6 3BN',
                      postcode='SW6 3BN', property_type='Office', size=1636)
    db.session.add(prop); db.session.commit()
    PROP = prop.id
    BEN = A.User.query.filter_by(username='admin').first().id

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


# ─── 1. He is the one fee earner, and the one the CRM reaches for ───────────
with A.app.app_context():
    people = A.fee_earners()
    assert len(people) == 1, [p.display_name for p in people]
    assert people[0].display_name == 'Benjamin Cowan', people[0].display_name
    assert A.default_fee_earner().id == BEN
print('1. Benjamin Cowan is the only fee earner')


# ─── 2. No page offers a choice ─────────────────────────────────────────────
PAGES = ['/enquiries/new', '/contacts/new', '/projects/new', '/organisations/new',
         '/transactions/new', '/contacts', '/transactions', '/enquiries']
for url in PAGES:
    r = cl.get(url)
    assert r.status_code == 200, (url, r.status_code)
    html = r.get_data(as_text=True)
    assert not re.search(r'<select[^>]*name="fee_earner_id"', html), \
        f'{url} still offers a fee earner dropdown'
    assert 'Not assigned' not in html or 'fee_earner' not in html, \
        f'{url} still offers "Not assigned"'
print(f'2. none of the {len(PAGES)} pages offers a fee earner to choose')

# And where one is recorded, his name is shown rather than left blank.
r = cl.get('/enquiries/new')
assert 'Benjamin Cowan' in r.get_data(as_text=True), \
    'the new-enquiry page does not show who the fee earner is'
print('   the name is shown instead')


# ─── 3. Whatever is posted, the record is his ───────────────────────────────
# Nothing sent at all.
r = cl.post('/enquiries/new', data={
    'enquiry_type': 'Tenant — Looking to Rent', 'status': 'Open',
    'contact_mode': 'new', 'caller_name': 'Jane Whitfield',
    'caller_email': 'jane@example.co.uk'}, follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    e = A.Enquiry.query.order_by(A.Enquiry.id.desc()).first()
    assert e.fee_earner_id == BEN, e.fee_earner_id
print('3. an enquiry saved with no fee earner sent is his')

# A blank, and an id that is nobody, both come back as him rather than as
# nothing — the form cannot be edited to leave a record unassigned.
for sent in ('', '9999', 'nonsense'):
    r = cl.post('/enquiries/new', data={
        'enquiry_type': 'Buyer — Looking to Buy', 'status': 'Open',
        'fee_earner_id': sent}, follow_redirects=True)
    assert r.status_code == 200
    with A.app.app_context():
        e = A.Enquiry.query.order_by(A.Enquiry.id.desc()).first()
        assert e.fee_earner_id == BEN, f'{sent!r} gave {e.fee_earner_id}'
print('   a blank, an unknown id and a nonsense value all come back as him')


# ─── 4. A website lead is his the moment it lands ───────────────────────────
A._ENQUIRY_HITS.clear()
r = cl.post('/api/enquiry', json={
    'from_name': 'Peter Ngozi', 'from_email': 'peter@example.com',
    'interest': 'Commercial Agency', 'message': 'Looking for a shop.'},
    headers={'Origin': 'https://cowanandrutter.com'})
assert r.status_code == 200, r.get_data(as_text=True)
with A.app.app_context():
    e = A.Enquiry.query.filter_by(source='Website').order_by(A.Enquiry.id.desc()).first()
    assert e is not None, 'no website enquiry was recorded'
    assert e.fee_earner_id == BEN, e.fee_earner_id
print('4. a lead off the website is his the moment it lands')


# ─── 5. A project and a contact, the same ───────────────────────────────────
r = cl.post('/projects/new', data={
    'name': 'Marlin', 'property_mode': 'existing', 'property_id': str(PROP),
    'instruction_type': A.INSTRUCTION_TO_LET, 'status': 'Active'},
    follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    p = A.Project.query.order_by(A.Project.id.desc()).first()
    assert p is not None and p.fee_earner_id == BEN, getattr(p, 'fee_earner_id', None)
print('5. an instruction is his')

r = cl.post('/contacts/new', data={
    'first_name': 'Ada', 'last_name': 'Okonkwo', 'contact_type': 'Tenant'},
    follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    c = A.Contact.query.filter_by(last_name='Okonkwo').first()
    assert c is not None and c.fee_earner_id == BEN, getattr(c, 'fee_earner_id', None)
print('   and so is a contact')


# ─── 6. Hiring somebody brings the choice back, everywhere, at once ─────────
# The point of doing this in one place. Nothing is rewritten to add a second
# fee earner: the list simply returns.
with A.app.app_context():
    db.session.add(A.User(username='kathryn', role='staff',
                          full_name='Kathryn Rutter', active=True,
                          can_earn_fees=True,
                          password_hash=generate_password_hash('pw')))
    db.session.commit()
    assert len(A.fee_earners()) == 2
    assert A.default_fee_earner() is None, \
        'one is still assumed where there are two'

for url in ('/enquiries/new', '/contacts/new', '/projects/new'):
    html = cl.get(url).get_data(as_text=True)
    assert re.search(r'<select[^>]*name="fee_earner_id"', html), \
        f'{url} does not offer the choice once there are two'
    assert 'Kathryn Rutter' in html, f'{url} does not offer the second one'
print('6. a second account brings the dropdown back on every page at once')

# And with a real choice, the CRM stops choosing: a blank stays a blank.
r = cl.post('/enquiries/new', data={
    'enquiry_type': 'Valuation', 'status': 'Open', 'fee_earner_id': ''},
    follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    e = A.Enquiry.query.order_by(A.Enquiry.id.desc()).first()
    assert e.fee_earner_id is None, \
        f'work was assigned to {e.fee_earner_id} without anybody choosing'
print('   and with two, nobody is assigned work the office did not choose')

print('\nFEE EARNER: ALL CHECKS PASSED')
