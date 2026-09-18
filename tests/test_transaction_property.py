"""A transaction's property comes from its instruction, not from a copy.

Box two offered two pickers: one for the property and one for the instruction.
Nothing held them together, so a transaction could name one property while the
instruction it was filed against was about another, and the record then said
two different things about the same deal.

The instruction carries the property. A transaction filed against one reads it
from there — the address, the postcode and the floor area are the property's
own, so correcting them on the property record corrects them here. Only a
transaction filed against no instruction keeps a property of its own, because
then there is nowhere else for it to come from.
"""
import os
import re
import sys
import tempfile
from datetime import date

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/trxprop.db'
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

    right = A.Property(address='42 Peterborough Road, London', postcode='SW6 3BN',
                       property_type='Office', size=1636)
    wrong = A.Property(address='8 Farm Lane, London', postcode='SW6 1QJ',
                       property_type='Industrial', size=2436)
    db.session.add_all([right, wrong]); db.session.commit()
    RIGHT, WRONG = right.id, wrong.id

    project = A.Project(name='Peterborough', property_id=right.id, status='Active',
                        instruction_type=A.INSTRUCTION_TO_LET)
    db.session.add(project); db.session.commit()
    PROJECT = project.id

    # Filed against the instruction, but carrying the other property — the
    # disagreement the two pickers allowed.
    t = A.Transaction(property_id=wrong.id, project_id=project.id,
                      transaction_type='Lease', status='Terms Agreed',
                      transaction_date=date.today())
    db.session.add(t); db.session.commit()
    TRX = t.id

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


# ─── 1. What it reads is the instruction's property, not its own copy ───────
with A.app.app_context():
    t = A.Transaction.query.get(TRX)
    assert t.property_id == WRONG, 'the fixture did not set up the disagreement'
    assert t.the_property.id == RIGHT, \
        f'it read {t.the_property.address}, not the instruction\'s property'
print('1. a transaction reads the property from the instruction it is filed against')


# ─── 2. The page shows that property, its postcode and its size ─────────────
r = cl.get(f'/transactions/{TRX}')
assert r.status_code == 200, r.status_code
html = r.get_data(as_text=True)
assert '42 Peterborough Road' in html, 'the instruction\'s property is not shown'
assert 'SW6 3BN' in html, 'the postcode does not come through'
assert '1,636' in html, 'the floor area does not come through'
assert '8 Farm Lane' not in html, 'the property it used to name is still shown'
print('2. the address, postcode and size shown are the instruction\'s')


# ─── 3. Box two holds four things, and Instruction is not one of them ───────
box = html.split('2. Property and project', 1)[1]
box = box.split('<!-- ── 3.', 1)[0]
for wanted in ('Property', 'Project', 'Postcode', 'Size'):
    assert f'>{wanted}<' in box, f'box two does not show {wanted}'
assert 'Instruction' not in box, 'box two still shows Instruction'
print('3. box two holds Property, Project, Postcode and Size, and nothing else')


# ─── 4. With an instruction, the property is not a second question ──────────
assert not re.search(r'<select[^>]*name="property_id"', box), \
    'the property can still be chosen separately from the instruction'
assert re.search(r'<select[^>]*name="project_id"', box), \
    'the instruction can no longer be chosen'
print('4. with an instruction linked, the property is read rather than chosen')


# ─── 5. Saving settles the column, so anything that looks it up agrees ──────
r = cl.post(f'/transactions/{TRX}/save', data={
    'project_id': str(PROJECT), 'status': 'Terms Agreed'}, follow_redirects=True)
assert r.status_code == 200, r.status_code
with A.app.app_context():
    t = A.Transaction.query.get(TRX)
    assert t.property_id == RIGHT, \
        f'the stored property is still {t.property_id}, not the instruction\'s'
print('5. saving writes the instruction\'s property to the transaction')


# ─── 6. Correcting the property corrects the transaction ────────────────────
with A.app.app_context():
    prop = A.Property.query.get(RIGHT)
    prop.postcode = 'SW6 3XX'
    prop.size = 1800
    db.session.commit()

html = cl.get(f'/transactions/{TRX}').get_data(as_text=True)
assert 'SW6 3XX' in html, 'a corrected postcode does not reach the transaction'
assert '1,800' in html, 'a corrected floor area does not reach the transaction'
assert 'SW6 3BN' not in html, 'the old postcode is still being shown'
print('6. correcting the property record corrects what the transaction shows')


# ─── 7. Without an instruction, it keeps a property of its own ──────────────
with A.app.app_context():
    loose = A.Transaction(property_id=WRONG, transaction_type='Sale',
                          status='Terms Agreed', transaction_date=date.today())
    db.session.add(loose); db.session.commit()
    LOOSE = loose.id
    assert loose.the_property.id == WRONG, 'it lost the only property it had'

html = cl.get(f'/transactions/{LOOSE}').get_data(as_text=True)
box = html.split('2. Property and project', 1)[1].split('<!-- ── 3.', 1)[0]
assert re.search(r'<select[^>]*name="property_id"', box), \
    'a transaction with no instruction cannot be given a property'
assert '8 Farm Lane' in html
print('7. with no instruction, the property is still chosen on the transaction')

print('\nTRANSACTION PROPERTY: ALL CHECKS PASSED')
