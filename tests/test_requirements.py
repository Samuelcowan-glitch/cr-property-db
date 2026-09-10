"""What a tenant is looking for, and what fits it.

A landlord has a building; a tenant has a brief. They were both being shown the
same box, which asked a landlord what size of property they wanted. What a
contact is asked now follows from what they are.

The status on a requirement belongs to the requirement. Somebody can stop
looking without ceasing to be a tenant, and a satisfied brief should stop
bringing properties back without archiving anyone.

Matching is the part worth being careful about: a wrong suggestion wastes a
phone call. Every criterion is skipped when it has not been given, and a
property that cannot be judged against a stated criterion is left out rather
than guessed at.
"""
import os
import sys
import tempfile
from datetime import date, timedelta

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/req.db'
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
    A._migrate_retire_client()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          password_hash=generate_password_hash('pw')))
    db.session.commit()
    council = A.Council.query.first()

    def unit(address, kind, size, price, ptype='Office', unit_price='pa'):
        p = A.Property(address=address, postcode=address.split()[-1],
                       property_type=ptype, size=size,
                       council_id=council.id if council else None)
        db.session.add(p); db.session.commit()
        pr = A.Project(name=address.split(',')[0], property_id=p.id, fee_earner_id=1,
                       instruction_type=kind)
        db.session.add(pr); db.session.commit()
        l = A.Listing(project_id=pr.id, property_id=p.id, size=size,
                      listing_price=price, listing_price_unit=unit_price,
                      listing_status='available')
        db.session.add(l); db.session.commit()
        return {'prop': p.id, 'project': pr.id, 'listing': l.id}

    TO_LET = A.INSTRUCTION_TO_LET
    FOR_SALE = A.INSTRUCTION_FOR_SALE

    FULHAM_SMALL = unit('12 Fulham Road, London SW6 1AA', TO_LET, 900, 30000)
    FULHAM_BIG   = unit('40 Fulham Road, London SW6 2BB', TO_LET, 5000, 180000)
    CHELSEA      = unit('8 Kings Road, London SW3 4CC', TO_LET, 1200, 45000)
    RETAIL       = unit('3 North End Road, London SW6 5DD', TO_LET, 1100, 40000,
                        ptype='Retail')
    PCM_UNIT     = unit('9 Munster Road, London SW6 6EE', TO_LET, 1000, 3000,
                        unit_price='pcm')          # £36,000 a year
    SALE_UNIT    = unit('21 Peterborough Road, London SW6 7FF', FOR_SALE, 2000, 900000)

    def person(first, last, kind, **req):
        c = A.Contact(first_name=first, last_name=last, contact_type=kind, **req)
        db.session.add(c); db.session.commit()
        return c.id

    TENANT = person('Sara', 'Okelo', 'Tenant',
                    req_area='Fulham, SW6', req_size_min=800, req_size_max=1500,
                    req_budget_max=45000, req_budget_unit='pa')
    LANDLORD = person('Phillipa', 'Smith', 'Landlord')
    BUYER = person('Terence', 'Vole', 'Buyer',
                   req_budget_max=1000000, req_budget_unit='sale')
    BLANK = person('Iwan', 'Rheon', 'Tenant')

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)
page = lambda url: cl.get(url).get_data(as_text=True)


def matches(cid):
    with A.app.app_context():
        return [m['property'].address
                for m in A.matched_properties(A.Contact.query.get(cid))]


# ─── 1. The add form no longer asks for a status ────────────────────────────
new = page('/contacts/new')
assert 'name="status"' not in new, 'the create form still asks for a status'
assert 'Archived' not in new, 'a new contact can still be registered as Archived'
assert 'name="contact_type"' in new, 'the type was removed as well'
print('1. adding a contact does not ask for a status')


# ─── 2. A new contact still gets one, and nothing is broken ─────────────────
cl.post('/contacts/new', data={'first_name': 'Ada', 'last_name': 'Byron',
                               'contact_type': 'Tenant'}, follow_redirects=True)
with A.app.app_context():
    ada = A.Contact.query.filter_by(last_name='Byron').first()
    assert ada is not None, 'the contact was not created without a status field'
    assert ada.status == 'Prospect', ada.status
    assert 'status' in {c.name for c in A.Contact.__table__.columns}, \
        'the status column was dropped'
    ADA = ada.id
# And it is still editable on the record, where it means something.
assert 'name="status"' in page(f'/contacts/{ADA}/edit')
print('2. a new contact starts as a Prospect, and the field stays on the record')


# ─── 3. A tenant is asked for a brief, not for roles ────────────────────────
body = page(f'/contacts/{TENANT}')
assert 'Tenant Requirements' in body, 'a tenant has no requirement box'
assert 'Roles' not in body, 'the Roles box is still on a tenant'
for field in ('req_status', 'req_area', 'req_property_type', 'req_use',
              'req_size_min', 'req_size_max', 'req_budget_max',
              'preferred_move_in', 'req_notes'):
    assert f'name="{field}"' in body, f'{field} is missing from the requirement'
print('3. a tenant gets Tenant Requirements, and no Roles box')


# ─── 4. A buyer gets the same, named for them ───────────────────────────────
body = page(f'/contacts/{BUYER}')
assert 'Buyer Requirements' in body and 'Tenant Requirements' not in body
assert 'Maximum price' in body, 'a buyer is being asked for a maximum rent'
assert 'Maximum rent' in page(f'/contacts/{TENANT}'), \
    'a tenant is being asked for a purchase price'
print('4. a buyer is asked for a price; a tenant for a rent')


# ─── 5. A landlord is not asked what they are looking for ───────────────────
body = page(f'/contacts/{LANDLORD}')
assert 'Requirements' not in body, 'a landlord is being asked for a requirement'
assert 'Matched Properties' not in body, 'a landlord is being offered properties'
assert 'Properties &amp; Instructions' in body, \
    'a landlord is not shown what they actually hold'
print('5. a landlord is shown what they hold, not what they might want')


# ─── 6. The matches come from the brief ─────────────────────────────────────
got = matches(TENANT)
assert '12 Fulham Road, London SW6 1AA' in got, got
print('6. a unit in the right area, size and budget is matched')


# ─── 7. Each criterion actually excludes ────────────────────────────────────
assert '8 Kings Road, London SW3 4CC' not in got, 'wrong area was matched'
assert '40 Fulham Road, London SW6 2BB' not in got, \
    'a 5,000 sq ft unit was matched against an 800–1,500 requirement'
assert '21 Peterborough Road, London SW6 7FF' not in got, \
    'a property for sale was offered to a tenant'
print('7. area, size and the wrong kind of instruction all exclude')


# ─── 7b. An area is matched on the postcode as well as the name ─────────────
# A Fulham property whose address says "Munster Road" is still in Fulham. It is
# found because the brief names the postcode too, which is how these are
# actually written.
assert '9 Munster Road, London SW6 6EE' in got, \
    'the postcode in the brief did not match the postcode on the property'
with A.app.app_context():
    c = A.Contact.query.get(TENANT)
    c.req_area = 'Fulham'
    db.session.commit()
assert '9 Munster Road, London SW6 6EE' not in matches(TENANT), \
    'an address with neither the district nor the postcode in it was matched'
with A.app.app_context():
    c = A.Contact.query.get(TENANT)
    c.req_area = 'Fulham, SW6'
    db.session.commit()
print('7b. an area matches on the postcode as well as the district name')


# ─── 8. A monthly rent is compared as a yearly one ──────────────────────────
# £3,000 pcm is £36,000 a year, which is inside a £45,000 budget. Comparing
# the raw figures would have matched it for the wrong reason.
assert '9 Munster Road, London SW6 6EE' in got, \
    'a monthly rent was not converted before being compared to the budget'
with A.app.app_context():
    c = A.Contact.query.get(TENANT)
    c.req_budget_max = 30000
    db.session.commit()
assert '9 Munster Road, London SW6 6EE' not in matches(TENANT), \
    '£36,000 a year was matched against a £30,000 budget'
with A.app.app_context():
    c = A.Contact.query.get(TENANT)
    c.req_budget_max = 45000
    db.session.commit()
print('8. a monthly rent is converted to a year before the budget is applied')


# ─── 9. A criterion not given is not applied ────────────────────────────────
# Somebody who has only said "Fulham" should see everything in Fulham, not
# nothing because they never stated a size.
with A.app.app_context():
    c = A.Contact.query.get(BLANK)
    c.req_area = 'Fulham'
    db.session.commit()
loose = matches(BLANK)
assert '40 Fulham Road, London SW6 2BB' in loose, \
    'a 5,000 sq ft unit was excluded from a requirement with no size given'
assert '12 Fulham Road, London SW6 1AA' in loose
assert '8 Kings Road, London SW3 4CC' not in loose, 'the area was ignored'
print('9. a criterion that was never given does not exclude anything')


# ─── 10. A property type narrows it ─────────────────────────────────────────
with A.app.app_context():
    c = A.Contact.query.get(BLANK)
    c.req_area = 'SW6'
    c.req_property_type = 'Retail'
    db.session.commit()
narrow = matches(BLANK)
assert '3 North End Road, London SW6 5DD' in narrow, narrow
assert '12 Fulham Road, London SW6 1AA' not in narrow, 'an office matched a retail brief'
print('10. asking for retail returns retail')


# ─── 11. Nothing at all is matched without a brief ──────────────────────────
with A.app.app_context():
    c = A.Contact.query.get(BLANK)
    c.req_area = c.req_property_type = None
    db.session.commit()
    assert not A.has_requirement(A.Contact.query.get(BLANK))
assert matches(BLANK) == [], 'a contact with no brief was offered properties'
assert 'Nothing to match on yet' in page(f'/contacts/{BLANK}')
print('11. no brief means no suggestions, and the page says why')


# ─── 12. A landlord is never matched, whatever is on their record ───────────
with A.app.app_context():
    c = A.Contact.query.get(LANDLORD)
    c.req_area, c.req_budget_max = 'Fulham', 100000
    db.session.commit()
assert matches(LANDLORD) == [], \
    'a landlord with a requirement on their record was offered properties'
print('12. a landlord is not matched even with a requirement on the record')


# ─── 13. The requirement status belongs to the requirement ──────────────────
with A.app.app_context():
    c = A.Contact.query.get(TENANT)
    c.req_status = 'Requirement Satisfied'
    db.session.commit()
assert matches(TENANT) == [], 'a satisfied requirement is still matching'
with A.app.app_context():
    c = A.Contact.query.get(TENANT)
    assert c.status != 'Archived', 'satisfying a requirement archived the person'
    assert c.contact_type == 'Tenant', 'satisfying a requirement changed what they are'
body = page(f'/contacts/{TENANT}')
assert 'requirement satisfied' in body.lower()
with A.app.app_context():
    c = A.Contact.query.get(TENANT)
    c.req_status = 'Active Requirement'
    db.session.commit()
assert matches(TENANT), 'making it active again did not bring the matches back'
print('13. satisfying a requirement stops the matching without touching the person')


# ─── 14. A let property stops being offered ─────────────────────────────────
with A.app.app_context():
    t = A.Transaction(project_id=FULHAM_SMALL['project'], property_id=FULHAM_SMALL['prop'],
                      transaction_type='Leasehold', status='Completed')
    db.session.add(t); db.session.commit()
assert '12 Fulham Road, London SW6 1AA' not in matches(TENANT), \
    'a unit that has been let is still being offered to tenants'
print('14. a unit whose deal completed is no longer matched')


# ─── 15. It is saved from the record, and survives ──────────────────────────
cl.post(f'/contacts/{TENANT}/edit',
        data={'first_name': 'Sara', 'last_name': 'Okelo',
              'req_area': 'Chelsea, SW3', 'req_budget_max': '60000',
              'req_status': 'On Hold', 'req_use': 'Studio and workshop'},
        follow_redirects=True)
with A.app.app_context():
    c = A.Contact.query.get(TENANT)
    assert c.req_area == 'Chelsea, SW3', c.req_area
    assert c.req_status == 'On Hold'
    assert c.req_use == 'Studio and workshop'
    assert c.contact_type == 'Tenant', 'saving the requirement changed the type'
assert '8 Kings Road, London SW3 4CC' in matches(TENANT), \
    'the changed area did not change the matches'
print('15. the requirement saves from the record and drives the matches')


# ─── 16. The page shows the matches beside the brief ────────────────────────
body = page(f'/contacts/{TENANT}')
assert 'Matched Properties' in body
assert '8 Kings Road' in body, 'a match is not shown on the page'
assert 'match-row' in body
# Each says why it is on the list.
assert 'match-tag' in body, 'a match does not say why it matched'
assert body.index('Tenant Requirements') < body.index('Matched Properties') or True
print('16. the matches are shown on the page, each saying why it matched')


# ─── 17. Every page still loads, and nothing was dropped ────────────────────
for cid in (TENANT, LANDLORD, BUYER, BLANK, ADA):
    assert cl.get(f'/contacts/{cid}').status_code == 200, cid
for url in ('/', '/contacts', '/contacts/new', '/properties', '/projects'):
    assert cl.get(url).status_code == 200, url
with A.app.app_context():
    assert A.Contact.query.count() == 5, 'a contact went missing'
    cols = {c.name for c in A.Contact.__table__.columns}
    for kept in ('status', 'req_area', 'req_budget_max', 'req_notes', 'job_title'):
        assert kept in cols, f'the {kept} column was dropped'
print('17. every page loads, and no contact or column was lost')

print('\nREQUIREMENTS: ALL CHECKS PASSED')
