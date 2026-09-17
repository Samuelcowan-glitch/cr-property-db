"""A website lead files itself under the right heading, with the right person.

The CRM speaks one vocabulary — Landlord, Tenant, Buyer, Seller — and this is
where a lead off the website joins it. It used to file everything under the
headings the register no longer offers ("Agency — Letting", "Agency — Sale",
"Other") and to name the person "Prospect" or "Prospective Tenant", none of
which are contact types the CRM has. Every website enquiry then had to be
corrected by hand.

What the enquiry is about decides what it is: an enquiry on a To Let unit is a
tenant and one on a For Sale unit is a buyer, whatever was picked on the form.
"""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/leads.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db

with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    A._migrate_enquiry_columns()

    to_let = A.Property(address='40 Peterborough Road, London', postcode='SW6 3BN',
                        property_type='Office', size=1636)
    for_sale = A.Property(address='12 Harwood Road, London', postcode='SW6 4QP',
                          property_type='Retail', size=900)
    both = A.Property(address='9 Munster Road, London', postcode='SW6 4EN',
                      property_type='Office', size=2400)
    db.session.add_all([to_let, for_sale, both]); db.session.commit()

    db.session.add_all([
        A.Project(name='Peterborough', property_id=to_let.id, status='Active',
                  instruction_type=A.INSTRUCTION_TO_LET),
        A.Project(name='Harwood', property_id=for_sale.id, status='Active',
                  instruction_type=A.INSTRUCTION_FOR_SALE),
        # One property carrying both, to check the right one is chosen.
        A.Project(name='Munster letting', property_id=both.id, status='Active',
                  instruction_type=A.INSTRUCTION_TO_LET),
        A.Project(name='Munster sale', property_id=both.id, status='Active',
                  instruction_type=A.INSTRUCTION_FOR_SALE),
    ])
    db.session.commit()

cl = A.app.test_client()
seq = [0]


def lead(**payload):
    """Post a website lead from a person nobody has met before.

    The endpoint caps how often one address may submit, which is a different
    thing from what is being checked here and is covered by its own test, so
    the count is cleared between leads.
    """
    A._ENQUIRY_HITS.clear()
    seq[0] += 1
    body = {'from_name': f'Person {seq[0]}',
            'from_email': f'person{seq[0]}@example.com',
            'phone': '07700 900000'}
    body.update(payload)
    r = cl.post('/api/enquiry', json=body,
                headers={'Origin': 'https://cowanandrutter.com'})
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True))
    with A.app.app_context():
        e = A.Enquiry.query.order_by(A.Enquiry.id.desc()).first()
        contact = A.Contact.query.get(e.contact_id) if e.contact_id else None
        return e, contact


# ─── 1. The property decides, not the dropdown ──────────────────────────────
e, c = lead(property='Ground Floor — 40 Peterborough Road, SW6 3BN',
            transaction='let', interest='Arrange a viewing',
            message='Is this still available?')
assert e.enquiry_type == 'Tenant — Looking to Rent', e.enquiry_type
assert c.contact_type == 'Tenant', c.contact_type
assert e.project_id, 'the enquiry was not filed against the instruction'
print('1. an enquiry on a To Let unit is a Tenant, against its instruction')

e, c = lead(property='Shop — 12 Harwood Road, SW6 4QP',
            transaction='sale', interest='Arrange a viewing',
            message='What is the guide price?')
assert e.enquiry_type == 'Buyer — Looking to Buy', e.enquiry_type
assert c.contact_type == 'Buyer', c.contact_type
print('2. an enquiry on a For Sale unit is a Buyer')

# The form said Commercial Agency, which on its own would mean a tenant. The
# property is for sale, so the property wins.
e, c = lead(property='Shop — 12 Harwood Road, SW6 4QP',
            interest='Commercial Agency', message='Interested in this one.')
assert e.enquiry_type == 'Buyer — Looking to Buy', e.enquiry_type
assert c.contact_type == 'Buyer', c.contact_type
print('3. the property overrules what was picked on the form')


# ─── 2. The right instruction where a property carries two ──────────────────
e, _ = lead(property='Suite — 9 Munster Road, SW6 4EN', transaction='sale',
            interest='Arrange a viewing', message='Price?')
with A.app.app_context():
    assert A.Project.query.get(e.project_id).name == 'Munster sale', \
        A.Project.query.get(e.project_id).name
assert e.enquiry_type == 'Buyer — Looking to Buy', e.enquiry_type

e, _ = lead(property='Suite — 9 Munster Road, SW6 4EN', transaction='let',
            interest='Arrange a viewing', message='Rent?')
with A.app.app_context():
    assert A.Project.query.get(e.project_id).name == 'Munster letting'
assert e.enquiry_type == 'Tenant — Looking to Rent', e.enquiry_type
print('4. a property offered both ways is filed against the right instruction')


# ─── 3. Owner-side enquiries, which the website form cannot yet offer ───────
e, c = lead(interest='General Enquiry',
            message='I would like to let my property in Fulham. Can you help?')
assert e.enquiry_type == 'Landlord — Looking to Let', e.enquiry_type
assert c.contact_type == 'Landlord', c.contact_type
print('5. "let my property" is a Landlord')

e, c = lead(interest='General Enquiry',
            message='We are looking to sell our building on the Kings Road.')
assert e.enquiry_type == 'Owner/Vendor — Looking to Sell', e.enquiry_type
assert c.contact_type == 'Seller', c.contact_type
print('6. "sell our building" is a Seller')

e, c = lead(interest='General Enquiry',
            message='Could you give me a valuation for a shop I own?')
assert e.enquiry_type == 'Valuation', e.enquiry_type
print('7. a valuation asks for a Valuation')

e, c = lead(interest='Management', message='Who looks after blocks in SW6?')
assert e.enquiry_type == 'Landlord — Looking to Let', e.enquiry_type
assert c.contact_type == 'Landlord', c.contact_type
print('8. a management enquiry is a Landlord — it is their property')


# ─── 4. Nothing is guessed from a bare general enquiry ──────────────────────
e, c = lead(interest='General Enquiry', message='What are your office hours?')
assert e.enquiry_type == 'General Inquiry', e.enquiry_type
assert c.contact_type in (None, ''), \
    f'a general enquiry was filed as a {c.contact_type}'
assert e.project_id is None and e.property_id is None
print('9. an enquiry about nothing in particular is filed as one, not guessed')

# A tenant looking for space is not read as a landlord because they said "my".
e, c = lead(interest='Commercial Agency',
            message='I am looking to rent a shop for my business.')
assert e.enquiry_type == 'Tenant — Looking to Rent', e.enquiry_type
assert c.contact_type == 'Tenant', c.contact_type
print('10. "a shop for my business" is still a Tenant, not a Landlord')


# ─── 5. Every type it produces is one the CRM actually offers ───────────────
for etype, ctype in A.SIDES.values():
    assert etype in A.INQUIRY_TYPES, f'{etype} is not an enquiry type'
    assert ctype is None or ctype in A.CONTACT_TYPES, f'{ctype} is not a contact type'
assert 'General Inquiry' in A.INQUIRY_TYPES
print('11. every heading it files under is one the register offers')


# ─── 6. The contact gets the role their type names ──────────────────────────
e, c = lead(property='Ground Floor — 40 Peterborough Road, SW6 3BN',
            transaction='let', interest='Arrange a viewing', message='Viewing?')
with A.app.app_context():
    contact = A.Contact.query.get(c.id)
    assert 'Tenant' in A.role_names(contact), A.role_names(contact)
print('12. the contact is given the role their type names')


# ─── 7. A landlord is not put on the applicant list ─────────────────────────
e, c = lead(interest='General Enquiry',
            message='I want to let my office at 40 Peterborough Road, SW6 3BN.')
assert e.enquiry_type == 'Landlord — Looking to Let', e.enquiry_type
with A.app.app_context():
    rows = A.ProjectApplicant.query.filter_by(contact_id=c.id).all()
    assert not rows, 'a landlord was added to the applicant list'
print('13. a landlord is never added as an applicant on a property')


# ─── 8. A type somebody already holds is not overwritten ────────────────────
with A.app.app_context():
    known = A.Contact(first_name='Margaret', last_name='Osei',
                      email='margaret@example.com', contact_type='Landlord')
    db.session.add(known); db.session.commit()

A._ENQUIRY_HITS.clear()
cl.post('/api/enquiry', json={
    'from_name': 'Margaret Osei', 'from_email': 'margaret@example.com',
    'property': 'Ground Floor — 40 Peterborough Road, SW6 3BN',
    'transaction': 'let', 'interest': 'Arrange a viewing',
    'message': 'Asking on behalf of a friend.'},
    headers={'Origin': 'https://cowanandrutter.com'})
with A.app.app_context():
    again = A.Contact.query.filter_by(email='margaret@example.com').first()
    assert again.contact_type == 'Landlord', \
        f'a known Landlord was overwritten as {again.contact_type}'
print('14. somebody already known as a Landlord stays one')

print('\nWEBSITE LEAD TYPES: ALL CHECKS PASSED')
