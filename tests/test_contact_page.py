"""The contact record: type, linked properties, and what is no longer there."""
import os
import re
import sys
import tempfile
from html.parser import HTMLParser

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/contacts.db'
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
                          password_hash=generate_password_hash('pw')))
    db.session.commit()
    council = A.Council.query.first()

    org = A.Organisation(name='Hurlingham Holdings Ltd')
    db.session.add(org); db.session.commit()

    # John is the client on one property directly, and the client on an
    # instruction that carries another.
    john = A.Contact(first_name='John', last_name='Smith',
                     email='john@hurlingham.co.uk', mobile='07700 900123',
                     organisation_id=org.id, contact_type='Prospect')
    # Sara is an applicant with requirements — she must NOT get properties.
    sara = A.Contact(first_name='Sara', last_name='Okelo', email='sara@example.com',
                     contact_type='Tenant', req_category='commercial',
                     req_area='Fulham', req_size_min=500, req_size_max=2000,
                     req_budget_max=60000, req_budget_unit='pa')
    nobody = A.Contact(first_name='Nora', last_name='Quinn', email='n@example.com')
    db.session.add_all([john, sara, nobody]); db.session.commit()

    direct = A.Property(address='10 New Kings Road, London SW6 4LT',
                        postcode='SW6 4LT', property_type='Retail', size=900,
                        council_id=council.id, client_contact_id=john.id)
    viaproj = A.Property(address='42 Peterborough Road, London SW6 3BN',
                         postcode='SW6 3BN', property_type='Office', size=1400,
                         council_id=council.id)
    other = A.Property(address='99 Nothing To Do With John Street',
                       postcode='W1 1AA', property_type='Office', size=1200,
                       council_id=council.id)
    db.session.add_all([direct, viaproj, other]); db.session.commit()

    proj = A.Project(name='42 Peterborough Road', property_id=viaproj.id,
                     fee_earner_id=1, instruction_type=A.INSTRUCTION_TO_LET,
                     status='Active', client_contact_id=john.id)
    db.session.add(proj); db.session.commit()

    # Something that would match Sara's requirement, if matching still ran.
    listing = A.Listing(project_id=proj.id, property_id=viaproj.id,
                        set_as_to_let=True, listing_price=45000,
                        listing_price_unit='pa', listing_status='available',
                        website_category='commercial', area='Fulham')
    db.session.add(listing); db.session.commit()

    IDS = {'john': john.id, 'sara': sara.id, 'nobody': nobody.id,
           'direct': direct.id, 'viaproj': viaproj.id, 'other': other.id,
           'proj': proj.id}

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def page(cid):
    r = cl.get(f'/contacts/{cid}')
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)


# ─── 1. Type is a dropdown of Client and Tenant ─────────────────────────────
body = page(IDS['sara'])
block = body[body.index('name="contact_type"'):]
block = block[:block.index('</select>')]
assert 'name="contact_type"' in body
assert 'input type="text" name="contact_type"' not in body, \
    'Type is still a free-text box'
for t in ('Client', 'Tenant'):
    assert f'>{t}<' in block, f'{t!r} is not offered'
print('1. Type is a dropdown offering Client and Tenant')


# ─── 2. It is edited on the page, not on a separate screen ──────────────────
rec_form = body[body.index('<form'):body.rindex('</form>')]
assert 'name="contact_type"' in rec_form
assert f"/contacts/{IDS['sara']}/edit" in body, \
    'the page does not post to the contact edit route'
cl.post(f"/contacts/{IDS['sara']}/edit", data={'contact_type': 'Client'},
        follow_redirects=True)
with A.app.app_context():
    assert A.Contact.query.get(IDS['sara']).contact_type == 'Client'
    A.Contact.query.get(IDS['sara']).contact_type = 'Tenant'
    db.session.commit()
print('2. the type saves from the contact page itself')


# ─── 3. An older type is kept and offered, not silently rewritten ───────────
body = page(IDS['john'])
block = body[body.index('name="contact_type"'):]
block = block[:block.index('</select>')]
assert '>Prospect<' in block, "a contact filed as Prospect loses their type"
cl.post(f"/contacts/{IDS['john']}/edit", data={'mobile': '07700 900123'},
        follow_redirects=True)
with A.app.app_context():
    assert A.Contact.query.get(IDS['john']).contact_type == 'Prospect', \
        'saving the page reclassified an existing contact'
print('3. an existing contact keeps its own type, and saving does not change it')


# ─── 4. Matched Properties is gone ──────────────────────────────────────────
for cid in (IDS['john'], IDS['sara'], IDS['nobody']):
    body = page(cid)
    assert 'Matched Properties' not in body, 'Matched Properties is still there'
    assert 'matched_properties' not in body
    assert 'match-row' not in body and 'match-list' not in body
print('4. Matched Properties is gone from every contact page')


# ─── 5. Matching no longer runs for a contact ───────────────────────────────
src = open(os.path.join(ROOT, 'app.py')).read()
route = src[src.index('def contact_detail(id):'):]
route = route[:route.index('\n@app.route')]
assert 'match_properties_to_contact' not in route, \
    'the contact page still runs property matching'
assert 'linked_properties' in route
print('5. the contact page no longer runs the matching logic at all')


# ─── 6. Requirements are gone from the page ─────────────────────────────────
body = page(IDS['sara'])
assert '>Requirement<' not in body and 'New Requirement' not in body
for field in ('req_category', 'req_area', 'req_size_min', 'req_size_max',
              'req_budget_max', 'req_notes'):
    assert f'name="{field}"' not in body, f'{field} is still on the contact page'
print('6. no requirement fields remain on the contact page')


# ─── 7. But the requirement DATA is untouched ───────────────────────────────
with A.app.app_context():
    s = A.Contact.query.get(IDS['sara'])
    assert s.req_area == 'Fulham' and s.req_budget_max == 60000, \
        'requirement data was destroyed rather than merely hidden'
print('7. requirement data is preserved on the record, just not shown here')


# ─── 8. Linked Properties shows a genuine relationship ──────────────────────
body = page(IDS['john'])
assert 'Linked Properties' in body
assert '10 New Kings Road' in body, \
    'the property John is the client of is not listed'
assert '42 Peterborough Road' in body, \
    "the property behind John's instruction is not listed"
assert '99 Nothing To Do With John' not in body, \
    'an unrelated property appeared'
print('8. both properties John is genuinely recorded against are listed')


# ─── 9. It is derived, with nothing linked twice ────────────────────────────
with A.app.app_context():
    rows = A.linked_properties(A.Contact.query.get(IDS['john']))
    addresses = [r['property'].address for r in rows]
    assert len(addresses) == len(set(addresses)) == 2, addresses
    by_addr = {r['property'].address: r for r in rows}
    assert by_addr['10 New Kings Road, London SW6 4LT']['roles'] == ['Client']
    row = by_addr['42 Peterborough Road, London SW6 3BN']
    assert row['instruction'] == A.INSTRUCTION_TO_LET, row['instruction']
    assert row['status'] == 'Active', row['status']
print('9. each property appears once, with its role, status and instruction type')


# ─── 10. The card shows address, status and instruction, and links ──────────
body = page(IDS['john'])
card = body[body.index('id="linked-properties"'):body.index('</div>', body.index('lp-note')) if 'lp-note' in body else -1]
assert f"/properties/{IDS['direct']}" in card, 'the card does not link to the property'
assert f"/properties/{IDS['viaproj']}" in card
assert 'Active' in card, 'the status is not shown'
assert A.INSTRUCTION_TO_LET in card, 'the instruction type is not shown'
print('10. each card shows address, status and instruction type, and links through')


# ─── 11. Nothing is suggested — an applicant gets no properties ─────────────
body = page(IDS['sara'])
assert 'Linked Properties' in body
assert 'No linked properties' in body, \
    'an applicant with requirements was given matched properties'
assert '42 Peterborough Road' not in body, \
    'a property was suggested from her requirement'
with A.app.app_context():
    assert A.linked_properties(A.Contact.query.get(IDS['sara'])) == []
print('11. an applicant with requirements gets no properties — nothing is matched')


# ─── 12. A contact with nothing shows the empty message ─────────────────────
body = page(IDS['nobody'])
assert 'No linked properties' in body
print('12. a contact with no relationships shows "No linked properties"')


# ─── 13. Removing the relationship removes the property ─────────────────────
with A.app.app_context():
    p = A.Property.query.get(IDS['direct'])
    p.client_contact_id = None
    db.session.commit()
body = page(IDS['john'])
assert '10 New Kings Road' not in body, \
    'the property stayed after the relationship was removed'
assert '42 Peterborough Road' in body, 'the other relationship was lost too'
with A.app.app_context():
    A.Property.query.get(IDS['direct']).client_contact_id = IDS['john']
    db.session.commit()
print('13. the list follows the relationship — remove it and the property goes')


# ─── 14. The page keeps what it should ──────────────────────────────────────
body = page(IDS['john'])
for wanted in ('Contact Details', 'Linked Properties', 'Journal',
               'name="first_name"', 'name="last_name"', 'name="contact_type"',
               'name="organisation_id"', 'name="email"', 'name="mobile"'):
    assert wanted in body, f'{wanted!r} was lost from the contact page'
print('14. name, type, organisation, email, mobile, linked properties and journal remain')


# ─── 15. The page is valid, closed markup ───────────────────────────────────
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


for name, cid in (('john', IDS['john']), ('sara', IDS['sara']),
                  ('nobody', IDS['nobody'])):
    p = Balance()
    p.feed(page(cid))
    assert not p.bad, f'{name}: {p.bad[:3]}'
    assert not p.stack, f'{name}: unclosed {p.stack[:3]}'
print('15. every contact page renders as valid, fully closed markup')


# ─── 16. The four concepts stay separate ────────────────────────────────────
# Contact = who they are. Linked property = what they own or are client for.
# Applicant requirement = what they want. Matching = against a requirement.
import ast as _ast
tree = _ast.parse(src)
fn_node = next(n for n in _ast.walk(tree)
               if isinstance(n, _ast.FunctionDef) and n.name == 'linked_properties')
# Drop the docstring, then read back only the executable statements.
body = fn_node.body[1:] if (fn_node.body and isinstance(fn_node.body[0], _ast.Expr)
                            and isinstance(fn_node.body[0].value, _ast.Constant)) else fn_node.body
code = '\n'.join(_ast.unparse(stmt) for stmt in body)
for forbidden in ('req_', 'match', 'budget', 'ProjectApplicant'):
    assert forbidden not in code, \
        f'linked_properties consults {forbidden!r} — that is matching, not a relationship'
assert 'client_contact_id' in code
print('16. linked properties reads relationships only — no requirement, no matching')

print('\nCONTACT PAGE: ALL CHECKS PASSED')
