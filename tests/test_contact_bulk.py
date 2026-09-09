"""Tags, and doing one thing to a lot of contacts at once.

A role says what somebody is doing and comes from a fixed list. A tag is a
label the agency puts on somebody — "Key account", "Kings Road" — and is
deliberately free text. The two are separate on purpose, and this holds them
apart as well as checking the bulk bar.
"""
import csv
import io
import os
import sys
import tempfile
from html.parser import HTMLParser

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/bulk.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db

NAMES = [('John', 'Smith'), ('Sara', 'Okelo'), ('Margaret', 'Hale'),
         ('Iwan', 'Rheon'), ('Priya', 'Shah')]

with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    A._migrate_progression_columns()
    A._migrate_contact_roles()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          password_hash=generate_password_hash('pw')))
    db.session.add(A.User(username='looker', role='viewer', full_name='A Viewer',
                          password_hash=generate_password_hash('pw')))
    org = A.Organisation(name='Hurlingham Holdings Ltd')
    db.session.add(org)
    db.session.commit()
    for first, last in NAMES:
        db.session.add(A.Contact(first_name=first, last_name=last,
                                 email=f'{first.lower()}@example.com',
                                 mobile='07700 900123',
                                 organisation_id=org.id))
    db.session.commit()
    IDS = [c.id for c in A.Contact.query.order_by(A.Contact.id).all()]

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def bulk(action, ids, follow=True, **extra):
    data = {'action': action, 'ids': [str(i) for i in ids]}
    data.update(extra)
    return cl.post('/contacts/bulk', data=data, follow_redirects=follow)


def tags_of(cid):
    with A.app.app_context():
        return A.contact_tags(A.Contact.query.get(cid))


# ─── 1. A tag is stored as typed, and read back as a list ───────────────────
cl.post(f'/contacts/{IDS[0]}/edit',
        data={'first_name': 'John', 'last_name': 'Smith',
              'tags': 'Key account, Kings Road'}, follow_redirects=True)
assert tags_of(IDS[0]) == ['Key account', 'Kings Road'], tags_of(IDS[0])
print('1. tags are saved on the record and read back in order')


# ─── 2. Spacing and case do not make a second tag ───────────────────────────
cl.post(f'/contacts/{IDS[0]}/edit',
        data={'first_name': 'John', 'last_name': 'Smith',
              'tags': '  Key   account ,KEY ACCOUNT,  Kings Road '},
        follow_redirects=True)
got = tags_of(IDS[0])
assert got == ['Key account', 'Kings Road'], got
print('2. the same tag typed twice, or in another case, is stored once')


# ─── 3. Tagging in bulk adds, and never replaces ────────────────────────────
r = bulk('tag', IDS[:3], tag='Christmas card')
assert r.status_code == 200
for cid in IDS[:3]:
    assert 'Christmas card' in tags_of(cid), f'{cid} was not tagged'
assert tags_of(IDS[0]) == ['Key account', 'Kings Road', 'Christmas card'], \
    'bulk tagging overwrote the tags already there'
assert tags_of(IDS[3]) == [], 'somebody outside the selection was tagged'
print('3. a bulk tag is added to each selected contact, keeping what was there')


# ─── 4. Tagging the same people again does not duplicate ────────────────────
bulk('tag', IDS[:3], tag='christmas card')
assert tags_of(IDS[1]).count('Christmas card') == 1, tags_of(IDS[1])
assert len(tags_of(IDS[1])) == 1
print('4. re-tagging the same people does not duplicate the tag')


# ─── 5. An empty tag is refused rather than writing nothing ─────────────────
before = tags_of(IDS[4])
body = bulk('tag', [IDS[4]], tag='   ').get_data(as_text=True)
assert tags_of(IDS[4]) == before
assert 'Type a tag' in body
print('5. an empty tag is refused')


# ─── 6. A role assigned in bulk is a real role record ───────────────────────
bulk('role', IDS[:3], role='Prospective Tenant')
with A.app.app_context():
    for cid in IDS[:3]:
        c = A.Contact.query.get(cid)
        assert 'Prospective Tenant' in A.role_names(c), cid
        assert c.contact_type in (None, ''), \
            'assigning a role in bulk rewrote the contact type'
    assert A.ContactRole.query.filter_by(contact_id=IDS[3]).count() == 0
print('6. a bulk role is a real role on each contact, and leaves the type alone')


# ─── 7. Assigning it twice does not give anybody it twice ───────────────────
bulk('role', IDS[:3], role='Prospective Tenant')
with A.app.app_context():
    n = A.ContactRole.query.filter_by(contact_id=IDS[0],
                                      role='Prospective Tenant').count()
assert n == 1, f'{n} copies of the same role'
print('7. assigning the same role again leaves the existing one alone')


# ─── 8. An invented role is refused ─────────────────────────────────────────
with A.app.app_context():
    before = A.ContactRole.query.count()
body = bulk('role', IDS, role='Supreme Overlord').get_data(as_text=True)
with A.app.app_context():
    assert A.ContactRole.query.count() == before, 'an invented role was accepted'
assert 'Choose a role' in body
print('8. an invented role is refused, and nothing is written')


# ─── 9. Nothing selected does nothing ───────────────────────────────────────
with A.app.app_context():
    before = A.ContactRole.query.count()
for data in ({'action': 'tag', 'tag': 'x'},
             {'action': 'role', 'role': 'Buyer'},
             {'action': 'role', 'role': 'Buyer', 'ids': ['not-a-number']}):
    body = cl.post('/contacts/bulk', data=data,
                   follow_redirects=True).get_data(as_text=True)
    assert 'Nothing was selected' in body, data
with A.app.app_context():
    assert A.ContactRole.query.count() == before
print('9. an empty or unreadable selection changes nothing')


# ─── 10. Export is the selected people, in order, with roles and tags ───────
r = bulk('export', [IDS[1], IDS[0]], follow=False)
assert r.status_code == 200, r.status_code
assert 'text/csv' in r.headers['Content-Type']
assert 'attachment' in r.headers['Content-Disposition']
rows = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
assert rows[0][0] == 'First name' and 'Roles' in rows[0] and 'Tags' in rows[0]
assert 'Job title' not in rows[0], 'the export still offers a field that was removed'
assert len(rows) == 3, f'{len(rows) - 1} data rows for a selection of two'
assert rows[1][1] == 'Okelo' and rows[2][1] == 'Smith', 'the order was not kept'
smith = dict(zip(rows[0], rows[2]))
assert smith['Organisation'] == 'Hurlingham Holdings Ltd'
assert 'Prospective Tenant' in smith['Roles']
assert 'Key account' in smith['Tags']
assert smith['Email'] == 'john@example.com'
print('10. the export holds exactly the selected contacts, with roles and tags')


# ─── 11. Filtering by tag returns only whole-tag matches ────────────────────
cl.post(f'/contacts/{IDS[4]}/edit',
        data={'first_name': 'Priya', 'last_name': 'Shah', 'tags': 'Key'},
        follow_redirects=True)
page = cl.get('/contacts?tag=Key').get_data(as_text=True)
assert 'Shah' in page, 'the exact tag did not match'
assert 'Smith' not in page, '"Key" matched "Key account" as a substring'
page = cl.get('/contacts?tag=Key+account').get_data(as_text=True)
assert 'Smith' in page and 'Shah' not in page
print('11. a tag filter matches whole tags, not substrings')


# ─── 12. Two tags means both, not either ────────────────────────────────────
page = cl.get('/contacts?tag=Key+account&tag=Christmas+card').get_data(as_text=True)
assert 'Smith' in page
assert 'Okelo' not in page, 'two tags matched somebody holding only one'
print('12. selecting two tags asks for both of them')


# ─── 13. Tags do not become roles, and roles do not become tags ─────────────
with A.app.app_context():
    john = A.Contact.query.get(IDS[0])
    assert 'Key account' not in A.role_names(john), 'a tag became a role'
    assert 'Prospective Tenant' not in A.contact_tags(john), 'a role became a tag'
    assert 'Key account' not in A.CONTACT_ROLE_NAMES
print('13. tags and roles stay separate')


# ─── 14. A viewer cannot tag or assign, and the bar is not offered ──────────
look = A.app.test_client()
look.post('/login', data={'username': 'looker', 'password': 'pw'},
          follow_redirects=True)
before = tags_of(IDS[3])
for data in ({'action': 'tag', 'tag': 'Sneaked in'},
             {'action': 'role', 'role': 'Buyer'},
             {'action': 'export'}):
    data['ids'] = [str(IDS[3])]
    r = look.post('/contacts/bulk', data=data)
    assert r.status_code in (302, 403), (data['action'], r.status_code)
    assert 'text/csv' not in r.headers.get('Content-Type', ''), \
        'a viewer exported the contact book'
assert tags_of(IDS[3]) == before, 'a viewer changed a record'
with A.app.app_context():
    assert A.ContactRole.query.filter_by(contact_id=IDS[3]).count() == 0
print('14. a viewer cannot tag, assign or export in bulk')


# ─── 15. The list offers selection and the bar, with valid markup ───────────
page = cl.get('/contacts').get_data(as_text=True)
for expected in ('name="ids"', 'id="bulk-form"', 'value="export"',
                 'value="tag"', 'value="role"', 'bulk-select.js'):
    assert expected in page, f'{expected} is missing from the list'
assert 'Key account' in page, 'tags are not shown on the cards'


class Shape(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
            'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__()
        self.stack, self.depth, self.bad = [], 0, []

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        if tag == 'form':
            self.depth += 1
            if self.depth > 1:
                self.bad.append('nested form')
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if tag == 'form':
            self.depth -= 1
        if tag in self.stack:
            while self.stack.pop() != tag:
                pass


for url in ('/contacts', '/contacts?tag=Key+account', f'/contacts/{IDS[0]}'):
    p = Shape()
    p.feed(cl.get(url).get_data(as_text=True))
    assert not p.bad, f'{url}: {p.bad[0]}'
    assert not p.stack, f'{url} left {p.stack[:3]} unclosed'
print('15. the list offers selection and the bulk bar, in valid, unnested markup')


# ─── 16. Every bulk action is audited ───────────────────────────────────────
with A.app.app_context():
    actions = {a.action for a in A.AuditLog.query.all()}
for wanted in ('contacts-tagged', 'roles-assigned', 'contacts-exported'):
    assert wanted in actions, f'{wanted} was not audited'
print('16. tagging, assigning and exporting are all audited')

print('\nCONTACT TAGS AND BULK ACTIONS: ALL CHECKS PASSED')
