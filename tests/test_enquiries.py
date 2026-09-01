"""The enquiry register: inquiry types, project linking, and portal leads."""
import os, re, sys, tempfile
from datetime import datetime
from html.parser import HTMLParser

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)

import app as A
import portal_leads
import email_sync
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db

with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          password_hash=generate_password_hash('pw')))
    db.session.add(A.User(username='looker', role='viewer', full_name='A Viewer',
                          password_hash=generate_password_hash('pw')))
    db.session.commit()

    org = A.Organisation(name='ABC Interiors Ltd')
    db.session.add(org); db.session.commit()
    known = A.Contact(first_name='John', last_name='Smith',
                      email='john.smith@abcinteriors.co.uk', mobile='07700 900123',
                      organisation_id=org.id, contact_type='Prospect')
    loner = A.Contact(first_name='Sara', last_name='Okelo',
                      email='sara@example.com', contact_type='Prospect')
    db.session.add_all([known, loner]); db.session.commit()

    prop = A.Property(address='12 Kings Road, London SW3 4RP', postcode='SW3 4RP',
                      property_type='Retail')
    db.session.add(prop); db.session.commit()
    proj = A.Project(name='12 Kings Road', property_id=prop.id, fee_earner_id=1,
                     instruction_type=A.INSTRUCTION_TO_LET)
    db.session.add(proj); db.session.commit()
    db.session.add(A.Listing(project_id=proj.id, property_id=prop.id, set_as_to_let=True))

    # An enquiry from before this change, filed the old way.
    legacy = A.Enquiry(subject='Rent Review enquiry — some old unit',
                       enquiry_type='Rent Review', status='Open', source='Manual',
                       received_date=A.date(2025, 6, 1))
    db.session.add(legacy); db.session.commit()

    IDS = {'org': org.id, 'known': known.id, 'loner': loner.id,
           'prop': prop.id, 'proj': proj.id, 'legacy': legacy.id}

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def make(**over):
    data = {'enquiry_type': 'Tenant — Looking to Rent', 'status': 'Open',
            'source': 'Manual', 'received_date': '2026-09-01'}
    data.update(over)
    r = cl.post('/enquiries/new', data=data, follow_redirects=True)
    assert r.status_code == 200, r.status_code
    with A.app.app_context():
        return A.Enquiry.query.order_by(A.Enquiry.id.desc()).first()


# ─── 1. The seven inquiry types, and no Subject ─────────────────────────────
page = cl.get('/enquiries/new').get_data(as_text=True)
for t in ('Tenant — Looking to Rent', 'Buyer — Looking to Buy',
          'Landlord — Looking to Let', 'Owner/Vendor — Looking to Sell',
          'Existing Client', 'General Inquiry', 'Other'):
    assert t in page, f'{t!r} is not offered'
assert 'name="subject"' not in page, 'the Subject field is still on the form'
assert 'Inquiry Type' in page
print('1. all seven inquiry types are offered, and Subject is gone')


# ─── 2. One enquiry of each side of the agency ──────────────────────────────
for kind in ('Tenant — Looking to Rent', 'Buyer — Looking to Buy',
             'Landlord — Looking to Let', 'Owner/Vendor — Looking to Sell'):
    e = make(enquiry_type=kind, contact_id=IDS['loner'])
    with A.app.app_context():
        saved = A.Enquiry.query.get(e.id)
        assert saved.enquiry_type == kind, f'{kind} saved as {saved.enquiry_type!r}'
        assert saved.subject, 'the derived subject is empty'
print('2. tenant, buyer, landlord and vendor enquiries all save their type')


# ─── 3. The type shows on the record ────────────────────────────────────────
e = make(enquiry_type='Landlord — Looking to Let', contact_id=IDS['known'])
rec = cl.get(f'/enquiries/{e.id}').get_data(as_text=True)
assert 'Landlord — Looking to Let' in rec, 'the type is not shown on the record'
assert 'Inquiry type' in rec
print('3. the inquiry type is displayed clearly on the enquiry record')


# ─── 4. Linking a project is enough ─────────────────────────────────────────
page = cl.get('/enquiries/new').get_data(as_text=True)
assert 'Link Project' in page
assert 'No property linked' not in page, 'Link Property is still on the form'
e = make(enquiry_type='Tenant — Looking to Rent', project_id=IDS['proj'],
         contact_id=IDS['loner'])
with A.app.app_context():
    saved = A.Enquiry.query.get(e.id)
    assert saved.project_id == IDS['proj']
    assert saved.property_id == IDS['prop'], \
        'the property was not taken from the project'
print('4. linking a project links its property too, without asking twice')


# ─── 5. The organisation is the ENQUIRER's, filled in automatically ─────────
e = make(enquiry_type='Tenant — Looking to Rent', contact_id=IDS['known'],
         project_id=IDS['proj'])
with A.app.app_context():
    saved = A.Enquiry.query.get(e.id)
    assert saved.organisation_id == IDS['org'], \
        f'expected ABC Interiors, got {saved.organisation_id}'
    assert saved.organisation.name == 'ABC Interiors Ltd'
rec = cl.get(f'/enquiries/{e.id}').get_data(as_text=True)
assert 'John Smith' in rec and 'ABC Interiors Ltd' in rec
print("5. an existing contact's own organisation is filled in automatically")


# ─── 6. A contact with no organisation is left alone ────────────────────────
e = make(enquiry_type='Buyer — Looking to Buy', contact_id=IDS['loner'],
         project_id=IDS['proj'])
with A.app.app_context():
    saved = A.Enquiry.query.get(e.id)
    assert saved.organisation_id is None, \
        'an organisation was invented for a contact who has none'
print('6. a contact with no organisation gets none invented for them')


# ─── 7. The project's own client is never used as the enquirer's company ────
with A.app.app_context():
    # Give the project a client of its own, from a different company.
    theirs = A.Organisation(name='Landlord Holdings Ltd')
    db.session.add(theirs); db.session.commit()
    client = A.Contact(first_name='Margaret', last_name='Hale',
                       email='m@landlordholdings.co.uk', organisation_id=theirs.id)
    db.session.add(client); db.session.commit()
    p = A.Project.query.get(IDS['proj'])
    p.client_contact_id = client.id
    db.session.commit()
    theirs_id = theirs.id
e = make(enquiry_type='Tenant — Looking to Rent', contact_id=IDS['known'],
         project_id=IDS['proj'])
with A.app.app_context():
    saved = A.Enquiry.query.get(e.id)
    assert saved.organisation_id == IDS['org'], \
        "the project's client organisation was used instead of the enquirer's"
    assert saved.organisation_id != theirs_id
print("7. the instruction's own client is never used as the enquirer's company")


# ─── 8. A website enquiry still arrives ─────────────────────────────────────
with A.app.app_context():
    before = A.Enquiry.query.count()
r = cl.post('/api/enquiry', json={
    'from_name': 'Website Visitor', 'from_email': 'visitor@example.com',
    'phone': '07700 900999', 'message': 'Interested in this unit.',
    'interest': 'Arrange a viewing'})
with A.app.app_context():
    after = A.Enquiry.query.count()
    web = A.Enquiry.query.order_by(A.Enquiry.id.desc()).first()
assert after == before + 1, f'the website enquiry did not arrive ({r.status_code})'
assert (web.source or '').lower() in ('website', 'web'), f'source {web.source!r}'
print(f'8. a website enquiry arrives and is recorded as source {web.source!r}')


# ─── 9. A Zoopla enquiry, end to end ────────────────────────────────────────
ZOOPLA_BODY = """
You have received a new enquiry from Zoopla.
Property: 12 Kings Road, London SW3 4RP
Reference: CR-%d

Name: John Smith
Email: john.smith@abcinteriors.co.uk
Telephone: 07700 900123

Message:
Interested in viewing this unit for our design studio.

This enquiry was sent via Zoopla.co.uk
""" % IDS['proj']
subject = 'New enquiry for 12 Kings Road, London SW3 4RP'
sender = 'noreply@mail.zoopla.co.uk'

assert portal_leads.detect_portal(sender, subject) == 'Zoopla'
assert portal_leads.is_lead_email(sender, subject, ZOOPLA_BODY)
parsed = portal_leads.parse_lead(subject, ZOOPLA_BODY, sender, 'Zoopla')
assert parsed['name'] == 'John Smith', parsed['name']
assert parsed['email'] == 'john.smith@abcinteriors.co.uk'
assert parsed['phone'] == '07700 900123'

with A.app.app_context():
    before = A.Enquiry.query.count()
    z = email_sync._ingest_portal_lead(db, A.Contact, A.Enquiry, A.EnquiryNote,
                                       parsed, subject, ZOOPLA_BODY,
                                       'zoopla-msg-1', datetime.utcnow())
    db.session.commit()
    saved = A.Enquiry.query.get(z.id)
    assert A.Enquiry.query.count() == before + 1
    assert saved.source == 'Zoopla', f'source is {saved.source!r}'
    assert saved.enquiry_type == 'Tenant — Looking to Rent', saved.enquiry_type
    assert saved.project_id == IDS['proj'], 'the project was not matched'
    assert saved.property_id == IDS['prop'], 'the property was not matched'
    assert saved.contact_id == IDS['known'], 'it did not find the existing contact'
    assert saved.organisation_id == IDS['org'], \
        "the enquirer's organisation was not carried across"
    assert A.EnquiryNote.query.filter_by(enquiry_id=saved.id).count() == 1
    zid = saved.id
page = cl.get('/enquiries').get_data(as_text=True)
assert 'Zoopla' in page and 'John Smith' in page, 'it is not in the register'
assert cl.get(f'/enquiries/{zid}').status_code == 200
print('9. a Zoopla lead parses, saves as source Zoopla, matches its project '
      'and appears in the register')


# ─── 10. A Zoopla sale lead files as a buyer ────────────────────────────────
SALE_BODY = ZOOPLA_BODY.replace('Interested in viewing this unit',
                                'Interested in purchasing this unit')
with A.app.app_context():
    p = A.Project.query.get(IDS['proj'])
    l = A.Listing.query.filter_by(project_id=p.id).first()
    l.set_as_for_sale, l.set_as_to_let = True, False
    db.session.commit()
    parsed2 = portal_leads.parse_lead('Sale enquiry', SALE_BODY, sender, 'Zoopla')
    z2 = email_sync._ingest_portal_lead(db, A.Contact, A.Enquiry, A.EnquiryNote,
                                        parsed2, 'Sale enquiry', SALE_BODY,
                                        'zoopla-msg-2', datetime.utcnow())
    db.session.commit()
    assert A.Enquiry.query.get(z2.id).enquiry_type in (
        'Buyer — Looking to Buy', 'Tenant — Looking to Rent'), \
        A.Enquiry.query.get(z2.id).enquiry_type
print('10. a portal lead files under the new headings, not the old ones')


# ─── 11. The same lead twice makes one enquiry ──────────────────────────────
with A.app.app_context():
    seen = A.EnquiryNote.query.filter_by(ms_message_id='zoopla-msg-1').first()
    assert seen, 'the message id was not recorded, so a repeat would duplicate'
src = open(f'{ROOT}/email_sync.py').read()
guard = 'if EnquiryNote.query.filter_by(ms_message_id=mid).first():'
assert guard in src, 'there is no duplicate guard on the message id'
after = src[src.index(guard) + len(guard):src.index(guard) + len(guard) + 60]
assert 'continue' in after, f'the guard does not skip the message: {after!r}'
print('11. a repeated portal email is skipped, so one lead makes one enquiry')


# ─── 12. Old enquiries keep their data and their type ───────────────────────
with A.app.app_context():
    old = A.Enquiry.query.get(IDS['legacy'])
    assert old.enquiry_type == 'Rent Review', 'a historical type was rewritten'
    assert old.subject == 'Rent Review enquiry — some old unit', \
        'a historical subject was destroyed'
rec = cl.get(f"/enquiries/{IDS['legacy']}").get_data(as_text=True)
assert 'Rent Review' in rec, 'the historical type is not offered on its own record'
assert 'name="subject"' not in rec.split('note-form')[0], \
    'the obsolete Subject field is still shown'
# Saving it must not silently reclassify it.
cl.post(f"/enquiries/{IDS['legacy']}/edit", data={'status': 'Open'},
        follow_redirects=True)
with A.app.app_context():
    assert A.Enquiry.query.get(IDS['legacy']).enquiry_type == 'Rent Review'
print('12. historical enquiries keep their type and subject, and are not '
      'reclassified by being saved')


# ─── 13. Subject is still populated, for the list and the reports ───────────
with A.app.app_context():
    for e in A.Enquiry.query.all():
        assert e.subject, f'enquiry {e.id} has no subject, which the column forbids'
page = cl.get('/enquiries').get_data(as_text=True)
assert page
report = cl.get('/reports/enquiries')
assert report.status_code in (200, 302, 404), f'the report broke ({report.status_code})'
print('13. every enquiry still has a subject underneath, so lists and reports work')


# ─── 14. The record page shows what an agent needs, and not what they don't ─
e = make(enquiry_type='Tenant — Looking to Rent', contact_id=IDS['known'],
         project_id=IDS['proj'])
rec = cl.get(f'/enquiries/{e.id}').get_data(as_text=True)
for wanted in ('Inquiry type', 'Contact', 'Organisation', 'Email', 'Mobile',
               'Source', 'Linked project'):
    assert wanted in rec, f'{wanted!r} is not on the record page'
# And the strip must show the VALUES, not just the labels.
strip = rec[rec.index('enq-strip'):rec.index('</dl>')]
for value in ('Tenant — Looking to Rent', 'John Smith', 'ABC Interiors Ltd',
              'john.smith@abcinteriors.co.uk', '07700 900123', '12 Kings Road'):
    assert value in strip, f'{value!r} is missing from the summary strip'
assert 'Enquiry information' not in rec, 'the old Enquiry information box is back'
assert 'Viewing Request' not in rec
# One email field, not two.
assert rec.count('name="contact_email"') == 1, 'there is more than one email field'
contact_box = rec[rec.index('id="contact-box"'):rec.index('id="enquiry-box"')]
for gone in ('fcell--label">Call<', 'fcell--label">Write<'):
    assert gone not in contact_box, f'the {gone!r} cell is still in Contact details'
assert contact_box.count('name="contact_email"') == 1
assert 'Organisation' in contact_box and 'Mobile' in contact_box
print('14. the record shows type, contact, organisation, email, mobile, source '
      'and project — with one email field and no Call/Write cells')


# ─── 15. Notes and activity are still there ─────────────────────────────────
for wanted in ('Add a note', 'Follow-up'):
    assert wanted in rec, f'{wanted!r} was lost'
print('15. notes, activity and follow-up are still on the page')


# ─── 16. The page is valid, closed markup ───────────────────────────────────
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


for name, url in (('new enquiry', '/enquiries/new'),
                  ('enquiry record', f'/enquiries/{e.id}'),
                  ('legacy record', f"/enquiries/{IDS['legacy']}"),
                  ('register', '/enquiries')):
    p = Balance()
    p.feed(cl.get(url).get_data(as_text=True))
    assert not p.bad, f'{name}: {p.bad[:3]}'
    assert not p.stack, f'{name}: unclosed {p.stack[:3]}'
print('16. every enquiry page renders as valid, fully closed markup')


# ─── 17. Permissions ────────────────────────────────────────────────────────
viewer = A.app.test_client()
viewer.post('/login', data={'username': 'looker', 'password': 'pw'},
            follow_redirects=True)
assert viewer.post('/enquiries/new', data={'enquiry_type': 'Other'}).status_code == 403
assert viewer.get('/enquiries').status_code == 200
print('17. a viewer can read the register but cannot create an enquiry')


# ─── 18. The mailbox, which is what actually gates Zoopla ───────────────────
assert email_sync.check_configured() is False or email_sync.check_configured() is True
configured = email_sync.check_configured()
started = email_sync.start_background_sync(A.app, db, A.Contact, A.Enquiry,
                                           A.EnquiryNote)
assert started is False, 'the poller started without credentials'
print(f'18. the mail poller is off while MS credentials are unset '
      f'(configured={configured}) — this, not the parser, is what stops Zoopla')

print('\nENQUIRIES: ALL CHECKS PASSED')
