"""Recording a deal: choose the instruction, and the rest is already known.

The instruction records the property, its size, whose it is and what is being
asked. A transaction against one should take all of it from there — the form
exists for what is new about the deal, not to ask again for what the CRM
already holds.

Three things stopped that working. The floor area was an input named "value",
the transaction's money column, so the size of a unit was being typed into the
field that holds a sale price. Nothing filled it in from the instruction, so
the rate per square foot under it could never work out however much rent was
entered. And the owner was filled in twice — once into the hidden field and
once by the routine that draws the card — and the second gave up when it found
the first had been there, leaving the landlord the project already knew sitting
behind an empty search box.
"""
import os
import re
import sys
import tempfile
from datetime import date

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/trxproj.db'
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
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          email='bc@cowanandrutter.co.uk', active=True,
                          can_earn_fees=True,
                          password_hash=generate_password_hash('pw')))
    db.session.commit()

    prop = A.Property(address='1 Stanley Bridge Studios, London', postcode='SW6 2AA',
                      property_type='Office', size=1000)
    db.session.add(prop); db.session.commit()
    landlord = A.Contact(first_name='Phillipa', last_name='Smith',
                         contact_type='Landlord')
    db.session.add(landlord); db.session.commit()
    project = A.Project(name='Stanley Bridge', property_id=prop.id, status='Active',
                        instruction_type=A.INSTRUCTION_TO_LET,
                        client_contact_id=landlord.id, fee_earner_id=1)
    db.session.add(project); db.session.commit()
    listing = A.Listing(project_id=project.id, property_id=prop.id,
                        set_as_to_let=True, listing_price=30000,
                        listing_price_unit='pa', size=1000)
    db.session.add(listing); db.session.commit()
    PROJECT, PROP, LANDLORD = project.id, prop.id, landlord.id

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


# ─── 1. The instruction hands over everything it holds ──────────────────────
r = cl.get(f'/api/projects/{PROJECT}/transaction-defaults')
assert r.status_code == 200, r.status_code
d = r.get_json()
assert d['property_id'] == PROP
assert d['address'].startswith('1 Stanley Bridge Studios')
assert d['postcode'] == 'SW6 2AA'
assert d['size'] == 1000, d['size']
assert d['owner_role'] == 'Landlord', d['owner_role']
assert 'Phillipa Smith' in (d['owner_name'] or ''), d['owner_name']
assert d['owner_contact_id'] == LANDLORD
assert d['transaction_type'] == 'Letting'
assert d['rent_pa'] == 30000
print('1. the instruction hands over property, postcode, size, landlord and rent')


# ─── 2. The size is not an input, and not the money column ──────────────────
html = cl.get('/transactions/new').get_data(as_text=True)
lease = html.split('LETTING', 1)[1] if 'LETTING' in html else html
assert 'id="lease-size"' in html, 'the size is no longer on the form at all'
assert not re.search(r'<input[^>]*id="lease-size"[^>]*name="value"', html), \
    'the floor area is still typed into the transaction\'s value column'
assert re.search(r'<input[^>]*type="hidden"[^>]*id="lease-size"', html), \
    'the size is still an input somebody has to fill in'
assert 'From the instruction.' in html
print('2. the size is read from the instruction, not typed into the money column')

# And only one field on the form claims the name "value" — the sale price.
names = re.findall(r'<(?:input|select)[^>]*name="value"', html)
assert len(names) == 1, f'{len(names)} fields are called "value"'
print('   only one field on the form is called "value" — the sale price')


# ─── 3. The owner is filled by one path, which also puts the search away ────
# Filling the hidden field as well made the card routine give up.
assert "setIfEmpty('landlord'" not in html, \
    'the landlord is still filled in twice'
assert "setIfEmpty('vendor'" not in html, 'the seller is still filled in twice'
assert 'window.fillOwnerFromProject' in html, 'nothing fills the owner in'
assert 'if (name.value) { return; }' in html, \
    'the guard that made this matter has gone — check the card still draws'
print('3. the owner is filled by one path, which draws the card')


# ─── 4. A letting saves the rent, and no floor area in the money column ─────
r = cl.post('/transactions/new', data={
    'project_id': str(PROJECT),
    'transaction_date': date.today().isoformat(),
    'transaction_type': 'Letting',
    'landlord': 'Phillipa Smith', 'landlord_contact_id': str(LANDLORD),
    'tenant': 'Richard Hockney',
    'rent_pa': '30000', 'size_units': 'sq ft',
    'headline_rate': '30.00', 'headline_rate_unit': 'psf pa',
}, follow_redirects=True)
assert r.status_code == 200, r.status_code

with A.app.app_context():
    t = A.Transaction.query.order_by(A.Transaction.id.desc()).first()
    assert t is not None
    assert t.project_id == PROJECT and t.property_id == PROP
    assert t.transaction_type == 'Letting', t.transaction_type
    assert t.rent_pa == 30000, t.rent_pa
    assert t.landlord == 'Phillipa Smith', t.landlord
    assert t.value in (None, 0), f'the floor area landed in value: {t.value}'
    assert t.headline_rate == 30.0, t.headline_rate
    # The property, and so the size, is read back through the instruction.
    assert t.the_property.id == PROP
    assert t.the_property.size == 1000
    TRX = t.id
print('4. a letting saves the rent and the landlord, and nothing in value')


# ─── 5. The worked example ──────────────────────────────────────────────────
# 1,000 sq ft at £30,000 a year is £30.00 per sq ft. The page works it out;
# this is the arithmetic it is doing, checked against the records it reads.
with A.app.app_context():
    t = A.Transaction.query.get(TRX)
    size = t.the_property.size
    assert round(t.rent_pa / size, 2) == 30.00, round(t.rent_pa / size, 2)
    assert t.headline_rate == round(t.rent_pa / size, 2), \
        'what was recorded is not what the size and the rent come to'
print('5. £30,000 over 1,000 sq ft is £30.00 per sq ft, and that is what is stored')

print('\nTRANSACTION FROM PROJECT: ALL CHECKS PASSED')
