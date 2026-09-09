"""What a contact is: Landlord, Tenant, Buyer or Seller.

The type is the headline — the four sides of the two deals this office does.
The roles on the record are the full picture, because somebody can be a
Landlord on one building and a Buyer on another at the same time. The two are
kept in step: a contact typed Landlord always holds the Landlord role.

Job Title is gone. What somebody's business card says is not what they are to
the agency, and the field it occupied now shows their role.
"""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/types.db'
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
    org = A.Organisation(name='Hurlingham Holdings Ltd')
    db.session.add(org)
    db.session.commit()
    # Somebody filed under the labels used before this change.
    legacy = A.Contact(first_name='Margaret', last_name='Hale',
                       contact_type='Client', job_title='Estates Director')
    other = A.Contact(first_name='Iwan', last_name='Rheon',
                      contact_type='Prospective Tenant')
    db.session.add_all([legacy, other])
    db.session.commit()
    LEGACY, OTHER, ORG = legacy.id, other.id, org.id

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)
FOUR = ['Landlord', 'Tenant', 'Buyer', 'Seller']


def options_in(body, field):
    import re
    block = body[body.index(f'name="{field}"'):]
    block = block[:block.index('</select>')]
    return {t.strip() for t in re.findall(r'<option[^>]*>([^<]*)<', block)}


def make(**extra):
    data = {'first_name': 'Test', 'last_name': 'Person'}
    data.update(extra)
    r = cl.post('/contacts/new', data=data, follow_redirects=True)
    assert r.status_code == 200, r.status_code
    with A.app.app_context():
        return A.Contact.query.order_by(A.Contact.id.desc()).first()


# ─── 1. The four types, and nothing wider ───────────────────────────────────
assert A.CONTACT_TYPES == FOUR, A.CONTACT_TYPES
new_page = cl.get('/contacts/new').get_data(as_text=True)
offered = options_in(new_page, 'contact_type')
for t in FOUR:
    assert t in offered, f'{t} is not offered when adding a contact'
assert offered - set(FOUR) <= {'&mdash; Select &mdash;'}, offered
print('1. adding a contact offers Landlord, Tenant, Buyer and Seller only')


# ─── 2. The old free-for-all list is gone ───────────────────────────────────
for gone in ('Prospect', 'Solicitor', 'Developer', 'Surveyor', 'Other',
             'Prospective Buyer', 'Agent'):
    assert gone not in offered, f'{gone} is still offered as a type'
print('2. the twelve-option list it used to offer is gone')


# ─── 3. Job Title is off both pages ─────────────────────────────────────────
edit_page = cl.get(f'/contacts/{LEGACY}').get_data(as_text=True)
for label, body in (('add', new_page), ('edit', edit_page)):
    assert 'name="job_title"' not in body, f'Job Title is still on the {label} page'
    assert 'Job Title' not in body and 'Job title' not in body, \
        f'the {label} page still says Job Title'
print('3. Job Title is gone from both the add and edit pages')


# ─── 4. Contact Role took its place ─────────────────────────────────────────
assert 'Contact Role' in new_page, 'the add page has no Contact Role field'
assert 'name="role"' in new_page, 'the add page cannot record a role'
assert 'Contact role' in edit_page, 'the record does not show a contact role'
print('4. Contact Role stands where Job Title was, on both pages')


# ─── 5. The type recorded on a new contact is real, and becomes a role ──────
c = make(contact_type='Landlord')
with A.app.app_context():
    made = A.Contact.query.get(c.id)
    assert made.contact_type == 'Landlord'
    assert 'Landlord' in A.role_names(made), \
        'a new Landlord holds no Landlord role'
print('5. a new contact typed Landlord is recorded as one, and holds the role')


# ─── 6. A second role can be given at the same time ─────────────────────────
c = make(contact_type='Landlord', role='Buyer')
with A.app.app_context():
    names = A.role_names(A.Contact.query.get(c.id))
assert 'Landlord' in names and 'Buyer' in names, names
print('6. a contact can be added as a Landlord who is also buying')


# ─── 7. The same role twice is not recorded twice ───────────────────────────
c = make(contact_type='Tenant', role='Tenant')
with A.app.app_context():
    n = A.ContactRole.query.filter_by(contact_id=c.id, role='Tenant').count()
assert n == 1, f'{n} Tenant roles on one new contact'
print('7. naming the same thing as type and role records it once')


# ─── 8. An invented type is refused on the way in ───────────────────────────
c = make(contact_type='Supreme Overlord')
with A.app.app_context():
    assert A.Contact.query.get(c.id).contact_type is None, \
        'an invented type was saved on a new contact'
    assert A.ContactRole.query.filter_by(contact_id=c.id).count() == 0
print('8. an invented type is refused rather than stored')


# ─── 9. An older label is kept, and offered only to whoever holds it ────────
body = cl.get(f'/contacts/{LEGACY}').get_data(as_text=True)
assert 'Client' in options_in(body, 'contact_type'), \
    'a contact filed as Client lost their type'
assert 'Client' not in options_in(new_page, 'contact_type'), \
    'Client is still offered to new contacts'
cl.post(f'/contacts/{LEGACY}/edit',
        data={'first_name': 'Margaret', 'last_name': 'Hale',
              'mobile': '07700 900999'}, follow_redirects=True)
with A.app.app_context():
    assert A.Contact.query.get(LEGACY).contact_type == 'Client', \
        'saving the record silently reclassified them'
print('9. an older label is kept and offered on that record, and nowhere else')


# ─── 10. Job title data is kept, not destroyed ──────────────────────────────
# The field is off the forms. That is not a reason to throw away what is in it.
with A.app.app_context():
    assert A.Contact.query.get(LEGACY).job_title == 'Estates Director', \
        'removing the field deleted what was stored in it'
print('10. what was already in Job Title is kept, just no longer shown')


# ─── 11. One sidebar page per type ──────────────────────────────────────────
nav = cl.get('/contacts').get_data(as_text=True)
for t in FOUR:
    assert f'type={t}' in nav, f'there is no {t} page in the sidebar'
assert 'type=Client' not in nav, 'the old Clients page is still in the sidebar'
print('11. the sidebar has one page per type')


# ─── 12. Each page gathers the older labels that meant the same ─────────────
page = cl.get('/contacts?type=Landlord').get_data(as_text=True)
assert 'Hale' in page, 'a contact filed as Client vanished from Landlords'
page = cl.get('/contacts?type=Tenant').get_data(as_text=True)
assert 'Rheon' in page, 'a Prospective Tenant vanished from Tenants'
assert 'Hale' not in page, 'a Landlord is showing under Tenants'
page = cl.get('/contacts?type=Buyer').get_data(as_text=True)
assert 'Hale' not in page and 'Rheon' not in page
print('12. each page gathers the older labels that meant the same thing')


# ─── 13. The heading matches the page ───────────────────────────────────────
for t in FOUR:
    page = cl.get(f'/contacts?type={t}').get_data(as_text=True)
    assert f'<h2>{t}s</h2>' in page, f'the {t} page is not headed {t}s'
assert '<h2>All Contacts</h2>' in cl.get('/contacts').get_data(as_text=True)
print('13. each page is headed by the type it shows')


# ─── 14. The same words everywhere ──────────────────────────────────────────
# A person's subtitle across the CRM is what they are to us, not their job.
import glob
for path in glob.glob(f'{ROOT}/templates/**/*.html', recursive=True):
    body = open(path).read()
    assert 'job_title' not in body, \
        f'{os.path.basename(path)} still reads job_title'
    assert 'Job Title' not in body and 'Job title' not in body, \
        f'{os.path.basename(path)} still says Job Title'
print('14. no page in the CRM still asks for or shows a job title')

print('\nCONTACT TYPE: ALL CHECKS PASSED')
