"""A Landlord box searches landlords. A Tenant box searches tenants.

The two searches used to call the same endpoint with nothing but the typed
letters, so they returned the same thing — everything. That is why a tenant
search brought back clients, and why the landlords you wanted were buried
among whatever else matched.

The field knows which side of the deal it is filling. It says so, and the
search honours it. A letting offers landlords and tenants; a sale offers
sellers and buyers; neither is offered the other's people.
"""
import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/party.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db

with A.app.app_context():
    db.create_all()
    A._sync_model_columns()
    A._migrate_rates_tables()
    A._migrate_progression_columns()
    A._migrate_contact_roles()
    A._migrate_retire_client()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          password_hash=generate_password_hash('pw')))
    db.session.commit()

    # Everybody is called Marsden, so only the type can tell them apart. If the
    # search ignores the role, every one of these comes back for every field.
    def org_with(name, kind, person):
        o = A.Organisation(name=f'Marsden {name}', fee_earner='Benjamin Cowan')
        db.session.add(o); db.session.commit()
        c = A.Contact(first_name=person, last_name='Marsden',
                      contact_type=kind, organisation_id=o.id)
        db.session.add(c); db.session.commit()
        return o.id, c.id

    LANDLORD_ORG, LANDLORD = org_with('Estates', 'Landlord', 'Lena')
    TENANT_ORG, TENANT = org_with('Retail', 'Tenant', 'Tom')
    SELLER_ORG, SELLER = org_with('Holdings', 'Seller', 'Sara')
    BUYER_ORG, BUYER = org_with('Capital', 'Buyer', 'Ben')

    # Somebody filed under the retired type, which is what was leaking into
    # tenant searches.
    legacy_org = A.Organisation(name='Marsden Legacy', fee_earner='Benjamin Cowan')
    db.session.add(legacy_org); db.session.commit()
    legacy = A.Contact(first_name='Clive', last_name='Marsden',
                       contact_type='Client', organisation_id=legacy_org.id)
    db.session.add(legacy); db.session.commit()
    LEGACY_ORG, LEGACY = legacy_org.id, legacy.id

    prop = A.Property(address='Worlds End Studios, London SW10 0RJ', postcode='SW10 0RJ')
    db.session.add(prop); db.session.commit()
    letting = A.Project(name='Worlds End Studios', property_id=prop.id,
                        instruction_type=A.INSTRUCTION_TO_LET)
    sale = A.Project(name='Peterborough Road', property_id=prop.id,
                     instruction_type=A.INSTRUCTION_FOR_SALE)
    db.session.add_all([letting, sale]); db.session.commit()
    LETTING, SALE, PROP = letting.id, sale.id, prop.id

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def orgs(role=None, q='Marsden'):
    url = f'/api/organisations?q={q}' + (f'&role={role}' if role else '')
    return {row['name'] for row in cl.get(url).get_json()}


def people(kind=None, q='Marsden'):
    url = f'/api/contacts?q={q}' + (f'&type={kind}' if kind else '')
    return {row['name'] for row in cl.get(url).get_json()}


# ─── 1. Unfiltered, everything comes back — the old behaviour ───────────────
everyone = orgs()
assert len(everyone) >= 5, everyone
assert 'Marsden Legacy' in everyone
print(f'1. an unfiltered search returns all {len(everyone)} of them, as it did')


# ─── 2. A landlord search returns landlords ─────────────────────────────────
found = orgs('Landlord')
assert found == {'Marsden Estates'}, found
print('2. the Landlord field returns only the landlord')


# ─── 3. A tenant search returns tenants, and no clients ─────────────────────
found = orgs('Tenant')
assert found == {'Marsden Retail'}, found
assert 'Marsden Legacy' not in found, 'a Client is still appearing in a Tenant search'
assert 'Marsden Estates' not in found, 'a landlord is appearing in a Tenant search'
print('3. the Tenant field returns only the tenant — no clients, no landlords')


# ─── 4. A sale offers sellers and buyers, and nothing else ──────────────────
assert orgs('Seller') == {'Marsden Holdings'}, orgs('Seller')
assert orgs('Buyer') == {'Marsden Capital'}, orgs('Buyer')
for role in ('Seller', 'Buyer'):
    assert 'Marsden Estates' not in orgs(role), f'a landlord appears in a {role} search'
    assert 'Marsden Retail' not in orgs(role), f'a tenant appears in a {role} search'
for role in ('Landlord', 'Tenant'):
    assert 'Marsden Holdings' not in orgs(role), f'a seller appears in a {role} search'
    assert 'Marsden Capital' not in orgs(role), f'a buyer appears in a {role} search'
print('4. a sale offers sellers and buyers; a letting offers landlords and tenants')


# ─── 5. The four sides never overlap ────────────────────────────────────────
sides = {r: orgs(r) for r in ('Landlord', 'Tenant', 'Seller', 'Buyer')}
for a in sides:
    for b in sides:
        if a != b:
            assert not (sides[a] & sides[b]), f'{a} and {b} both return {sides[a] & sides[b]}'
print('5. no organisation is offered for two different sides')


# ─── 6. A role nobody recognises is ignored, not obeyed ─────────────────────
# An invented role must not silently return nothing, which would look like
# "there are no landlords" rather than "that is not a role".
assert orgs('Client') == everyone, 'the retired Client role is still filtering'
assert orgs('Nonsense') == everyone, 'an invented role narrowed the search'
print('6. an unknown role is ignored rather than quietly returning nothing')


# ─── 7. The same rule for the people search ─────────────────────────────────
assert people('Landlord') == {'Lena Marsden'}, people('Landlord')
assert people('Tenant') == {'Tom Marsden'}, people('Tenant')
assert 'Clive Marsden' not in people('Tenant'), 'a Client appears in a Tenant search'
assert people('Seller') == {'Sara Marsden'}
assert people('Buyer') == {'Ben Marsden'}
assert len(people()) >= 5, 'the unfiltered people search stopped returning everybody'
print('7. the people search narrows the same way')


# ─── 8. The picker on the record tells the search which side it is ──────────
with A.app.app_context():
    t = A.Transaction(project_id=LETTING, property_id=PROP,
                      transaction_type='Leasehold', reference='TR0003')
    db.session.add(t); db.session.commit()
    LET_T = t.id
    t2 = A.Transaction(project_id=SALE, property_id=PROP,
                       transaction_type='Capital', reference='TR0004')
    db.session.add(t2); db.session.commit()
    SALE_T = t2.id

body = cl.get(f'/transactions/{LET_T}').get_data(as_text=True)
roles = set(re.findall(r'data-role="([^"]+)"', body))
assert roles == {'Landlord', 'Tenant'}, roles
body = cl.get(f'/transactions/{SALE_T}').get_data(as_text=True)
roles = set(re.findall(r'data-role="([^"]+)"', body))
assert roles == {'Seller', 'Buyer'}, roles
print('8. the record marks each field with the side it is filling')


# ─── 9. And the script sends it ─────────────────────────────────────────────
js = open(os.path.join(ROOT, 'static/js/org-picker.js')).read()
search = js[js.index('function search('):]
search = search[:search.index('\n  }')]
assert 'dataset.role' in search and 'role=' in search, \
    'the picker still searches without saying which side it is'
print('9. the picker sends that side to the search')


# ─── 10. The contact offered within an organisation follows too ─────────────
with A.app.app_context():
    org = A.Organisation.query.get(LANDLORD_ORG)
    db.session.add(A.Contact(first_name='Tessa', last_name='Marsden',
                             contact_type='Tenant', organisation_id=org.id))
    db.session.commit()
rows = cl.get(f'/api/organisations/{LANDLORD_ORG}/contacts?role=Landlord').get_json()
names = {r['name'] for r in rows}
assert names == {'Lena Marsden'}, names
assert 'Tessa Marsden' not in names, 'a tenant is offered as the landlord contact'
print('10. the contact offered within an organisation matches the side too')


# ─── 11. An organisation with nobody typed still offers somebody ────────────
# Narrowing must not leave a field with no one to choose, which reads as broken.
with A.app.app_context():
    blank = A.Organisation(name='Untyped Holdings', fee_earner='Benjamin Cowan')
    db.session.add(blank); db.session.commit()
    db.session.add(A.Contact(first_name='Nobody', last_name='Typed',
                             organisation_id=blank.id))
    db.session.commit()
    BLANK = blank.id
rows = cl.get(f'/api/organisations/{BLANK}/contacts?role=Landlord').get_json()
assert {r['name'] for r in rows} == {'Nobody Typed'}, rows
print('11. an organisation whose people are untyped still offers them')


# ─── 12. The add form suggests the right people per field ───────────────────
form = cl.get('/transactions/new').get_data(as_text=True)
for field, kind in (('landlord', 'Landlord'), ('tenant', 'Tenant'),
                    ('vendor', 'Seller'), ('purchaser', 'Buyer')):
    pick = re.search(rf'<div class="partypick" data-party="([^"]+)" data-field="{field}"',
                     form)
    assert pick, f'{field} has no picker on the add form'
    assert pick.group(1) == kind, \
        f'the {field} picker asks for {pick.group(1)}s, not {kind}s'
    assert f'name="{field}_contact_id"' in form, \
        f'{field} does not carry the chosen person'
assert 'partypick-change' in form, 'there is no way to change a chosen party'
assert 'partypick-card' in form, 'a chosen party is not shown as a card'
print('12. each party picker on the add form asks for its own kind of person')


# ─── 13. Nothing that was recorded has changed ──────────────────────────────
with A.app.app_context():
    assert A.Contact.query.count() == 7, A.Contact.query.count()
    assert A.Contact.query.get(LEGACY) is not None, 'a Client contact was removed'
    for url in ('/transactions/new', f'/transactions/{LET_T}', f'/transactions/{SALE_T}',
                '/contacts', '/organisations'):
        assert cl.get(url).status_code == 200, url
print('13. every contact is still there and every page still loads')

print('\nPARTY SEARCH: ALL CHECKS PASSED')
