"""One Client Contact Details box, read from the client's own record.

And nothing about the client — name, company, telephone, email or address —
on a marketing document. Particulars carry the property, the company and the
fee earner, and nobody else.
"""
import io, os, re, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)
from app import (app, db, User, Property, Project, Contact, Organisation,
                 Listing, ListingPhoto, AuditLog, particulars_data)
import pymupdf
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash
from PIL import Image

CLIENT = 'Phillipa Smith'
COMPANY = 'Example Property Limited'
CLIENT_PHONE = '020 7946 0100'
CLIENT_MOBILE = '07700 900 200'
CLIENT_EMAIL = 'p.smith@exampleproperty.co.uk'
CLIENT_ADDRESS = '18 Cadogan Square, London SW1X 0HT'


def shot():
    b = io.BytesIO()
    Image.new('RGB', (900, 700), (150, 160, 175)).save(b, 'JPEG')
    return b.getvalue()


with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw'),
                        role='admin', full_name='Benjamin Cowan',
                        email='Bc@cowanandrutter.co.uk'))
    org = Organisation(name=COMPANY, status='Active', address=CLIENT_ADDRESS)
    db.session.add(org); db.session.commit()
    who = Contact(first_name='Phillipa', last_name='Smith', organisation_id=org.id,
                  job_title='Estates Director', phone=CLIENT_PHONE,
                  mobile=CLIENT_MOBILE, email=CLIENT_EMAIL)
    solo = Contact(first_name='Terence', last_name='Vole', phone='01732 555 999')
    db.session.add_all([who, solo]); db.session.commit()
    prop = Property(address='Unit 2, Marlin House', postcode='SW6 3BN',
                    property_type='Office', size=237)
    db.session.add(prop); db.session.commit()
    proj = Project(name='Marlin', property_id=prop.id, fee_earner_id=1,
                   instruction_type='To Let – Available',
                   client_contact_id=who.id,
                   client_phone='OLD PHONE', client_mobile='OLD MOBILE',
                   client_email='old@example.com', client='Typed client name')
    db.session.add(proj); db.session.commit()
    lst = Listing(project_id=proj.id, property_id=prop.id, listing_status='available',
                  listing_price=45000, listing_price_unit='pa',
                  blurb='A bright fitted office.', strapline='OFFICE | TO LET | SW6')
    db.session.add(lst); db.session.commit()
    db.session.add(ListingPhoto(listing_id=lst.id, file_data=shot(), filename='a.jpg',
                                file_mime='image/jpeg', file_size=1, sort_order=0))
    db.session.commit()
    PROJ, WHO, SOLO, ORG, LST = proj.id, who.id, solo.id, org.id, lst.id
    PHOTO = ListingPhoto.query.first().id

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def page(url):
    r = cl.get(url)
    assert r.status_code == 200, f'{url} returned {r.status_code}'
    return r.get_data(as_text=True)


OVERVIEW = page(f'/projects/{PROJ}')


# ─── 1. Only one box remains ────────────────────────────────────────────────
assert OVERVIEW.count('Client Contact Details') == 1, \
    f"{OVERVIEW.count('Client Contact Details')} Client Contact Details boxes"
assert '>Client details<' not in OVERVIEW, 'the duplicate box is still there'
assert 'clientcard' not in OVERVIEW, 'the duplicate panel markup remains'
src = open(f'{ROOT}/templates/projects/detail.html').read()
assert 'client_details' not in src, 'the instruction still renders the second box'
print('1. exactly one Client Contact Details box on the instruction')


# ─── 2. Nothing empty was left where it was ─────────────────────────────────
assert '<div class="box box--grid"></div>' not in OVERVIEW
assert 'box-head"></div>' not in OVERVIEW
assert '<div class="fcell"></div>' not in OVERVIEW, 'an empty cell was left'
assert OVERVIEW.count('rec-cols-proj') == src.count('rec-cols-proj'), \
    'a column was left behind'
print('2. no empty box, heading or column was left behind')


# ─── 3. It shows the linked client, from their own record ───────────────────
assert f'{CLIENT} ({COMPANY})' in OVERVIEW, 'the name and company are not shown'
assert 'Estates Director' in OVERVIEW, 'the job title is missing'
assert CLIENT_PHONE in OVERVIEW and CLIENT_MOBILE in OVERVIEW
assert CLIENT_EMAIL in OVERVIEW
print('3. name, company, job title, telephone, mobile and email all shown')


# ─── 4. Clickable ───────────────────────────────────────────────────────────
assert f'tel:{CLIENT_PHONE.replace(" ", "")}' in OVERVIEW, 'the telephone is not clickable'
assert f'tel:{CLIENT_MOBILE.replace(" ", "")}' in OVERVIEW, 'the mobile is not clickable'
assert f'mailto:{CLIENT_EMAIL}' in OVERVIEW, 'the email is not clickable'
assert f'/contacts/{WHO}' in OVERVIEW, 'the client record cannot be opened'
print('4. telephone numbers and the email address are all clickable')


# ─── 5. Changing the client refreshes the box, with nothing re-typed ────────
cl.post(f'/projects/{PROJ}/edit', data={'name': 'Marlin', 'client_contact_id': SOLO},
        follow_redirects=True)
after = page(f'/projects/{PROJ}')
assert 'Terence Vole' in after, 'the box did not follow the new client'
assert CLIENT_EMAIL not in after, "the previous client's details are still shown"
assert '01732 555 999' in after, "the new client's telephone was not picked up"
assert '()' not in after.split('Client Contact Details')[1][:900], \
    'a client with no company showed empty brackets'
cl.post(f'/projects/{PROJ}/edit', data={'name': 'Marlin', 'client_contact_id': WHO},
        follow_redirects=True)
print('5. changing the client refreshes the box from their record')


# ─── 6. A change on the client record shows here ────────────────────────────
with app.app_context():
    Contact.query.get(WHO).mobile = '07700 900 999'
    Organisation.query.get(ORG).name = 'Example Property (Holdings) Limited'
    db.session.commit()
fresh = page(f'/projects/{PROJ}')
assert '07700 900 999' in fresh, 'the instruction is showing a stale mobile'
assert 'Example Property (Holdings) Limited' in fresh, 'a stale company name'
with app.app_context():
    Contact.query.get(WHO).mobile = CLIENT_MOBILE
    Organisation.query.get(ORG).name = COMPANY
    db.session.commit()
print('6. updating the client record updates the instruction')


# ─── 7. The selector is still in Instruction Details ────────────────────────
assert 'data-client-picker' in OVERVIEW, 'the client selector was removed'
assert 'name="client_contact_id"' in OVERVIEW
i = OVERVIEW.index('Instruction Detail')
j = OVERVIEW.index('data-client-picker')
assert j > i, 'the selector is no longer in Instruction Details'
print('7. the client can still be chosen and changed from Instruction Details')


# ─── 8. Nothing was deleted ─────────────────────────────────────────────────
with app.app_context():
    p = Project.query.get(PROJ)
    assert p.client_contact_id == WHO, 'the client relationship was removed'
    assert p.client_phone == 'OLD PHONE', 'the project lost its own recorded phone'
    assert p.client_mobile == 'OLD MOBILE' and p.client_email == 'old@example.com'
    assert p.client == 'Typed client name', 'the typed client name was removed'
    assert Contact.query.get(WHO) is not None and Organisation.query.get(ORG) is not None
    assert Contact.query.count() >= 2
print('8. every client, company and recorded detail is still in the database')


# ─── 9. An instruction with no linked client keeps its own fields ───────────
with app.app_context():
    lone = Project(name='Unlinked', property_id=Property.query.first().id,
                   fee_earner_id=1, client_phone='01732 000 111',
                   client_email='someone@example.com')
    db.session.add(lone); db.session.commit()
    LONE = lone.id
body = page(f'/projects/{LONE}')
assert 'name="client_phone"' in body, 'an unlinked instruction lost its own fields'
assert '01732 000 111' in body
assert 'Linking a client' in body, 'nothing explains how to link one'
print('9. an instruction with no linked client keeps its own editable fields')


# ─── 10. Permissions and the audit trail ────────────────────────────────────
with app.app_context():
    before = AuditLog.query.filter_by(entity='Project').count()
cl.post(f'/projects/{PROJ}/edit', data={'name': 'Marlin', 'client_contact_id': SOLO},
        follow_redirects=True)
with app.app_context():
    assert AuditLog.query.filter_by(entity='Project').count() > before, \
        'changing the client was not recorded'
    Project.query.get(PROJ).client_contact_id = WHO
    db.session.commit()
app_src = open(f'{ROOT}/app.py').read()
assert "@requires('edit')" in app_src and "@requires('create')" in app_src
print('10. permissions and audit logging are unchanged')


# ─── 11. No client detail reaches the particulars ───────────────────────────
with app.app_context():
    data = particulars_data(Project.query.get(PROJ))
private = str(data)
for secret in (CLIENT, COMPANY, CLIENT_PHONE, CLIENT_MOBILE, CLIENT_EMAIL,
               CLIENT_ADDRESS, 'Typed client name', 'Estates Director'):
    assert secret not in private, f'{secret!r} is in the particulars data'
print('11. no client name, company or contact detail is gathered for a brochure')


# ─── 12. Nor in the document itself, either format ──────────────────────────
for pages in (2, 4):
    r = cl.post(f'/projects/{PROJ}/particulars/preview',
                data={'pages': str(pages), 'photo_ids': [PHOTO]})
    assert r.status_code == 200
    text = re.sub(r'\s+', ' ', ' '.join(
        p.get_text() for p in pymupdf.open(stream=r.get_data(), filetype='pdf')))
    for secret in (CLIENT, 'Phillipa', COMPANY, CLIENT_PHONE, CLIENT_MOBILE,
                   CLIENT_EMAIL, CLIENT_ADDRESS, 'Typed client name'):
        assert secret not in text, f'{secret!r} appears on the {pages}-page particulars'
    # What should be there, is.
    assert 'Benjamin Cowan' in text, 'the fee earner is missing'
    assert 'Bc@cowanandrutter.co.uk' in text and '020 7349 6666' in text
    assert 'cowanandrutter.co.uk' in text
    assert 'Marlin House' in text or 'OFFICE' in text, 'the property is missing'
print('12. neither brochure carries a client detail; both carry the fee earner')


# ─── 13. A landlord or vendor name is kept out too ──────────────────────────
with app.app_context():
    p = Project.query.get(PROJ)
    p.landlord_name = 'Lord Ashcombe of Fulham'
    db.session.commit()
r = cl.post(f'/projects/{PROJ}/particulars/preview',
            data={'pages': '4', 'photo_ids': [PHOTO]})
text = ' '.join(p.get_text() for p in pymupdf.open(stream=r.get_data(), filetype='pdf'))
assert 'Ashcombe' not in text, 'the landlord appears on the particulars'
print('13. the landlord or vendor is never named on a brochure')

print('\nCLIENT CONTACT DETAILS: ALL CHECKS PASSED')
