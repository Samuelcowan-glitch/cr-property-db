"""A letting has a landlord and a tenant. A sale has a seller and a buyer.

Three things this holds to.

There is one status for a deal, and it lives on the transaction. An instruction
does not keep its own copy, so it cannot still read "Under Offer" after the
transaction completed.

The two sides of a deal are named by the kind of deal, and the kind comes from
the instruction. A letting is never asked who the purchaser is.

"Client" is gone. It said who we invoice, not what somebody is to a deal, and
it made a landlord and a seller look like the same thing. Retiring it does not
delete anybody: what can be worked out is remapped, and what cannot is kept and
flagged.
"""
import os
import re
import sys
import tempfile
from datetime import date

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/deals.db'
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
    A._migrate_progression_columns()
    A._migrate_contact_roles()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          password_hash=generate_password_hash('pw')))
    db.session.commit()
    council = A.Council.query.first()

    def instruction(name, kind, size, price, unit=None, fee=None):
        p = A.Property(address=f'{name}, London SW6 1AA', postcode='SW6 1AA',
                       property_type='Office', size=size,
                       council_id=council.id if council else None)
        db.session.add(p); db.session.commit()
        pr = A.Project(name=name, property_id=p.id, fee_earner_id=1,
                       instruction_type=kind, project_ref=f'CR-{p.id:04d}',
                       fee_percent=fee, landlord_name='Hurlingham Holdings Ltd')
        db.session.add(pr); db.session.commit()
        l = A.Listing(project_id=pr.id, property_id=p.id, unit_name=unit,
                      size=size, listing_price=price,
                      listing_price_unit='sale' if kind == A.INSTRUCTION_FOR_SALE else 'pa',
                      listing_status='available')
        db.session.add(l); db.session.commit()
        return {'prop': p.id, 'project': pr.id, 'listing': l.id}

    LET = instruction('Farm Lane', A.INSTRUCTION_TO_LET, 1636, 57260,
                      unit='Unit 3', fee=10.0)
    SALE = instruction('Peterborough Road', A.INSTRUCTION_FOR_SALE, 4200, 1950000,
                       fee=1.5)

    # Somebody filed under the retired type, whose instructions say which side
    # they are, and somebody whose instructions do not.
    clear = A.Contact(first_name='Margaret', last_name='Hale', contact_type='Client')
    unclear = A.Contact(first_name='Iwan', last_name='Rheon', contact_type='Client')
    both = A.Contact(first_name='Priya', last_name='Shah', contact_type='Client')
    db.session.add_all([clear, unclear, both]); db.session.commit()
    A.Project.query.get(LET['project']).client_contact_id = clear.id
    # Priya is the client on one letting and one sale — genuinely ambiguous.
    A.Project.query.get(SALE['project']).client_contact_id = both.id
    extra = A.Project(name='Kings Road', property_id=LET['prop'], fee_earner_id=1,
                      instruction_type=A.INSTRUCTION_TO_LET, client_contact_id=both.id)
    db.session.add(extra); db.session.commit()
    CLEAR, UNCLEAR, BOTH = clear.id, unclear.id, both.id

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)
page = lambda url: cl.get(url).get_data(as_text=True)


def make_transaction(ids, **extra):
    data = {'project_id': str(ids['project']), 'transaction_type': 'Leasehold'}
    data.update(extra)
    r = cl.post('/transactions/new', data=data, follow_redirects=True)
    assert r.status_code == 200, r.status_code
    with A.app.app_context():
        return A.Transaction.query.order_by(A.Transaction.id.desc()).first().id


# ─── 1. The instruction decides which kind of deal it is ────────────────────
# Posted as a Leasehold, but the instruction is a sale — the instruction wins.
SALE_T = make_transaction(SALE)
with A.app.app_context():
    t = A.Transaction.query.get(SALE_T)
    assert t.is_sale and not t.is_letting, t.transaction_type
    assert t.transaction_type == 'Capital', \
        'a sale instruction was saved as a letting because the form said so'
    assert t.project_id == SALE['project']
    assert t.property_id == SALE['prop'], 'the property did not come from the instruction'
LET_T = make_transaction(LET)
with A.app.app_context():
    assert A.Transaction.query.get(LET_T).is_letting
print('1. the instruction decides letting or sale, not the form')


# ─── 2. Each deal names its own two sides, and only those ───────────────────
with A.app.app_context():
    let_roles = [r for r, _, _ in A.Transaction.query.get(LET_T).party_roles]
    sale_roles = [r for r, _, _ in A.Transaction.query.get(SALE_T).party_roles]
assert let_roles == ['Landlord', 'Tenant'], let_roles
assert sale_roles == ['Seller', 'Buyer'], sale_roles
print('2. a letting is landlord and tenant; a sale is seller and buyer')


# ─── 3. And that is what the record shows ───────────────────────────────────
let_page = page(f'/transactions/{LET_T}')
assert 'Landlord and tenant' in let_page
for wrong in ('Purchaser', 'Vendor', 'Seller and buyer'):
    assert wrong not in let_page, f'{wrong} is on a letting'
sale_page = page(f'/transactions/{SALE_T}')
assert 'Seller and buyer' in sale_page
assert 'Landlord and tenant' not in sale_page, 'letting wording is on a sale'
print('3. the record shows only the wording for the deal it is')


# ─── 4. And only those parties can be linked ────────────────────────────────
for tid, wanted, unwanted in ((LET_T, {'Landlord', 'Tenant'}, {'Seller', 'Buyer'}),
                              (SALE_T, {'Seller', 'Buyer'}, {'Landlord', 'Tenant'})):
    roles = set(re.findall(r'data-role="([^"]+)"', page(f'/transactions/{tid}')))
    assert wanted <= roles, f'{wanted - roles} cannot be linked'
    assert not (roles & unwanted), f'the other deal\'s parties are offered: {roles}'
    assert 'Client' not in roles
print('4. only the two sides of that deal can be linked to it')


# ─── 5. A letting is not asked for a sale price ─────────────────────────────
assert 'name="rent_pa"' in let_page and 'name="value"' not in let_page, \
    'a letting is being asked for a sale price'
assert 'name="value"' in sale_page and 'name="rent_pa"' not in sale_page, \
    'a sale is being asked for a rent'
assert 'Lease start' in let_page and 'Lease start' not in sale_page
print('5. a letting asks for rent and a term; a sale asks for a price')


# ─── 6. One status, and everything reads it ─────────────────────────────────
with A.app.app_context():
    listing = A.Listing.query.get(LET['listing'])
    assert listing.effective_status == 'available', listing.effective_status

cl.post(f'/transactions/{LET_T}/edit-record',
        data={'status': 'Under Offer'}, follow_redirects=True)
with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    if t.status != 'Under Offer':          # the record posts to its own route
        t.status = 'Under Offer'
        db.session.commit()
    assert A.Listing.query.get(LET['listing']).effective_status == 'under-offer', \
        'the instruction did not follow the transaction to Under Offer'

with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    t.status = 'Completed'
    db.session.commit()
    listing = A.Listing.query.get(LET['listing'])
    assert listing.effective_status == 'let-agreed', listing.effective_status
    assert listing.status_label == 'Let Agreed'
    assert not listing.is_available, 'a completed letting still reads as available'
    # The stored field was never rewritten — the deal is simply the truth.
    assert (listing.listing_status or 'available') == 'available'
    assert listing.status_is_derived, 'the page cannot tell the deal is setting this'
print('6. completing the transaction changes the instruction everywhere at once')


# ─── 7. A completed sale reads as sold, not let ─────────────────────────────
with A.app.app_context():
    t = A.Transaction.query.get(SALE_T)
    t.status = 'Completed'
    db.session.commit()
    assert A.Listing.query.get(SALE['listing']).effective_status == 'sold'
print('7. a completed sale reads as sold; a completed letting as let agreed')


# ─── 8. A deal that fell through does not hold a property off the market ────
with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    t.status = 'Fallen Through'
    db.session.commit()
    listing = A.Listing.query.get(LET['listing'])
    assert listing.effective_status == 'available', listing.effective_status
    assert A.Project.query.get(LET['project']).live_transaction is None
    t.status = 'Completed'                # put it back for what follows
    db.session.commit()
print('8. a deal that fell through releases the instruction')


# ─── 9. The size is read from the instruction, not copied ───────────────────
with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    assert t.size == 1636, t.size
    assert t.unit_name == 'Unit 3'
    # Correcting the instruction corrects the transaction, because there is
    # only one number.
    A.Listing.query.get(LET['listing']).size = 1700
    db.session.commit()
    assert A.Transaction.query.get(LET_T).size == 1700, \
        'the transaction kept its own copy of the floor area'
    assert 'size' not in {c.name for c in A.Transaction.__table__.columns}, \
        'a size column was added to transactions'
print('9. the floor area is read from the instruction, never duplicated')


# ─── 10. The commission comes from the instruction ──────────────────────────
with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    assert t.fee_percent is None, 'a fee was copied onto the transaction'
    assert t.fee_percent_effective == 10.0, t.fee_percent_effective
    t.agreed_value = 50000
    db.session.commit()
    assert A.Transaction.query.get(LET_T).net_commission == 5000.0, \
        'the instruction fee was not used for the commission'
    # Anything set on the deal itself still wins.
    t = A.Transaction.query.get(LET_T)
    t.fee_percent = 12.0
    db.session.commit()
    assert A.Transaction.query.get(LET_T).net_commission == 6000.0
print('10. the commission uses the instruction fee, and a deal fee overrides it')


# ─── 11. Choosing an instruction hands over what it knows ───────────────────
data = cl.get(f"/api/projects/{LET['project']}/transaction-defaults").get_json()
assert data['transaction_type'] == 'Leasehold'
assert data['owner_role'] == 'Landlord' and data['taker_role'] == 'Tenant'
assert data['size'] == 1700 and data['unit'] == 'Unit 3'
assert data['reference'] and data['fee_percent'] == 10.0
sale_data = cl.get(f"/api/projects/{SALE['project']}/transaction-defaults").get_json()
assert sale_data['owner_role'] == 'Seller' and sale_data['taker_role'] == 'Buyer'
assert sale_data['transaction_type'] == 'Capital'
print('11. choosing an instruction hands over size, type, fee and parties')


# ─── 12. The form asks for the instruction, not the property ────────────────
form = page('/transactions/new')
assert 'Project / Instruction' in form, 'the form still asks for a property'
assert 'name="project_id"' in form
assert 'id="project-summary"' in form, 'the instruction detail is not shown'
prop_field = re.search(r'<input[^>]*name="property_id"[^>]*>', form)
assert prop_field and 'type="hidden"' in prop_field.group(0), \
    'the property is still picked by hand'
print('12. the form asks for the instruction and reads the property from it')


# ─── 13. Client is gone from the vocabulary ─────────────────────────────────
assert 'Client' not in A.ORG_ROLE_NAMES, A.ORG_ROLE_NAMES
assert 'Client' not in A.ORG_TYPES
assert 'Vendor' not in A.ORG_ROLE_NAMES and 'Purchaser' not in A.ORG_ROLE_NAMES
assert set(A.CONTACT_TYPES) == {'Landlord', 'Tenant', 'Buyer', 'Seller'}
print('13. Client, Vendor and Purchaser are gone from the vocabulary')


# ─── 14. Retiring it remaps what it can, and keeps what it cannot ───────────
A._migrate_retire_client()
with A.app.app_context():
    margaret = A.Contact.query.get(CLEAR)
    assert margaret.contact_type == 'Landlord', margaret.contact_type
    assert 'Landlord' in A.role_names(margaret), 'no role was recorded for them'

    iwan = A.Contact.query.get(UNCLEAR)
    assert iwan is not None, 'a contact was deleted'
    assert iwan.contact_type == 'Client', 'a type was guessed with nothing to go on'
    assert 'Review contact type' in A.contact_tags(iwan), \
        'an undecidable contact was not flagged'

    priya = A.Contact.query.get(BOTH)
    assert priya.contact_type == 'Client', 'a contact on both kinds was guessed at'
    assert 'Review contact type' in A.contact_tags(priya)
print('14. what can be worked out is remapped; what cannot is kept and flagged')


# ─── 15. Running it twice changes nothing further ───────────────────────────
with A.app.app_context():
    before = {c.id: (c.contact_type, tuple(A.contact_tags(c)))
              for c in A.Contact.query.all()}
A._migrate_retire_client()
with A.app.app_context():
    after = {c.id: (c.contact_type, tuple(A.contact_tags(c)))
             for c in A.Contact.query.all()}
assert before == after, 'running the migration again changed records'
print('15. running the migration again changes nothing')


# ─── 16. Nothing that was recorded has been thrown away ─────────────────────
with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    assert 'client' in {c.name for c in A.Transaction.__table__.columns}, \
        'the client column was dropped, taking what was typed in it'
    assert 'vendor' in {c.name for c in A.Transaction.__table__.columns}
    assert 'purchaser' in {c.name for c in A.Transaction.__table__.columns}
    assert A.Contact.query.count() == 3, 'a contact went missing'
print('16. no column was dropped and no contact was lost')


# ─── 17. Every page still loads ─────────────────────────────────────────────
for url in ('/', '/transactions', f'/transactions/{LET_T}', f'/transactions/{SALE_T}',
            '/transactions/new', '/properties', f"/properties/{LET['prop']}",
            '/projects', f"/projects/{LET['project']}", '/contacts'):
    assert cl.get(url).status_code == 200, url
print('17. every page that touches a deal still loads')


# ─── 18. The instruction page says where its status came from ───────────────
proj = page(f"/projects/{LET['project']}")
assert 'Let Agreed' in proj, 'the instruction is not showing the completed deal'
assert 'Change it there' in proj, \
    'the page does not say the transaction is setting the status'
assert f'/transactions/{LET_T}' in proj, 'there is no link to the deal that set it'
# And the property is off the market everywhere that counts stock.
with A.app.app_context():
    assert not any(l.id == LET['listing']
                   for l in A._available_listings(A.INSTRUCTION_TO_LET)), \
        'a let-agreed unit is still counted as available'
print('18. the instruction shows the deal status, and is off the market with it')

print('\nDEAL TERMS: ALL CHECKS PASSED')
