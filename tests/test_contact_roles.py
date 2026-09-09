"""Contacts hold roles: one person, several things they are doing."""
import os
import re
import sys
import tempfile
from datetime import timedelta
from html.parser import HTMLParser

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/roles.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db
today = A.date.today()

with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    A._migrate_progression_columns()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          password_hash=generate_password_hash('pw')))
    db.session.add(A.User(username='looker', role='viewer', full_name='A Viewer',
                          password_hash=generate_password_hash('pw')))
    db.session.commit()
    council = A.Council.query.first()

    org = A.Organisation(name='Hurlingham Holdings Ltd')
    db.session.add(org); db.session.commit()

    john = A.Contact(first_name='John', last_name='Smith', email='john@example.com',
                     mobile='07700 900123', organisation_id=org.id)
    sara = A.Contact(first_name='Sara', last_name='Okelo', email='sara@example.com')
    # Somebody filed under a type that is really a role, from before this.
    old = A.Contact(first_name='Margaret', last_name='Hale', email='m@example.com',
                    contact_type='Tenant')
    db.session.add_all([john, sara, old]); db.session.commit()

    prop = A.Property(address='10 New Kings Road, London SW6 4LT', postcode='SW6 4LT',
                      property_type='Retail', council_id=council.id)
    db.session.add(prop); db.session.commit()

    IDS = {'john': john.id, 'sara': sara.id, 'old': old.id,
           'prop': prop.id, 'org': org.id}

    A._migrate_contact_roles()

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def add_role(cid, role, **extra):
    data = {'role': role}
    data.update(extra)
    r = cl.post(f'/contacts/{cid}/roles/add', data=data, follow_redirects=True)
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)


def roles_of(cid):
    with A.app.app_context():
        return A.ContactRole.query.filter_by(contact_id=cid).all()


# ─── 1. Every role the agency needs ─────────────────────────────────────────
for wanted in ('Tenant', 'Prospective Tenant', 'Landlord', 'Prospective Landlord',
               'Buyer', 'Vendor', 'Investor', 'Agent'):
    assert wanted in A.CONTACT_ROLE_NAMES, f'{wanted} is not offered'
print(f'1. all {len(A.CONTACT_ROLE_NAMES)} roles are available')


# ─── 2. One contact holds several roles at once ─────────────────────────────
add_role(IDS['john'], 'Landlord', property_id=IDS['prop'])
add_role(IDS['john'], 'Buyer', target_area='Fulham', budget_max='900000',
         budget_unit='sale')
with A.app.app_context():
    names = A.role_names(A.Contact.query.get(IDS['john']))
assert 'Landlord' in names and 'Buyer' in names, names
assert A.Contact.query.filter_by(last_name='Smith').count() == 1 \
    if False else True
with A.app.app_context():
    assert A.Contact.query.filter(A.Contact.last_name == 'Smith').count() == 1, \
        'holding two roles created a second contact'
print('2. one person holds Landlord and Buyer at once, with no duplicate record')


# ─── 3. Each role keeps its own detail ──────────────────────────────────────
with A.app.app_context():
    by_role = {r.role: r for r in roles_of(IDS['john'])}
    assert by_role['Landlord'].property_id == IDS['prop']
    assert by_role['Buyer'].target_area == 'Fulham'
    assert by_role['Buyer'].budget_max == 900000
    # A landlord has no search budget, and a buyer no tenancy dates.
    assert by_role['Landlord'].target_area is None
    assert by_role['Buyer'].start_date is None
print('3. each role carries only the detail that role uses')


# ─── 4. A role is changed without touching the contact ──────────────────────
with A.app.app_context():
    buyer = next(r for r in roles_of(IDS['john']) if r.role == 'Buyer')
    bid = buyer.id
cl.post(f"/contacts/{IDS['john']}/roles/{bid}/edit",
        data={'role': 'Investor', 'target_area': 'Chelsea', 'budget_max': '1500000',
              'budget_unit': 'sale'}, follow_redirects=True)
with A.app.app_context():
    names = A.role_names(A.Contact.query.get(IDS['john']))
    assert 'Investor' in names and 'Buyer' not in names, names
    assert 'Landlord' in names, 'changing one role disturbed the other'
    c = A.Contact.query.get(IDS['john'])
    assert c.email == 'john@example.com' and c.first_name == 'John'
print('4. a role changes in place, leaving the contact and its other roles alone')


# ─── 5. Ending a role keeps it ──────────────────────────────────────────────
with A.app.app_context():
    landlord = next(r for r in roles_of(IDS['john']) if r.role == 'Landlord')
    lid = landlord.id
cl.post(f"/contacts/{IDS['john']}/roles/{lid}/end", data={}, follow_redirects=True)
with A.app.app_context():
    still = A.ContactRole.query.get(lid)
    assert still is not None, 'ending a role deleted it'
    assert still.end_date == today
    assert not still.is_current
    assert 'Landlord' not in A.role_names(A.Contact.query.get(IDS['john']))
    assert len(roles_of(IDS['john'])) == 2, 'the ended role left the record'
print('5. ending a role stops it being current but keeps it on the record')


# ─── 6. The same role twice is refused ──────────────────────────────────────
before = len(roles_of(IDS['sara']))
add_role(IDS['sara'], 'Prospective Tenant', target_area='Fulham')
body = add_role(IDS['sara'], 'Prospective Tenant', target_area='Fulham')
assert 'already recorded' in body, 'a duplicate role was accepted'
assert len(roles_of(IDS['sara'])) == before + 1
print('6. the same role twice is refused rather than duplicated')


# ─── 7. But the same role about a different property is allowed ─────────────
add_role(IDS['sara'], 'Prospective Tenant', property_id=IDS['prop'])
assert len(roles_of(IDS['sara'])) == 2, \
    'a second search about a specific property was refused'
print('7. the same role about a different property is a separate relationship')


# ─── 8. Nonsense is refused ─────────────────────────────────────────────────
before = len(roles_of(IDS['sara']))
body = add_role(IDS['sara'], 'Supreme Overlord')
assert len(roles_of(IDS['sara'])) == before, 'an invented role was accepted'
assert 'Choose a role' in body
with A.app.app_context():
    pt = next(r for r in roles_of(IDS['sara']) if r.property_id)
    rid = pt.id
body = cl.post(f"/contacts/{IDS['sara']}/roles/{rid}/edit",
               data={'role': 'Tenant', 'start_date': '2026-06-01',
                     'end_date': '2026-01-01'}, follow_redirects=True).get_data(as_text=True)
assert 'end date is before the start date' in body
print('8. an invented role, and an end date before its start, are both refused')


# ─── 9. The type is the four sides of a deal, and nothing else ──────────────
# The type is the headline and the roles are the full picture, so the type
# offers only the four — not every role somebody might hold.
page = cl.get(f"/contacts/{IDS['john']}").get_data(as_text=True)
block = page[page.index('name="contact_type"'):]
block = block[:block.index('</select>')]
for label in ('Landlord', 'Tenant', 'Buyer', 'Seller'):
    assert f'>{label}<' in block, f'the type dropdown lost {label}'
for narrower in ('Prospective Tenant', 'Prospective Landlord', 'Investor', 'Agent'):
    assert f'>{narrower}<' not in block, \
        f'{narrower} is a role, not one of the four types'
print('9. the type offers the four sides of a deal, and not the narrower roles')


# ─── 9b. Setting a type records the matching role, and removes nothing ──────
cl.post(f"/contacts/{IDS['sara']}/edit",
        data={'first_name': 'Sara', 'last_name': 'Okelo',
              'contact_type': 'Landlord'}, follow_redirects=True)
with A.app.app_context():
    sara_now = A.Contact.query.get(IDS['sara'])
    assert sara_now.contact_type == 'Landlord'
    assert 'Landlord' in A.role_names(sara_now), \
        'the type says Landlord but no Landlord role was recorded'
    kept = {r.role for r in roles_of(IDS['sara'])}
    assert 'Prospective Tenant' in kept, 'changing the type removed another role'
# Saving again must not record it twice.
cl.post(f"/contacts/{IDS['sara']}/edit",
        data={'first_name': 'Sara', 'last_name': 'Okelo',
              'contact_type': 'Landlord'}, follow_redirects=True)
with A.app.app_context():
    n = A.ContactRole.query.filter_by(contact_id=IDS['sara'],
                                      role='Landlord').count()
assert n == 1, f'{n} Landlord roles after saving the same type twice'
print('9b. setting a type records the matching role once, and removes nothing')


# ─── 9c. A type nobody offers is refused ────────────────────────────────────
cl.post(f"/contacts/{IDS['sara']}/edit",
        data={'first_name': 'Sara', 'last_name': 'Okelo',
              'contact_type': 'Supreme Overlord'}, follow_redirects=True)
with A.app.app_context():
    got = A.Contact.query.get(IDS['sara']).contact_type
assert got != 'Supreme Overlord', 'an invented type was saved'
print('9c. an invented type is not saved')


# ─── 10. An existing contact_type becomes a role, once ──────────────────────
with A.app.app_context():
    margaret = roles_of(IDS['old'])
    assert any(r.role == 'Tenant' for r in margaret), \
        "a contact filed as Tenant did not gain the matching role"
    assert A.Contact.query.get(IDS['old']).contact_type == 'Tenant', \
        'the contact type was rewritten'
    before = len(margaret)
    A._migrate_contact_roles()
    assert len(roles_of(IDS['old'])) == before, 'running it again duplicated the role'
print('10. an existing type is recorded as a role once, without being rewritten')


# ─── 11. Roles show on the contact record ───────────────────────────────────
page = cl.get(f"/contacts/{IDS['john']}").get_data(as_text=True)
assert 'Roles' in page
assert 'Investor' in page
assert 'Add a role' in page
assert 'past role' in page, 'the ended role is not shown as history'
print('11. the record shows current roles, a way to add one, and past ones')


# ─── 12. Linked Properties is still derived and separate ────────────────────
with A.app.app_context():
    A.Property.query.get(IDS['prop']).client_contact_id = IDS['john']
    db.session.commit()
page = cl.get(f"/contacts/{IDS['john']}").get_data(as_text=True)
assert 'Linked Properties' in page
assert '10 New Kings Road' in page
src = open(os.path.join(ROOT, 'app.py')).read()
import ast
tree = ast.parse(src)
fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == 'linked_properties')
body = fn.body[1:] if isinstance(fn.body[0], ast.Expr) else fn.body
code = '\n'.join(ast.unparse(x) for x in body)
assert 'ContactRole' not in code, \
    'linked properties now reads assigned roles — it must stay derived'
assert 'client_contact_id' in code, 'it no longer reads the real relationship'
print('12. Linked Properties still derives from real records, separately from roles')


# ─── 13. The list filters by role ───────────────────────────────────────────
page = cl.get('/contacts?role=Investor').get_data(as_text=True)
assert 'John Smith' in page, 'filtering by Investor lost the investor'
assert 'Sara Okelo' not in page, 'filtering by Investor returned somebody else'
page = cl.get('/contacts?role=Prospective+Tenant').get_data(as_text=True)
assert 'Sara Okelo' in page and 'John Smith' not in page
page = cl.get('/contacts?role=Landlord').get_data(as_text=True)
assert 'John Smith' not in page, \
    'an ended Landlord role still shows in the Landlord list'
print('13. the list filters by role, and an ended role drops out of it')


# ─── 14. It is a card list, not a table ─────────────────────────────────────
page = cl.get('/contacts').get_data(as_text=True)
assert 'ct-card' in page and 'ct-list' in page
assert 'rec-table' not in page, 'the contacts list is still a table'
assert 'role-tag' in page, 'the cards do not show which roles somebody holds'
for quick in ('tel:', 'mailto:'):
    assert quick in page, f'the cards have no {quick} action'
print('14. contacts are cards with role tags and call / email actions')


# ─── 15. Sorting works ──────────────────────────────────────────────────────
for sort in ('name', 'added', 'company'):
    r = cl.get(f'/contacts?sort={sort}')
    assert r.status_code == 200, f'sorting by {sort} failed'
assert 'Recently added' in cl.get('/contacts').get_data(as_text=True)
print('15. the list sorts by name, date added and company')


# ─── 16. Permissions ────────────────────────────────────────────────────────
viewer = A.app.test_client()
viewer.post('/login', data={'username': 'looker', 'password': 'pw'},
            follow_redirects=True)
assert viewer.post(f"/contacts/{IDS['sara']}/roles/add",
                   data={'role': 'Buyer'}).status_code == 403
with A.app.app_context():
    any_role = roles_of(IDS['sara'])[0].id
assert viewer.post(f"/contacts/{IDS['sara']}/roles/{any_role}/end",
                   data={}).status_code == 403
assert viewer.get('/contacts').status_code == 200
print('16. a viewer can read the list but cannot assign or end a role')


# ─── 17. Every change is audited ────────────────────────────────────────────
with A.app.app_context():
    actions = {a.action for a in A.AuditLog.query.all()}
for expected in ('role-added', 'role-edited', 'role-ended'):
    assert expected in actions, f'{expected} was never audited'
print('17. adding, changing and ending a role are all audited')


# ─── 18. The pages are valid, closed markup ─────────────────────────────────
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
        'meta', 'source', 'track', 'wbr'}


class Balance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.bad.append(f'stray </{tag}>')
        elif self.stack[-1] != tag:
            self.bad.append(f'</{tag}> closes <{self.stack[-1]}>')
        else:
            self.stack.pop()


for name, url in (('contacts list', '/contacts'),
                  ('filtered list', '/contacts?role=Investor'),
                  ('contact record', f"/contacts/{IDS['john']}"),
                  ('no roles yet', f"/contacts/{IDS['old']}")):
    p = Balance()
    p.feed(cl.get(url).get_data(as_text=True))
    assert not p.bad, f'{name}: {p.bad[:3]}'
    assert not p.stack, f'{name}: unclosed {p.stack[:3]}'
print('18. every contacts page renders as valid, fully closed markup')


# ─── 19. No form is nested inside another ───────────────────────────────────
for url in ('/contacts', f"/contacts/{IDS['john']}"):
    body = cl.get(url).get_data(as_text=True)
    assert not re.search(r'<form[^>]*>(?:(?!</form>).)*<form', body, re.S), \
        f'{url} has nested forms'
print('19. the role forms do not nest inside the record form')

print('\nCONTACT ROLES: ALL CHECKS PASSED')
