"""Offers on an instruction, and what becomes of an accepted one.

An offer belongs to the instruction and to the applicant who made it. The
property, the landlord and the client are not copied on to it — they are on
the instruction, and the offer reads them from there. That is what makes an
accepted offer worth a button: everything a transaction needs is already
recorded, so nothing is typed a second time, and the heads of terms that come
off it cannot disagree with the file they came from.
"""
import os
import re
import sys
import tempfile
from datetime import date, timedelta

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/offers.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
import pymupdf
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
                      property_type='Office', size=1636, measurement_type='NIA')
    db.session.add(prop); db.session.commit()

    landlord = A.Contact(first_name='Jonathan', last_name='Baker',
                         contact_type='Landlord')
    tenant = A.Contact(first_name='Richard', last_name='Hockney',
                       contact_type='Tenant')
    db.session.add_all([landlord, tenant]); db.session.commit()
    LANDLORD, TENANT = landlord.id, tenant.id

    letting = A.Project(name='Stanley Bridge', property_id=prop.id, status='Active',
                        instruction_type=A.INSTRUCTION_TO_LET,
                        client_contact_id=landlord.id, fee_earner_id=1)
    sale = A.Project(name='Kings Road', property_id=prop.id, status='Active',
                     instruction_type=A.INSTRUCTION_FOR_SALE,
                     client_contact_id=landlord.id, fee_earner_id=1)
    db.session.add_all([letting, sale]); db.session.commit()
    LETTING, SALE = letting.id, sale.id

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


# ─── 1. An offer is recorded against the instruction ────────────────────────
r = cl.post(f'/projects/{LETTING}/offers/add', data={
    'contact_id': str(TENANT), 'amount': '57260', 'amount_unit': 'pa'},
    follow_redirects=True)
assert r.status_code == 200, r.status_code
with A.app.app_context():
    offer = A.Offer.query.order_by(A.Offer.id.desc()).first()
    assert offer is not None and offer.project_id == LETTING
    assert offer.contact_id == TENANT
    assert offer.status == 'Submitted', offer.status
    assert offer.received_date == date.today()
    OFFER = offer.id
print('1. an offer is recorded against the instruction and the applicant')

# Every status the agency uses is offered.
for wanted in ('Submitted', 'Negotiating', 'Accepted', 'Rejected', 'Withdrawn'):
    assert wanted in A.OFFER_STATUSES, f'{wanted} is not a status'
print('2. Submitted, Negotiating, Accepted, Rejected and Withdrawn')


# ─── 3. The terms are filled in on it ───────────────────────────────────────
START = date.today() + timedelta(days=30)
r = cl.post(f'/offers/{OFFER}/save', data={
    'contact_id': str(TENANT), 'amount': '57260', 'amount_unit': 'pa',
    'status': 'Negotiating', 'lease_years': '10',
    'break_clause': "Year 5, 6 months' notice",
    'start_date': START.isoformat(), 'rent_free_months': '6',
    'deposit': '28630', 'conditions': 'Subject to board approval and references.',
    'received_date': date.today().isoformat()}, follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    offer = A.Offer.query.get(OFFER)
    assert offer.status == 'Negotiating'
    assert offer.lease_years == 10 and offer.rent_free_months == 6
    assert offer.deposit == 28630
    assert offer.rent_pa == 57260
    assert offer.lease_end is not None and offer.lease_end.year == START.year + 10
    assert offer.display_amount == '£57,260 per annum', offer.display_amount
print('3. rent, term, break, start, rent free, deposit and conditions are held')

# Entered per month, it still reads as a yearly rent where that is what counts.
with A.app.app_context():
    monthly = A.Offer(project_id=LETTING, contact_id=TENANT, amount=1000,
                      amount_unit='pcm', status='Submitted')
    db.session.add(monthly); db.session.commit()
    assert monthly.rent_pa == 12000, monthly.rent_pa
    db.session.delete(monthly); db.session.commit()
print('   a monthly figure still answers as a yearly rent')


# ─── 4. Only an accepted offer becomes a transaction ────────────────────────
with A.app.app_context():
    before = A.Transaction.query.count()
r = cl.post(f'/offers/{OFFER}/transaction', follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    assert A.Transaction.query.count() == before, \
        'a transaction was made from an offer nobody had accepted'
print('4. an offer still being negotiated does not become a transaction')


# ─── 5. An accepted one does, without anything being typed again ────────────
cl.post(f'/offers/{OFFER}/save', data={
    'contact_id': str(TENANT), 'amount': '57260', 'amount_unit': 'pa',
    'status': 'Accepted', 'lease_years': '10',
    'break_clause': "Year 5, 6 months' notice",
    'start_date': START.isoformat(), 'rent_free_months': '6',
    'deposit': '28630', 'conditions': 'Subject to board approval and references.',
    'received_date': date.today().isoformat()}, follow_redirects=True)
r = cl.post(f'/offers/{OFFER}/transaction', follow_redirects=True)
assert r.status_code == 200

with A.app.app_context():
    offer = A.Offer.query.get(OFFER)
    assert offer.transaction_id, 'the offer was not joined to a transaction'
    t = A.Transaction.query.get(offer.transaction_id)
    assert t.property_id == A.Project.query.get(LETTING).property_id
    assert t.project_id == LETTING
    assert t.transaction_type == 'Leasehold', t.transaction_type
    assert t.landlord == 'Jonathan Baker', t.landlord
    assert t.tenant == 'Richard Hockney', t.tenant
    assert t.rent_pa == 57260
    assert t.lease_start == START
    assert t.break_clause == "Year 5, 6 months' notice"
    assert abs((t.incentive_years or 0) - 0.5) < 0.01, t.incentive_years
    assert t.status == 'Terms Agreed'
    assert t.fee_earner_id == 1
    assert t.reference, 'the transaction has no reference'
    TRX = t.id
print('5. an accepted offer becomes a transaction carrying every term')

# Pressing it twice does not make a second one.
with A.app.app_context():
    count = A.Transaction.query.count()
r = cl.post(f'/offers/{OFFER}/transaction', follow_redirects=True)
with A.app.app_context():
    assert A.Transaction.query.count() == count, 'a second transaction was made'
print('   and pressing it again does not make a second')


# ─── 6. The heads of terms are drawn from the records ───────────────────────
with A.app.app_context():
    trx = A.Transaction.query.get(TRX)
    trx.client_solicitor_firm = 'Farrer & Co'
    trx.client_solicitor = 'A. Fitzwilliam'
    trx.other_solicitor_firm = 'Mishcon de Reya'
    trx.other_solicitor = 'P. Nwachukwu'
    db.session.commit()

r = cl.get(f'/offers/{OFFER}/heads-of-terms')
assert r.status_code == 200, r.status_code
assert r.headers['Content-Type'].startswith('application/pdf'), r.headers['Content-Type']
assert 'Heads-of-Terms' in r.headers.get('Content-Disposition', '')

doc = pymupdf.open(stream=r.get_data(), filetype='pdf')
assert len(doc) == 1, f'{len(doc)} pages'
text = doc[0].get_text()

for wanted in ('HEADS OF TERMS', 'Subject to contract',
               '1 Stanley Bridge Studios', 'SW6 2AA',
               'Jonathan Baker', 'Richard Hockney',
               '57,260', '10 years', "Year 5, 6 months' notice",
               '6 months', '28,630',
               'Farrer & Co', 'Mishcon de Reya',
               'Benjamin Cowan'):
    assert wanted in text, f'the heads of terms do not say {wanted!r}'
print('6. the heads of terms name the property, both sides, the terms and '
      'the solicitors')
print()
for line in [l for l in text.splitlines() if l.strip()][:22]:
    print('   |', line)
print()

# Nothing was typed for it: every figure on the page came off a record.
assert 'None' not in text and '—' not in text, \
    'a term with nothing against it was printed rather than left out'
print('7. a term with nothing recorded is left off, not printed blank')


# ─── 8. A sale offer is the same record, read the other way ─────────────────
r = cl.post(f'/projects/{SALE}/offers/add', data={
    'contact_id': str(TENANT), 'amount': '850000'}, follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    sale_offer = A.Offer.query.filter_by(project_id=SALE).first()
    assert not sale_offer.is_letting
    assert sale_offer.display_amount == '£850,000', sale_offer.display_amount
    assert sale_offer.rent_pa is None
    sale_offer.status = 'Accepted'
    db.session.commit()
    SALE_OFFER = sale_offer.id

r = cl.post(f'/offers/{SALE_OFFER}/transaction', follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    sale_offer = A.Offer.query.get(SALE_OFFER)
    t = A.Transaction.query.get(sale_offer.transaction_id)
    assert t.transaction_type == 'Capital', t.transaction_type
    assert t.vendor == 'Jonathan Baker' and t.purchaser == 'Richard Hockney'
    assert t.value == 850000 and t.rent_pa is None
print('8. a sale offer makes a capital transaction with a seller and a buyer')


# ─── 9. The instruction page shows them ─────────────────────────────────────
html = cl.get(f'/projects/{LETTING}').get_data(as_text=True)
assert 'Offers' in html
assert 'Generate heads of terms' in html, 'no button to draw them up'
assert 'On transaction' in html, 'the offer does not say it is on a transaction'
print('9. the instruction lists its offers and offers the two buttons')

print('\nOFFERS: ALL CHECKS PASSED')
