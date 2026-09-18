"""Service charge and EPC: one field each, read by everything.

The Commercial Lease Detail section on an instruction held ten fields. The rent
per square foot, the qualifier, the basis, the lease length and the lease type
came off it: the rent is on the listing above, and lease terms belong to an
offer or a transaction, where they are actually negotiated. What is left is
the service charge and the EPC, because those are what somebody looking at the
property on the website wants to know.

Neither is copied anywhere. Both are the listing's own fields, edited on the
instruction, printed on the brochure and sent to the website from the same
place — and the words are written once, so the three cannot phrase the same
figure differently.
"""
import json
import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/scepc.db'
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

    prop = A.Property(address='42 Peterborough Road, London', postcode='SW6 3BN',
                      property_type='Office', size=1636, lat=51.47, lng=-0.19)
    db.session.add(prop); db.session.commit()
    project = A.Project(name='Peterborough', property_id=prop.id, status='Active',
                        instruction_type=A.INSTRUCTION_TO_LET, fee_earner_id=1)
    db.session.add(project); db.session.commit()
    listing = A.Listing(project_id=project.id, property_id=prop.id,
                        set_as_to_let=True, listing_price=57260,
                        listing_price_unit='pa', website_listed=True,
                        website_category='commercial',
                        service_charge=4.5, epc_band='B',
                        strapline='GROUND FLOOR OFFICE')
    db.session.add(listing); db.session.commit()
    PROJECT, LISTING = project.id, listing.id

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


# ─── 1. The section is down to the two fields ───────────────────────────────
html = cl.get(f'/projects/{PROJECT}').get_data(as_text=True)
for gone in ('rent_from', 'rent_to', 'rent_qualifier', 'rent_inclusive',
             'lease_length_years', 'lease_length_months', 'lease_type'):
    assert f'name="{gone}"' not in html, f'the instruction still asks for {gone}'
print('1. rent psf, qualifier, basis, lease length and lease type are gone')

for stays in ('service_charge', 'epc_band'):
    assert f'name="{stays}"' in html, f'{stays} was taken off too'
print('2. the service charge and the EPC remain')


# ─── 3. Editing them on the instruction writes the listing's own fields ─────
# Not a copy: the same row the brochure and the website read.
r = cl.post(f'/projects/{PROJECT}/edit', data={
    'name': 'Peterborough', 'status': 'Active',
    'instruction_type': A.INSTRUCTION_TO_LET,
    '_listing_switches': '1', 'website_listed': '1',
    'service_charge': '6.25', 'epc_band': 'C'}, follow_redirects=True)
assert r.status_code == 200, r.status_code
with A.app.app_context():
    l = A.Listing.query.get(LISTING)
    assert l.service_charge == 6.25, l.service_charge
    assert l.epc_band == 'C', l.epc_band
print('3. saving the instruction writes the listing\'s own service charge and EPC')


# ─── 4. The website is sent both ────────────────────────────────────────────
r = cl.get('/api/listings')
assert r.status_code == 200, r.status_code
rows = json.loads(r.get_data(as_text=True))
assert rows, 'the website is sent no listings at all'
row = rows[0]
assert row.get('serviceCharge') == '£6.25 per sq ft', row.get('serviceCharge')
assert row.get('epc') == 'C', row.get('epc')
print(f"4. the website is sent serviceCharge={row['serviceCharge']!r} "
      f"and epc={row['epc']!r}")


# ─── 5. Worded once, so nothing phrases it differently ──────────────────────
with A.app.app_context():
    l = A.Listing.query.get(LISTING)
    said = A._listing_service_charge(l)
    assert said == '£6.25 per sq ft', said

    # Marked not applicable, it reads as included — on the website and on the
    # brochure alike, because both ask the same function.
    l.service_charge_na = True
    db.session.commit()
    assert A._listing_service_charge(l) == 'Included'

    # A comment typed by hand wins: somebody has said it better than a number.
    l.service_charge_comment = 'Approximately £6.25 per sq ft, reviewed annually'
    db.session.commit()
    assert A._listing_service_charge(l).startswith('Approximately')

    l.service_charge_comment = None
    l.service_charge_na = False
    db.session.commit()
print('5. the wording is written once — a figure, "Included", or what was typed')

# The brochure asks the same question of the same field.
with A.app.app_context():
    project = A.Project.query.get(PROJECT)
    data = A.particulars_data(project)
    assert data['service_charge'] == '£6.25 per sq ft', data['service_charge']
    assert data['epc'] == 'C', data['epc']
print('6. the brochure prints the same words from the same field')


# ─── 7. Nothing is sent where nothing is recorded ───────────────────────────
with A.app.app_context():
    l = A.Listing.query.get(LISTING)
    l.service_charge = None
    l.epc_band = None
    db.session.commit()
rows = json.loads(cl.get('/api/listings').get_data(as_text=True))
assert rows[0].get('serviceCharge') is None, rows[0].get('serviceCharge')
assert rows[0].get('epc') is None, rows[0].get('epc')
print('7. a listing with neither recorded sends neither, rather than a blank')

print('\nSERVICE CHARGE AND EPC: ALL CHECKS PASSED')
