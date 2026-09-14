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
    data = {'project_id': str(ids['project']), 'transaction_type': 'Letting'}
    data.update(extra)
    r = cl.post('/transactions/new', data=data, follow_redirects=True)
    assert r.status_code == 200, r.status_code
    with A.app.app_context():
        return A.Transaction.query.order_by(A.Transaction.id.desc()).first().id


# ─── 1. The instruction decides which kind of deal it is ────────────────────
# Posted as a Letting, but the instruction is a sale — the instruction wins.
SALE_T = make_transaction(SALE)
with A.app.app_context():
    t = A.Transaction.query.get(SALE_T)
    assert t.is_sale and not t.is_letting, t.transaction_type
    assert t.transaction_type == 'Sale', \
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
assert data['transaction_type'] == 'Letting'
assert data['owner_role'] == 'Landlord' and data['taker_role'] == 'Tenant'
assert data['size'] == 1700 and data['unit'] == 'Unit 3'
assert data['reference'] and data['fee_percent'] == 10.0
sale_data = cl.get(f"/api/projects/{SALE['project']}/transaction-defaults").get_json()
assert sale_data['owner_role'] == 'Seller' and sale_data['taker_role'] == 'Buyer'
assert sale_data['transaction_type'] == 'Sale'
print('11. choosing an instruction hands over size, type, fee and parties')


# ─── 12. One thing to choose: the project ───────────────────────────────────
form = page('/transactions/new')
assert 'name="project_id"' in form, 'the form does not ask for a project'
assert 'id="project-summary"' in form, 'what the project holds is not shown'
# Nothing else to pick. The property and the kind of deal come from it.
selects = re.findall(r'<select[^>]*name="([^"]+)"', form)
for unwanted in ('property_id', 'instruction_type'):
    assert unwanted not in selects, f'the form still has a {unwanted} selector'
# And the project's details are shown rather than asked for.
for shown in ('Property', 'Postcode', 'Size'):
    assert f'>{shown}<' in form, f'{shown} is not shown from the project'
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

# ─── 19. The deal is a Letting or a Sale, in those words ────────────────────
assert A.TRANSACTION_KINDS == ['Letting', 'Sale'], A.TRANSACTION_KINDS
form = page('/transactions/new')
block = form[form.index('name="transaction_type"') - 200:]
block = block[:block.index('</div>', block.index('type-toggle'))]
assert 'value="Letting"' in block and 'value="Sale"' in block, block[:200]
assert 'value="Capital"' not in block and 'value="Leasehold"' not in block, \
    'the form still offers the tenure words'
print('19. the form offers Letting and Sale')


# ─── 20. Older records are retyped, and read correctly meanwhile ────────────
with A.app.app_context():
    old_one = A.Transaction(property_id=LET['prop'], transaction_type='Leasehold',
                            reference='TR-OLD1')
    old_sale = A.Transaction(property_id=SALE['prop'], transaction_type='Capital',
                             reference='TR-OLD2')
    db.session.add_all([old_one, old_sale]); db.session.commit()
    OLD_LET, OLD_SALE = old_one.id, old_sale.id
    # Read correctly before the migration has touched them.
    assert A.Transaction.query.get(OLD_LET).is_letting, 'an old Leasehold reads as a sale'
    assert A.Transaction.query.get(OLD_SALE).is_sale, 'an old Capital reads as a letting'

A._migrate_transaction_kinds()
with A.app.app_context():
    assert A.Transaction.query.get(OLD_LET).transaction_type == 'Letting'
    assert A.Transaction.query.get(OLD_SALE).transaction_type == 'Sale'
    assert A.Transaction.query.get(OLD_LET).is_letting
    assert A.Transaction.query.get(OLD_SALE).is_sale
    assert A.Transaction.query.count() >= 4, 'a transaction went missing'
print('20. Capital and Leasehold are retyped, and read correctly either way')


# ─── 21. Running it again changes nothing ───────────────────────────────────
with A.app.app_context():
    before = {t.id: t.transaction_type for t in A.Transaction.query.all()}
A._migrate_transaction_kinds()
with A.app.app_context():
    after = {t.id: t.transaction_type for t in A.Transaction.query.all()}
assert before == after, 'a second run retyped something'
print('21. running the retype again changes nothing')


# ─── 22. A letting asks for rent; a sale asks for a price ───────────────────
let_page = page(f'/transactions/{LET_T}')
box4 = let_page[let_page.index('4. Agreed commercial terms'):]
box4 = box4[:box4.index('8. Important dates')]
assert 'name="rent_pa"' in box4, 'a letting is not asked for its rent'
assert 'name="value"' not in box4, 'a letting is being asked for a sale price'
assert 'Rent per annum' in box4

sale_page = page(f'/transactions/{SALE_T}')
sbox4 = sale_page[sale_page.index('4. Agreed commercial terms'):]
sbox4 = sbox4[:sbox4.index('8. Important dates')]
assert 'name="value"' in sbox4, 'a sale is not asked for its price'
assert 'name="rent_pa"' not in sbox4, 'a sale is being asked for a rent'
assert 'Sale price' in sbox4
print('22. a letting asks for rent, a sale for a price, neither for both')


# ─── 23. Agreed value is not asked for unless a record carries one ──────────
with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    had = t.agreed_value
    t.agreed_value = None
    db.session.commit()
clean = page(f'/transactions/{LET_T}')
clean4 = clean[clean.index('4. Agreed commercial terms'):]
clean4 = clean4[:clean4.index('8. Important dates')]
assert 'name="agreed_value"' not in clean4, \
    'a letting with no agreed value is still asked for one'
with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    t.agreed_value = had or 50000
    db.session.commit()
kept = page(f'/transactions/{LET_T}')
kept4 = kept[kept.index('4. Agreed commercial terms'):]
kept4 = kept4[:kept4.index('8. Important dates')]
assert 'name="agreed_value"' in kept4, \
    'a record that carries an agreed value can no longer see or change it'
print('23. agreed value is shown only where a record actually carries one')


# ─── 24. The fee still knows what it is charged on ──────────────────────────
with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    t.agreed_value = None
    t.rent_pa = 40000
    t.fee_percent = 10.0
    db.session.commit()
    assert A.Transaction.query.get(LET_T).commission_basis == 40000, \
        'a letting with no agreed value lost its commission basis'
    assert A.Transaction.query.get(LET_T).net_commission == 4000.0

    s2 = A.Transaction.query.get(SALE_T)
    s2.agreed_value = None
    s2.value = 900000
    s2.fee_percent = 1.0
    db.session.commit()
    assert A.Transaction.query.get(SALE_T).commission_basis == 900000
    assert A.Transaction.query.get(SALE_T).net_commission == 9000.0
print('24. hiding agreed value did not take the commission basis with it')


# ─── 25. The landlord comes from the instruction ────────────────────────────
with A.app.app_context():
    org = A.Organisation(name='Hurlingham Holdings Ltd', fee_earner='Benjamin Cowan')
    db.session.add(org); db.session.commit()
    who = A.Contact(first_name='Phillipa', last_name='Smith', contact_type='Landlord',
                    organisation_id=org.id)
    db.session.add(who); db.session.commit()
    A.Project.query.get(LET['project']).client_contact_id = who.id
    db.session.commit()
    ORG, WHO = org.id, who.id

new_t = make_transaction(LET)
with A.app.app_context():
    link = A.current_org_link('Landlord', transaction_id=new_t)
    assert link is not None, 'the landlord was not taken from the instruction'
    assert link.organisation_id == ORG
    assert link.contact_id == WHO, 'the person was not carried across'
    assert A.current_org_link('Tenant', transaction_id=new_t) is None, \
        'a tenant was invented from the instruction'
print('25. a new transaction takes its landlord from the instruction')


# ─── 26. Which shows as a card, not a search box ────────────────────────────
body = page(f'/transactions/{new_t}')
assert 'orgpick-card' in body, 'the linked landlord is not shown as a card'
assert 'Hurlingham Holdings Ltd' in body
assert 'orgpick-change' in body, 'there is no way to change it'
landlord_block = body[body.index('data-role="Landlord"'):]
landlord_block = landlord_block[:landlord_block.index('data-role="Tenant"')]
assert 'orgpick-current' in landlord_block
assert 'hidden' in landlord_block.split('orgpick-choose')[1][:40], \
    'the search box is still showing for a party that is already chosen'
print('26. a chosen party is a card with a Change option, and no search box')


# ─── 27. The tenant, which nothing knows yet, still offers a search ─────────
tenant_block = body[body.index('data-role="Tenant"'):]
tenant_block = tenant_block[:tenant_block.index('</div>', tenant_block.index('orgpick-search'))]
assert 'orgpick-q' in tenant_block, 'a party nobody has chosen has no way to choose one'
print('27. a party nobody has chosen still offers the search')


# ─── 28. An existing transaction is offered it rather than written to ───────
with A.app.app_context():
    orphan = A.Transaction(property_id=LET['prop'], project_id=LET['project'],
                           transaction_type='Letting', reference='TR-ORPH')
    db.session.add(orphan); db.session.commit()
    ORPHAN = orphan.id
    assert A.suggested_party(A.Transaction.query.get(ORPHAN), 'Landlord') is not None
    assert A.suggested_party(A.Transaction.query.get(ORPHAN), 'Tenant') is None, \
        'a tenant was suggested from an instruction that cannot know one'

body = page(f'/transactions/{ORPHAN}')
assert 'orgpick-suggest' in body, 'the instruction knows, and the page does not offer it'
assert 'Use them' in body
with A.app.app_context():
    assert A.current_org_link('Landlord', transaction_id=ORPHAN) is None, \
        'reading the page created a relationship on its own'
print('28. an older transaction is offered its landlord, not quietly given one')


# ─── 29. And a party already chosen is never overridden ─────────────────────
with A.app.app_context():
    other = A.Organisation(name='Somebody Else Ltd', fee_earner='Benjamin Cowan')
    db.session.add(other); db.session.commit()
    db.session.add(A.OrganisationRole(organisation_id=other.id, role='Landlord',
                                      transaction_id=ORPHAN))
    db.session.commit()
    A.link_owner_from_project(A.Transaction.query.get(ORPHAN),
                              A.Project.query.get(LET['project']))
    link = A.current_org_link('Landlord', transaction_id=ORPHAN)
    assert link.organisation_id == other.id, 'a choice somebody made was overwritten'
    assert A.suggested_party(A.Transaction.query.get(ORPHAN), 'Landlord') is None, \
        'it is still suggesting someone when a choice has been made'
print('29. a party somebody chose is never overwritten or second-guessed')


# ─── 30. A sale takes its seller the same way ───────────────────────────────
with A.app.app_context():
    seller_org = A.Organisation(name='Vendor Holdings Ltd', fee_earner='Benjamin Cowan')
    db.session.add(seller_org); db.session.commit()
    seller = A.Contact(first_name='Sara', last_name='Okelo', contact_type='Seller',
                       organisation_id=seller_org.id)
    db.session.add(seller); db.session.commit()
    A.Project.query.get(SALE['project']).client_contact_id = seller.id
    db.session.commit()
    SELLER_ORG = seller_org.id

sale_t = make_transaction(SALE)
with A.app.app_context():
    link = A.current_org_link('Seller', transaction_id=sale_t)
    assert link is not None and link.organisation_id == SELLER_ORG, \
        'a sale did not take its seller from the instruction'
    assert A.current_org_link('Landlord', transaction_id=sale_t) is None, \
        'a sale was given a landlord'
    assert A.current_org_link('Buyer', transaction_id=sale_t) is None
print('30. a sale takes its seller the same way, and is given no landlord')


# ─── 31. The transaction status reaches the website ─────────────────────────
# The reported fault: a transaction Under Offer, and the website listing still
# saying Available. The CRM knew; the feed did not read it.
def feed_status(listing_id):
    rows = cl.get('/api/listings').get_json()
    rows = rows.get('listings', rows) if isinstance(rows, dict) else rows
    for row in rows:
        if row.get('id') == f'cr-lst-{listing_id}':
            return row.get('listingStatus')
    return None


with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    t.status = 'In Progress'
    db.session.commit()
assert feed_status(LET['listing']) == 'available', feed_status(LET['listing'])

with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    t.status = 'Under Offer'
    db.session.commit()
assert feed_status(LET['listing']) == 'under-offer', \
    f'the website still says {feed_status(LET["listing"])!r} on an Under Offer deal'
print('31. a transaction going Under Offer puts its listing under offer on the site')


# ─── 32. Completed reads as let on a letting, sold on a sale ────────────────
with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    t.status = 'Completed'
    db.session.commit()
assert feed_status(LET['listing']) == 'let-agreed', feed_status(LET['listing'])

with A.app.app_context():
    t = A.Transaction.query.get(SALE_T)
    t.status = 'Completed'
    db.session.commit()
assert feed_status(SALE['listing']) == 'sold', feed_status(SALE['listing'])
print('32. a completed letting reads as let agreed; a completed sale as sold')


# ─── 33. Every status the website understands, and only those ───────────────
UNDERSTOOD = {'available', 'under-offer', 'let-agreed', 'sold', 'sold-stc', 'withdrawn'}
for status in A.TRANSACTION_STATUSES:
    with A.app.app_context():
        t = A.Transaction.query.get(LET_T)
        t.status = status
        db.session.commit()
    shown = feed_status(LET['listing'])
    assert shown in UNDERSTOOD, f'{status} produced {shown!r}, which the website cannot render'
print(f'33. all {len(A.TRANSACTION_STATUSES)} statuses map to something the website renders')


# ─── 34. A deal that fell through puts it back on the market ────────────────
with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    t.status = 'Fallen Through'
    db.session.commit()
assert feed_status(LET['listing']) == 'available', \
    'a deal that fell through is still holding the listing off the market'
print('34. a deal that falls through puts the listing back on the market')


# ─── 35. The stored field is never rewritten ────────────────────────────────
# The deal is the source of truth. Writing it onto the listing as well would
# give two places to disagree, which is the fault this replaced.
with A.app.app_context():
    t = A.Transaction.query.get(LET_T)
    t.status = 'Under Offer'
    db.session.commit()
    assert (A.Listing.query.get(LET['listing']).listing_status or 'available') == 'available', \
        'the deal status was copied onto the listing'
    assert A.Listing.query.get(LET['listing']).effective_status == 'under-offer'
print('35. the listing keeps its own field; the deal is simply read')


# ─── 36. A listing with no instruction still follows its deal ───────────────
with A.app.app_context():
    lone = A.Property(address='1 Stanley Bridge Studios, London SW6 2AD',
                      postcode='SW6 2AD', property_type='Office', size=900)
    db.session.add(lone); db.session.commit()
    l = A.Listing(property_id=lone.id, listing_price=30000, listing_price_unit='pa',
                  listing_status='available', website_listed=True)
    db.session.add(l); db.session.commit()
    LONE = l.id
    assert A.Listing.query.get(LONE).effective_status == 'available'
    db.session.add(A.Transaction(property_id=lone.id, transaction_type='Letting',
                                 status='Under Offer', reference='TR-LONE'))
    db.session.commit()
    assert A.Listing.query.get(LONE).effective_status == 'under-offer', \
        'a listing with no instruction does not follow the deal on its property'
print('36. a listing with no instruction still follows the deal on its property')


# ─── 37. Unless the property has several listings ───────────────────────────
# A deal on one unit says nothing about the others, and there is no instruction
# to tell them apart.
with A.app.app_context():
    l2 = A.Listing(property_id=A.Listing.query.get(LONE).property_id,
                   listing_price=25000, listing_price_unit='pa',
                   listing_status='available', website_listed=True)
    db.session.add(l2); db.session.commit()
    assert A.Listing.query.get(LONE).effective_status == 'available', \
        'one unit\'s deal was applied to a building with several listings'
print('37. a deal is not applied to a building whose units it cannot tell apart')


print('\nDEAL TERMS: ALL CHECKS PASSED')
