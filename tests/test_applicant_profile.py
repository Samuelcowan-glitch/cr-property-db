# -*- coding: utf-8 -*-
"""What a property fills in about the person enquiring about it.

The brief's own worked example is check 1: a property advertised at £1,500 a
month gives a budget of £1,200 to £1,900, and the same arithmetic has to hold
at any other rent rather than being a fixed range.

The rest is about what must NOT happen. Nothing is invented where the property
does not say what it costs; a landlord asking about their own building is not
given a tenant's budget; and nothing already typed is ever overwritten,
because every one of these fields is a starting point the office is expected
to correct.
"""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/applicants.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
import applicant_profile as apro
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)
    print('  %-68s %s' % (label, 'PASS' if condition else 'FAIL'))


def section(title):
    print('\n' + title)


# ── The register ─────────────────────────────────────────────────────────────
STOCK = [
    # key       address                              type          use          price   unit   size  area
    ('shop',   '12 King Street, London SW6 1AA',     'Retail',     'retail',     1500, 'pcm', 1200, 'Fulham'),
    ('office', '40 Peterborough Road, London SW6 3BN','Office',    'office',     1800, 'pcm',  900, 'Fulham'),
    ('unit',   '7 Imperial Works, London SW6 2AA',   'Industrial', 'industrial',24000, 'pa',  3000, 'Fulham'),
    ('shop2',  '88 Fulham Road, London SW3 6HR',     'Retail',     'retail',     1650, 'pcm', 1100, 'Chelsea'),
    ('poa',    '3 Harwood Terrace, London SW6 2AR',  'Retail',     'retail',     None, 'poa', 1000, 'Fulham'),
]

IDS = {}
with A.app.app_context():
    db.create_all()
    A._sync_model_columns()
    A._migrate_rates_tables()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          password_hash=generate_password_hash('pw')))
    db.session.commit()
    council = A.Council.query.first()

    for key, addr, ptype, use, price, unit, size, area in STOCK:
        prop = A.Property(address=addr, postcode=addr.rsplit(' ', 2)[-2] + ' ' + addr.rsplit(' ', 1)[-1],
                          property_type=ptype, size=size, area=area,
                          website_category='commercial', use_class=use,
                          website_listed=True,
                          listing_price=price, listing_price_unit=unit,
                          council_id=council.id)
        db.session.add(prop)
        db.session.commit()
        proj = A.Project(name=f'To Let — {addr}', property_id=prop.id,
                         instruction_type=A.INSTRUCTION_TO_LET, status='Active',
                         fee_earner_id=1)
        db.session.add(proj)
        db.session.commit()
        db.session.add(A.Listing(
            project_id=proj.id, property_id=prop.id, set_as_to_let=True,
            listing_price=price, listing_price_unit=unit,
            listing_status='available', website_category='commercial', area=area))
        db.session.commit()
        IDS[key] = {'prop': prop.id, 'proj': proj.id, 'ref': proj.project_ref}

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def enquire(**over):
    """A website enquiry, the way the public site posts one."""
    data = {'from_name': 'Ruth Alder', 'from_email': 'ruth@example.com',
            'phone': '07700 900111', 'interest': 'Viewing request',
            'transaction': 'let', 'message': 'Please may I view this.'}
    data.update(over)
    r = cl.post('/api/enquiry', json=data)
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:200])
    body = r.get_json()
    with A.app.app_context():
        return A.Enquiry.query.get(body['enquiry_id'])


# ─── 1. The budget range in the brief ────────────────────────────────────────
section('1. The budget range, as the brief sets it out')
r = apro.budget_range(1500, 'pcm')
check('£1,500 pcm gives a minimum of £1,200', r['min'] == 1200.0)
check('£1,500 pcm gives a maximum of £1,900', r['max'] == 1900.0)
check('and the basis is carried through as pcm', r['unit'] == 'pcm')
check('the record can say where the figures came from', '£1,500' in r['reason'])

# Dynamic, not a fixed range: every one of these is worked out from the rent.
for asking, low, high in [(1000, 800, 1300), (2000, 1600, 2500),
                          (2750, 2200, 3500), (3000, 2400, 3800)]:
    got = apro.budget_range(asking, 'pcm')
    check(f'£{asking:,} pcm gives £{low:,}–£{high:,}',
          got['min'] == low and got['max'] == high)
check('the range is never the same two rents running',
      apro.budget_range(1500, 'pcm')['max'] != apro.budget_range(2500, 'pcm')['max'])

section('2. An annual rent and a sale price are handled on their own terms')
r = apro.budget_range(24000, 'pa')
check('£24,000 pa gives £19,000–£30,000, rounded in thousands',
      (r['min'], r['max']) == (19000.0, 30000.0))
r = apro.budget_range(500000, 'sale')
check('a £500,000 sale price gives £400,000–£625,000',
      (r['min'], r['max']) == (400000.0, 625000.0))
check('a sale price asks to buy, not to lease',
      apro.requirement_tenure('sale') == 'purchase')
check('a rent asks to lease', apro.requirement_tenure('pcm') == 'lease')

section('3. Nothing is invented where the property does not say')
for amount, unit in [(None, 'pcm'), (0, 'pcm'), (1500, 'poa'), (1500, ''),
                     ('', 'pcm'), ('not a number', 'pcm'), (-500, 'pcm')]:
    check(f'no budget from {amount!r} {unit!r}', apro.budget_range(amount, unit) is None)

section('4. The property type, as the brief sets it out')
for given, wanted in [('Industrial', 'Industrial'), ('Retail', 'Retail'),
                      ('Office', 'Office'), ('Light Industrial', 'Light Industrial'),
                      ('Creative / Art Studio', 'Creative / Art Studio')]:
    check(f'a {given.lower()} property asks for {wanted}',
          apro.requirement_type(given) == wanted)
check('a type spelled differently still lands on one of the five',
      apro.requirement_type('warehouse') == 'Industrial'
      and apro.requirement_type('shop') == 'Retail'
      and apro.requirement_type('office suite') == 'Office')
check('Industrial and Light Industrial are kept apart',
      apro.requirement_type('Industrial') != apro.requirement_type('Light Industrial'))
check('a type nobody has drawn is left unsaid rather than guessed',
      apro.requirement_type('Hotel') is None
      and apro.requirement_type(None) is None)

# ─── 5. A website enquiry fills the enquiry in ───────────────────────────────
section('5. A website enquiry about a property fills itself in')
e = enquire(property='12 King Street, London SW6 1AA')
check('the enquiry is linked to the property', e.property_id == IDS['shop']['prop'])
check('and to the instruction on it', e.project_id == IDS['shop']['proj'])
check('the budget is the property’s, worked out',
      (e.req_budget_min, e.req_budget_max) == (1200.0, 1900.0))
check('on the property’s own basis, not a default of per annum',
      e.req_budget_unit == 'pcm')
check('the property type is recorded', e.req_property_type == 'Retail')
check('so is the use class', e.req_use_class == 'retail')
check('so is the area', e.req_area == 'Fulham')
check('and the category', e.req_category == 'commercial')
check('the size is NOT invented from one unit',
      e.req_size_min is None and e.req_size_max is None)

section('6. And the person, so they are matched against the whole register')
with A.app.app_context():
    who = A.Contact.query.filter_by(email='ruth@example.com').first()
    check('they are recorded as a Tenant', who.contact_type == 'Tenant')
    check('with the budget on them, not only on the enquiry',
          (who.req_budget_min, who.req_budget_max) == (1200.0, 1900.0))
    check('and the basis', who.req_budget_unit == 'pcm')
    check('and the property type', who.req_property_type == 'Retail')
    check('they are on the applicant list for the instruction',
          A.ProjectApplicant.query.filter_by(
              project_id=IDS['shop']['proj'], contact_id=who.id).first() is not None)
    matched = A.match_properties_to_contact(who)
    check('and they now match properties at all', len(matched) >= 1)
    check('the property they asked about is among them',
          IDS['shop']['prop'] in [m['prop'].id for m in matched])
    # The whole point: before this they matched nothing, because
    # match_contacts_to_property only looks at contacts with a category.
    check('a property’s applicant list finds them too',
          who.id in [c.id for _s, _r, c in A.match_contacts_to_property(
              A.Property.query.get(IDS['shop']['prop']))])

section(u'7. A landlord with space to let is not given a tenant\u2019s budget')
# Taken over the telephone, which is how a landlord's enquiry arrives. A
# website enquiry made ON a To Let listing is classified as a tenant whatever
# the form said \u2014 which is what such an enquiry almost always is \u2014 so the
# landlord case is the manual one.
r = cl.post('/enquiries/new', data={
    'enquiry_type': u'Landlord \u2014 Looking to Let', 'status': 'Open',
    'source': 'Manual', 'received_date': '2026-10-22',
    'contact_mode': 'new', 'caller_name': 'Henry Vale',
    'caller_email': 'henry@vale.example',
    'letting_mode': 'existing', 'project_id': str(IDS['office']['proj'])},
    follow_redirects=True)
check('the landlord enquiry is recorded', r.status_code == 200)
with A.app.app_context():
    henry = A.Contact.query.filter_by(email='henry@vale.example').first()
    land = A.Enquiry.query.filter_by(contact_id=henry.id).first()
    check('they are recorded as a Landlord, not a tenant',
          henry.contact_type == 'Landlord'
          and henry.contact_type not in A.LOOKING_FOR)
    check('the enquiry carries no budget', land.req_budget_max is None)
    check('nor a property type', land.req_property_type is None)
    check('and they are given no budget of their own', henry.req_budget_max is None)
    check('they are on nobody\u2019s applicant list',
          A.ProjectApplicant.query.filter_by(contact_id=henry.id).count() == 0)
    HENRY, LAND = henry.id, land.id
# And the registration refuses them outright rather than quietly doing it.
r = cl.post('/enquiries/%d/register-applicant' % LAND, follow_redirects=True)
check('registering a landlord as an applicant is refused, and says why',
      'not an applicant' in r.get_data(as_text=True))
with A.app.app_context():
    check('and nothing was written to them',
          A.ProjectApplicant.query.filter_by(contact_id=HENRY).count() == 0
          and A.Contact.query.get(HENRY).req_budget_max is None)

section('8. A property with no advertised figure gives no budget')
e3 = enquire(from_name='Dana Peck', from_email='dana@example.com',
             property='3 Harwood Terrace, London SW6 2AR')
check('no budget is invented for a price on application',
      e3.req_budget_min is None and e3.req_budget_max is None)
check('but what the property DOES say is still recorded',
      e3.req_property_type == 'Retail' and e3.req_area == 'Fulham')

# ─── 9. Nothing already recorded is overwritten ──────────────────────────────
section('9. Nothing already recorded is overwritten')
with A.app.app_context():
    told = A.Contact(first_name='Mara', last_name='Olsen', email='mara@example.com',
                     contact_type='Tenant', req_category='commercial',
                     req_property_type='Office', req_area='Chelsea, Pimlico',
                     req_budget_min=3000, req_budget_max=4500, req_budget_unit='pcm',
                     req_size_min=800, req_size_max=1500)
    db.session.add(told)
    db.session.commit()
    MARA = told.id
# She has told us what she wants, then enquires about a cheap shop in Fulham.
enquire(from_name='Mara Olsen', from_email='mara@example.com',
        property='12 King Street, London SW6 1AA')
with A.app.app_context():
    told = A.Contact.query.get(MARA)
    check('her own budget stands', (told.req_budget_min, told.req_budget_max) == (3000.0, 4500.0))
    check('her own property type stands', told.req_property_type == 'Office')
    check('her own areas stand', told.req_area == 'Chelsea, Pimlico')
    check('her own size range stands', (told.req_size_min, told.req_size_max) == (800.0, 1500.0))

section('10. Everything filled in stays editable, and matching follows it')
with A.app.app_context():
    who = A.Contact.query.filter_by(email='ruth@example.com').first()
    who.req_budget_max = 2600          # the office widens it after a conversation
    who.req_area = None                # and she will look anywhere
    db.session.commit()
    addresses = [m['prop'].address for m in A.match_properties_to_contact(who)]
    check('a wider budget brings in a property it had ruled out',
          any('Fulham Road' in a for a in addresses))
    check('the edited figure is what was saved',
          A.Contact.query.get(who.id).req_budget_max == 2600.0)

# ─── 11. The requirement fields on the forms actually save ──────────────────
section('11. The requirement fields on the record actually save')
page = cl.get(f'/enquiries/{e.id}').get_data(as_text=True)
check('the record offers the property type', 'name="req_property_type"' in page)
check('and the budget', 'name="req_budget_min"' in page and 'name="req_budget_max"' in page)
check('and the applicant registration panel', 'Applicant registration' in page)
cl.post(f'/enquiries/{e.id}/edit', data={
    'enquiry_type': e.enquiry_type, 'status': 'Open', 'source': 'Website',
    'req_property_type': 'Light Industrial', 'req_budget_min': '900',
    'req_budget_max': '1400', 'req_budget_unit': 'pcm',
    'req_category': 'commercial'}, follow_redirects=True)
with A.app.app_context():
    again = A.Enquiry.query.get(e.id)
    # req_property_type was on the page but was never written by the save:
    # the field existed on the model and the form, and nothing joined them.
    check('a property type chosen by hand is saved',
          again.req_property_type == 'Light Industrial')
    check('a budget typed by hand is saved',
          (again.req_budget_min, again.req_budget_max) == (900.0, 1400.0))

# ─── 12. Registering carries the enquiry's own answers, not the advert ──────
section('12. Registering carries what the enquiry says, corrections included')
with A.app.app_context():
    fresh = A.Contact(first_name='Tom', last_name='Reed', email='tom@example.com')
    db.session.add(fresh)
    db.session.commit()
    taken = A.Enquiry(subject='Telephone — industrial unit',
                      enquiry_type='Tenant — Looking to Rent', status='Open',
                      source='Manual', contact_id=fresh.id,
                      property_id=IDS['unit']['prop'], project_id=IDS['unit']['proj'],
                      # the office corrected the budget on the call
                      req_budget_min=18000, req_budget_max=26000, req_budget_unit='pa',
                      req_size_min=2500, req_size_max=4000)
    db.session.add(taken)
    db.session.commit()
    TOM, ENQ = fresh.id, taken.id
r = cl.post(f'/enquiries/{ENQ}/register-applicant', follow_redirects=True)
check('the registration goes through', r.status_code == 200)
with A.app.app_context():
    tom = A.Contact.query.get(TOM)
    check('the corrected budget is carried, not the one off the advert',
          (tom.req_budget_min, tom.req_budget_max) == (18000.0, 26000.0))
    check('the size range the caller gave is carried',
          (tom.req_size_min, tom.req_size_max) == (2500.0, 4000.0))
    check('and the property type is filled from the property',
          tom.req_property_type == 'Industrial')
    check('they are typed as a tenant by the registration',
          tom.contact_type == 'Tenant')
    row = A.ProjectApplicant.query.filter_by(
        project_id=IDS['unit']['proj'], contact_id=TOM).first()
    check('they are on the instruction’s applicant list', row is not None)
    ref = IDS['unit']['ref']
    check('and the note says which instruction brought them in',
          bool(row.notes) and (not ref or ref in row.notes
                               or 'Imperial Works' in row.notes))

section('13. A budget is compared on one basis, whichever way it was quoted')
with A.app.app_context():
    # £20,000-£30,000 a year against a shop at £1,650 pcm = £19,800 a year.
    annual = A.Contact(first_name='Paul', last_name='Dunn', contact_type='Tenant',
                       req_category='commercial', req_use_class='retail',
                       req_budget_min=19000, req_budget_max=30000, req_budget_unit='pa')
    db.session.add(annual)
    db.session.commit()
    hits = [m['prop'].id for m in A.match_properties_to_contact(annual)]
    check('an annual budget reaches a monthly rent inside it',
          IDS['shop2']['prop'] in hits)
    monthly = A.Contact(first_name='Ann', last_name='Shaw', contact_type='Tenant',
                        req_category='commercial', req_use_class='industrial',
                        req_budget_min=1600, req_budget_max=2200, req_budget_unit='pcm')
    db.session.add(monthly)
    db.session.commit()
    hits = [m['prop'].id for m in A.match_properties_to_contact(monthly)]
    check('and a monthly budget reaches an annual rent inside it',
          IDS['unit']['prop'] in hits)
    check('a monthly figure is twelve times the month, exactly',
          A.annual_equivalent(1500, 'pcm') == 18000
          and A.annual_equivalent(18000, 'pa') == 18000)
    check('a sale price is not multiplied by twelve',
          A.annual_equivalent(500000, 'sale') == 500000)

section('14. The same enquiry twice changes nothing')
before = None
with A.app.app_context():
    tom = A.Contact.query.get(TOM)
    before = (tom.req_budget_min, tom.req_budget_max, tom.req_property_type,
              A.ProjectApplicant.query.filter_by(contact_id=TOM).count())
cl.post(f'/enquiries/{ENQ}/register-applicant', follow_redirects=True)
with A.app.app_context():
    tom = A.Contact.query.get(TOM)
    after = (tom.req_budget_min, tom.req_budget_max, tom.req_property_type,
             A.ProjectApplicant.query.filter_by(contact_id=TOM).count())
    check('registering twice leaves the requirements as they were', before[:3] == after[:3])
    check('and does not add a second row to the applicant list', before[3] == after[3])

print()
if failures:
    print('FAILED: %d of these checks' % len(failures))
    for f in failures:
        print('   -', f)
    sys.exit(1)
print('APPLICANT PROFILE: ALL CHECKS PASSED')
