"""An appointment names itself, and knows who it is with.

The form asked for a title and for "Who", which was the member of staff. So
every viewing was a title typed out by hand, and the person being met — the
one fact that makes an appointment worth having — was not recorded at all
unless somebody went looking for the contact box.

What an appointment is decides who it is with: a viewing on a letting is with
a tenant and one on a sale with a buyer; a valuation is with the landlord or
the seller. The title is built from the type, the contact and the property,
and the location is the property's address until somebody says otherwise.
"""
import os
import re
import sys
import tempfile
from datetime import date, datetime, timedelta

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/diary.db'
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
    A._migrate_diary_tables()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          email='bc@cowanandrutter.co.uk', active=True,
                          can_earn_fees=True,
                          password_hash=generate_password_hash('pw')))
    db.session.commit()

    prop = A.Property(address='1 Stanley Bridge Studios, London', postcode='SW6 2AA',
                      property_type='Office', size=1200)
    db.session.add(prop); db.session.commit()
    PROP = prop.id

    letting = A.Project(name='Stanley Bridge', property_id=prop.id, status='Active',
                        instruction_type=A.INSTRUCTION_TO_LET)
    sale = A.Project(name='Stanley Bridge sale', property_id=prop.id, status='Active',
                     instruction_type=A.INSTRUCTION_FOR_SALE)
    db.session.add_all([letting, sale]); db.session.commit()
    LETTING, SALE = letting.id, sale.id

    richard = A.Contact(first_name='Richard', last_name='Hockney',
                        contact_type='Tenant', email='richard@example.co.uk')
    john = A.Contact(first_name='John', last_name='Smith', contact_type='Landlord')
    db.session.add_all([richard, john]); db.session.commit()
    RICHARD, JOHN = richard.id, john.id

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)

SOON = datetime.now().replace(second=0, microsecond=0) + timedelta(days=1)
LATER = SOON + timedelta(hours=1)


def make(**extra):
    data = {'start': SOON.strftime('%Y-%m-%dT%H:%M'),
            'end': LATER.strftime('%Y-%m-%dT%H:%M'),
            'property_id': str(PROP)}
    data.update(extra)
    r = cl.post('/diary/event/new', data=data, follow_redirects=True)
    assert r.status_code == 200, r.status_code
    with A.app.app_context():
        return A.DiaryEvent.query.order_by(A.DiaryEvent.id.desc()).first()


# ─── 1. The title is built, never typed ─────────────────────────────────────
ev = make(event_type='viewing', contact_id=str(RICHARD), project_id=str(LETTING))
assert ev.title == 'Viewing – Richard Hockney – 1 Stanley Bridge Studios', ev.title
print('1.', ev.title)

ev = make(event_type='valuation', contact_id=str(JOHN))
assert ev.title == 'Valuation – John Smith – 1 Stanley Bridge Studios', ev.title
print('  ', ev.title)

ev = make(event_type='landlord_meeting', contact_id=str(JOHN))
assert ev.title == 'Landlord Meeting – John Smith – 1 Stanley Bridge Studios', ev.title
print('  ', ev.title)

# Nothing is left blank where there is no contact.
ev = make(event_type='inspection')
assert ev.title == 'Inspection – 1 Stanley Bridge Studios', ev.title
print('   and an appointment with nobody still names itself')

# The form no longer asks for one.
html = cl.get('/diary').get_data(as_text=True)
assert 'name="title"' not in html, 'the new-appointment form still asks for a title'
print('2. the form does not ask for a title')


# ─── 3. Who it is with follows what it is ───────────────────────────────────
with A.app.app_context():
    let_p = A.Project.query.get(LETTING)
    sale_p = A.Project.query.get(SALE)
    assert A.appointment_contact_kinds('viewing', let_p) == ('Tenant',)
    assert A.appointment_contact_kinds('viewing', sale_p) == ('Buyer',)
    assert A.appointment_contact_kinds('viewing', None) == ('Tenant', 'Buyer')
    assert A.appointment_contact_kinds('valuation') == ('Landlord', 'Seller')
    assert A.appointment_contact_kinds('landlord_meeting') == ('Landlord',)
    assert A.appointment_contact_kinds('tenant_meeting') == ('Tenant',)
    assert A.appointment_contact_kinds('buyer_meeting') == ('Buyer',)
    assert A.appointment_contact_kinds('call') == ()
    assert A.appointment_contact_label('valuation') == 'Landlord or Seller'
print('3. a viewing To Let is with a Tenant, For Sale with a Buyer, '
      'a valuation with a Landlord or Seller')

# The generic "Who" box — the member of staff — is off the form, and a
# contact can be chosen instead.
assert 'name="contact_id"' in html, 'the form cannot record who it is with'
assert not re.search(r'<input[^>]*name="owner"', html), \
    'the form still asks "Who" as a free-text box'
print('   the form asks for the contact, not for "Who"')


# ─── 4. Everything stays linked to the records it came from ─────────────────
ev = make(event_type='viewing', contact_id=str(RICHARD), project_id=str(LETTING))
assert ev.contact_id == RICHARD and ev.project_id == LETTING and ev.property_id == PROP
with A.app.app_context():
    again = A.DiaryEvent.query.get(ev.id)
    assert again.contact.full_name == 'Richard Hockney'
    assert again.project.name == 'Stanley Bridge'
    assert again.linked_prop.address.startswith('1 Stanley Bridge Studios')
print('4. the appointment, the contact, the instruction and the property are linked')


# ─── 5. The location is the property's, unless it is not ────────────────────
assert ev.location == '1 Stanley Bridge Studios, London, SW6 2AA', ev.location
elsewhere = make(event_type='meeting', contact_id=str(JOHN),
                 location='Our office, 2 Effie Road')
assert elsewhere.location == 'Our office, 2 Effie Road', elsewhere.location
print('5. the location is the property\'s address, or wherever is typed instead')

# Clearing it on the record puts the property's address back.
r = cl.post(f'/diary/event/{elsewhere.id}', data={
    'property_id': str(PROP), 'event_type': 'meeting', 'location': '',
    'start': SOON.strftime('%Y-%m-%dT%H:%M'),
    'end': LATER.strftime('%Y-%m-%dT%H:%M')}, follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    back = A.DiaryEvent.query.get(elsewhere.id)
    assert back.location == '1 Stanley Bridge Studios, London, SW6 2AA', back.location
print('   clearing it goes back to the property')


# ─── 6. The title follows a change of type, contact or property ─────────────
r = cl.post(f'/diary/event/{ev.id}', data={
    'property_id': str(PROP), 'event_type': 'buyer_meeting',
    'contact_id': str(JOHN),
    'start': SOON.strftime('%Y-%m-%dT%H:%M'),
    'end': LATER.strftime('%Y-%m-%dT%H:%M')}, follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    moved = A.DiaryEvent.query.get(ev.id)
    assert moved.title == 'Buyer Meeting – John Smith – 1 Stanley Bridge Studios', \
        moved.title
print('6. changing the type or the contact rewrites the title')


# ─── 7. The confirmation is written out, and reads like a person wrote it ───
with A.app.app_context():
    ev = A.DiaryEvent.query.filter_by(contact_id=RICHARD).first()
    draft = A.appointment_email(ev)

assert draft['to'] == 'richard@example.co.uk', draft['to']
assert draft['body'].startswith('Hi Richard,'), draft['body'][:40]
assert 'Just confirming our ' in draft['body']
assert '1 Stanley Bridge Studios' in draft['body']
assert 'The address is:' in draft['body']
assert 'I look forward to seeing you there.' in draft['body']
assert draft['body'].rstrip().endswith('Benjamin'), draft['body'][-40:]
assert 'google.com/maps' in draft['body'], 'no directions link'
assert 'Stanley' in draft['subject'], draft['subject']
print('7. the confirmation names the place, the day and the time, and links a map')
print()
for line in draft['body'].splitlines():
    print('   |', line)
print()

# It is shown on the page with a send button, not sent by opening the page.
html = cl.get(f'/diary/event/{ev.id}').get_data(as_text=True)
assert 'Email details' in html, 'the appointment does not offer to confirm it'
assert 'Send confirmation' in html
assert 'richard@example.co.uk' in html
print('8. the appointment page shows it, and sends only when told to')

# With no email address there is nowhere to send it, and it says so.
with A.app.app_context():
    nomail = A.DiaryEvent.query.filter_by(contact_id=JOHN).first()
html = cl.get(f'/diary/event/{nomail.id}').get_data(as_text=True)
assert 'no email address on their record' in html
print('   and says so when the contact has no address')

print('\nDIARY APPOINTMENTS: ALL CHECKS PASSED')
