"""The new-enquiry form asks what the kind of enquiry calls for.

It used to ask "Who and what it is about" — which existing contact, which
organisation, which instruction — and then one flat list of applicant
requirements. None of that suits somebody who has just rung up: they are not
on the CRM yet, and what is worth asking a prospective tenant is not what is
worth asking somebody who wants a valuation.

Several types ask for the same column under a different heading: a tenant's
preferred area and a vendor's address are both req_area. The page shows one at
a time and disables the rest, because a disabled box is not submitted. This
checks that for every type exactly one control claims each name — if that ever
broke, the browser would send both and the record would silently take
whichever came first.
"""
import os
import sys
import tempfile
from html.parser import HTMLParser

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/enqform.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db

VOID = {'input', 'hr', 'br', 'img', 'meta', 'link'}

with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    A._migrate_enquiry_columns()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          email='bc@cowanandrutter.co.uk',
                          password_hash=generate_password_hash('pw')))
    db.session.commit()

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)

r = cl.get('/enquiries/new')
assert r.status_code == 200, r.status_code
HTML = r.get_data(as_text=True)


# ─── 1. The old section and its pickers are gone ────────────────────────────
for gone in ('Who and what it is about', 'Link Project',
             'name="contact_id"', 'name="organisation_id"'):
    assert gone not in HTML, f'the form still carries {gone!r}'
print('1. "Who and what it is about" and its contact pickers are gone')


# ─── 2. The caller is typed in rather than chosen ───────────────────────────
for want in ('name="caller_name"', 'name="caller_company"',
             'name="caller_phone"', 'name="caller_email"'):
    assert want in HTML, f'the form cannot record {want}'
print('2. the caller\'s name, company, phone and email are typed in')


# ─── 3. Each kind of enquiry has its own heading ────────────────────────────
for heading in ('What they are looking to rent', 'What they are looking to buy',
                'What they have to let', 'The property they want to sell',
                'What needs valuing'):
    assert heading in HTML, f'no section for: {heading}'
assert 'Valuation' in [t for t in A.INQUIRY_TYPES], 'Valuation is not offered'
print('3. tenant, buyer, landlord, vendor and valuation each have a section')


class Fields(HTMLParser):
    """Every named control, with the data-for group it sits in."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.found = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        own = a.get('data-for')
        scope = own if own is not None else (self.stack[-1] if self.stack else None)
        # The page carries more than one form, each with its own token, and
        # two forms may both hold a token without either being ambiguous.
        if (a.get('name') and tag in ('input', 'select', 'textarea')
                and not a['name'].startswith('_')):
            self.found.append((a['name'], scope, a.get('type', '')))
        if tag not in VOID:
            self.stack.append(scope)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag not in VOID and self.stack:
            self.stack.pop()


parser = Fields()
parser.feed(HTML)


def shown_for(*keys):
    """The controls the page leaves enabled for this type — the rest disable.

    More than one key where a type asks a question of its own: a letting
    enquiry is either against an instruction on the books or one made here,
    and the two never show together.
    """
    active = [k for k in keys if k] or ['none']
    out = []
    for name, scope, _type in parser.found:
        if scope is None:
            out.append(name)                      # always on the page
        elif any(k in scope.split() for k in active):
            out.append(name)
    return out


# ─── 4. No name is claimed twice, for any type ──────────────────────────────
KEYS = [(), ('tenant',), ('buyer',), ('landlord', 'landlord-existing'),
        ('landlord', 'landlord-new'), ('vendor',), ('valuation',)]
for key in KEYS:
    names = shown_for(*key)
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f'type {key or "(general)"} submits {sorted(dupes)} twice'
print(f'4. no field is submitted twice, across all {len(KEYS)} sets of questions')

# The two ways of giving a letting instruction never appear together.
assert 'project_id' in shown_for('landlord', 'landlord-existing')
assert 'project_id' not in shown_for('landlord', 'landlord-new')
assert 'new_address' in shown_for('landlord', 'landlord-new')
assert 'new_address' not in shown_for('landlord', 'landlord-existing')
assert 'project_id' not in shown_for('tenant'), 'a tenant is offered an instruction'
print('   linking an instruction and adding one are never offered together')


# ─── 5. Each type is asked what it should be ────────────────────────────────
EXPECTED = {
    'tenant':    ['req_area', 'req_size_min', 'req_size_max', 'req_budget_min',
                  'req_budget_max', 'req_budget_unit', 'req_occupation_date'],
    'buyer':     ['req_area', 'req_size_min', 'req_size_max', 'req_budget_min',
                  'req_budget_max', 'req_tenure'],
    'landlord':  ['letting_mode', 'req_budget_min', 'req_budget_unit',
                  'req_occupation_date'],
    'vendor':    ['req_area', 'req_size_min', 'req_budget_min'],
    'valuation': ['req_area', 'req_size_min', 'req_tenure', 'req_notes'],
}
for key, wanted in EXPECTED.items():
    names = shown_for(key, f'{key}-existing')
    for field in wanted:
        assert field in names, f'a {key} enquiry is never asked for {field}'
    print(f'   {key:10s} asks for {len(set(names))} fields')

# And a tenant is not asked a vendor's question, nor the other way round.
assert 'req_tenure' not in shown_for('tenant'), 'a tenant is asked about tenure'
assert 'req_occupation_date' not in shown_for('vendor'), \
    'a vendor is asked when they want to move in'
print('5. every type is asked its own questions and not another type\'s')


# ─── 6. What is typed is what is stored ─────────────────────────────────────
r = cl.post('/enquiries/new', data={
    'enquiry_type': 'Tenant — Looking to Rent', 'status': 'Open',
    'caller_name': 'Jane Whitfield', 'caller_company': 'Acme Ltd',
    'caller_phone': '07700 900123', 'caller_email': 'jane@acme.co.uk',
    'req_category': 'commercial', 'req_use_class': 'office',
    'req_area': 'Fulham SW6', 'req_size_min': '500', 'req_size_max': '2000',
    'req_budget_min': '20000', 'req_budget_max': '60000',
    'req_budget_unit': 'pa', 'req_occupation_date': '2026-12-01',
    'req_notes': 'Ground floor, parking',
}, follow_redirects=True)
assert r.status_code == 200

with A.app.app_context():
    e = A.Enquiry.query.order_by(A.Enquiry.id.desc()).first()
    assert e.caller_name == 'Jane Whitfield', e.caller_name
    assert e.caller_phone == '07700 900123'
    assert e.caller_email == 'jane@acme.co.uk'
    assert e.req_area == 'Fulham SW6'
    assert e.req_occupation_date is not None
    assert e.req_notes == 'Ground floor, parking'

r = cl.post('/enquiries/new', data={
    'enquiry_type': 'Valuation', 'status': 'Open',
    'caller_name': 'Peter Ngozi', 'caller_phone': '020 7946 0000',
    'req_category': 'residential', 'req_area': '57B New Kings Road, SW6',
    'req_size_min': '1200', 'req_tenure': 'freehold',
    'req_budget_unit': 'sale', 'req_notes': 'Probate',
}, follow_redirects=True)
assert r.status_code == 200

with A.app.app_context():
    e = A.Enquiry.query.order_by(A.Enquiry.id.desc()).first()
    assert e.enquiry_type == 'Valuation'
    assert e.req_tenure == 'freehold'
    assert e.req_notes == 'Probate', e.req_notes
    assert e.caller_name == 'Peter Ngozi'
print('6. a tenant enquiry and a valuation each store what they were asked')


# ─── 7. A landlord's enquiry is filed against an instruction ────────────────
# Adding a new one: the property, the instruction and the link are all made.
before = None
with A.app.app_context():
    before = A.Project.query.count()

r = cl.post('/enquiries/new', data={
    'enquiry_type': 'Landlord — Looking to Let', 'status': 'Open',
    'caller_name': 'Margaret Osei', 'caller_company': 'Osei Estates',
    'caller_phone': '020 7946 1111',
    'letting_mode': 'new',
    'new_address': '12 Harwood Road, London', 'new_postcode': 'SW6 4QP',
    'new_property_type': 'Office', 'new_size': '1800',
    'req_budget_min': '45000', 'req_budget_unit': 'pa',
    'req_occupation_date': '2026-11-01',
}, follow_redirects=True)
assert r.status_code == 200

with A.app.app_context():
    assert A.Project.query.count() == before + 1, 'no instruction was created'
    e = A.Enquiry.query.order_by(A.Enquiry.id.desc()).first()
    assert e.project_id, 'the enquiry was not filed against an instruction'
    project = A.Project.query.get(e.project_id)
    assert project.instruction_type == A.INSTRUCTION_TO_LET, project.instruction_type
    assert e.property_id == project.property_id, 'the property does not match'
    prop = A.Property.query.get(e.property_id)
    assert '12 Harwood Road' in prop.address, prop.address
    assert prop.postcode == 'SW6 4QP', prop.postcode
    assert prop.size == 1800, prop.size
    assert project.landlord_name == 'Margaret Osei', project.landlord_name
    assert '12 Harwood Road' in e.subject, e.subject
    MADE = project.id
print('7. adding a new one creates the property, the To Let instruction '
      'and the link')

# The same address again reuses the property already on file.
with A.app.app_context():
    props_before = A.Property.query.count()
r = cl.post('/enquiries/new', data={
    'enquiry_type': 'Landlord — Looking to Let', 'status': 'Open',
    'caller_name': 'Margaret Osei', 'letting_mode': 'new',
    'new_address': '12 Harwood Road, London', 'new_postcode': 'SW6 4QP',
}, follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    assert A.Property.query.count() == props_before, \
        'a second copy of the property was put on the register'
print('   the same address again reuses the property already on file')

# Linking one already on the books makes nothing new.
with A.app.app_context():
    projects_before = A.Project.query.count()
r = cl.post('/enquiries/new', data={
    'enquiry_type': 'Landlord — Looking to Let', 'status': 'Open',
    'caller_name': 'Tomas Lindqvist',
    'letting_mode': 'existing', 'project_id': str(MADE),
    'req_budget_min': '38000', 'req_budget_unit': 'pa',
}, follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    assert A.Project.query.count() == projects_before, \
        'linking an instruction created another one'
    e = A.Enquiry.query.order_by(A.Enquiry.id.desc()).first()
    assert e.project_id == MADE, e.project_id
    assert e.property_id == A.Project.query.get(MADE).property_id
print('   linking one already on the books creates nothing new')

# An empty address makes nothing rather than an instruction with no property.
with A.app.app_context():
    projects_before = A.Project.query.count()
r = cl.post('/enquiries/new', data={
    'enquiry_type': 'Landlord — Looking to Let', 'status': 'Open',
    'caller_name': 'Nobody Yet', 'letting_mode': 'new', 'new_address': '',
}, follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    assert A.Project.query.count() == projects_before, \
        'an instruction was invented from an empty address'
print('   an empty address creates nothing')


# ─── 8. Saving from this form does not clear links it never showed ──────────
# The form no longer offers the contact, organisation and project boxes. An
# enquiry that already has them must keep them when it is saved from here.
with A.app.app_context():
    contact = A.Contact(first_name='Ada', last_name='Okonkwo')
    db.session.add(contact); db.session.commit()
    e = A.Enquiry(subject='Existing', enquiry_type='Tenant — Looking to Rent',
                  status='Open', contact_id=contact.id)
    db.session.add(e); db.session.commit()
    EID, CID = e.id, contact.id

r = cl.post(f'/enquiries/{EID}/edit', data={
    'enquiry_type': 'Tenant — Looking to Rent', 'status': 'Open',
    'caller_name': 'Ada Okonkwo', 'req_area': 'Putney',
}, follow_redirects=True)
assert r.status_code == 200

with A.app.app_context():
    e = A.Enquiry.query.get(EID)
    assert e.contact_id == CID, \
        'saving from a form without the contact box cleared the contact'
    assert e.caller_name == 'Ada Okonkwo'
    assert e.req_area == 'Putney'
print('8. an enquiry keeps a link the form no longer shows')

print('\nENQUIRY FORM: ALL CHECKS PASSED')
