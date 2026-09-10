"""The dashboard: four figures, and today.

Three things this holds to.

A contact is created before anybody knows what they want. Asking for applicant
requirements at that moment is asking the wrong question — the person may be a
landlord with a building to let — so those fields belong on the record, not on
the form that makes one.

The figures at the top carry their own month against the one before, worked out
from the records. Where that comparison would answer a different question from
the one the card asks, it is not shown at all.

The diary on the dashboard is the diary page scaled down, reading the same
events, so an appointment moved on one moves on the other.
"""
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/dash.db'
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
    db.session.commit()
    council = A.Council.query.first()

    # Two transactions last month, three this — a real 50% rise.
    last_start, this_start, tomorrow = A._month_bounds()
    prop = A.Property(address='10 New Kings Road, London SW6 4LT', postcode='SW6 4LT',
                      property_type='Retail', council_id=council.id if council else None)
    db.session.add(prop); db.session.commit()

    def transaction_at(when):
        t = A.Transaction(property_id=prop.id, transaction_type='Letting')
        db.session.add(t); db.session.commit()
        t.created_at = when
        db.session.commit()

    for _ in range(2):
        transaction_at(last_start + timedelta(days=2))
    for _ in range(3):
        transaction_at(this_start + timedelta(hours=1))

    # An appointment today, through the ordinary diary route.
    base = A.to_london(datetime.utcnow()).replace(minute=0, second=0, microsecond=0)
    start = A.from_london(base.replace(hour=10))
    ev = A.DiaryEvent(title='Viewing', event_type='viewing',
                      location='1 Stanley Bridge Studios', owner='Benjamin Cowan',
                      start_at=start, end_at=start + timedelta(minutes=60))
    db.session.add(ev); db.session.commit()
    EVENT_ID = ev.id

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)
page = lambda url: cl.get(url).get_data(as_text=True)


# ─── 1. Creating a contact does not ask what they are looking for ───────────
new = page('/contacts/new')
assert 'Applicant Requirements' not in new, \
    'the create form still asks for applicant requirements'
for field in ('req_budget_max', 'req_size_min', 'req_area', 'req_use_class'):
    assert f'name="{field}"' not in new, f'{field} is still on the create form'
print('1. adding a contact does not ask for applicant requirements')


# ─── 2. It only asks for a name and what they are ───────────────────────────
assert 'name="contact_type"' in new and 'name="first_name"' in new
block = new[new.index('name="contact_type"'):]
block = block[:block.index('</select>')]
for t in ('Landlord', 'Tenant', 'Buyer', 'Seller'):
    assert f'>{t}<' in block, f'{t} is not offered when adding a contact'
print('2. the form asks for a name and one of the four types')


# ─── 3. The requirements are still there on the record ──────────────────────
r = cl.post('/contacts/new', data={'first_name': 'Sara', 'last_name': 'Okelo',
                                   'contact_type': 'Buyer'}, follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    made = A.Contact.query.filter_by(last_name='Okelo').first()
    assert made is not None, 'the contact was not created'
    assert made.contact_type == 'Buyer'
    CID = made.id
record = page(f'/contacts/{CID}/edit')
assert 'Applicant Requirements' in record, \
    'the requirements were removed from the record as well as the form'
assert 'name="req_budget_max"' in record
print('3. requirements are still on the record, where the answer is known')


# ─── 4. Saved as a Buyer, filed under Buyers ────────────────────────────────
assert 'Okelo' in page('/contacts?type=Buyer'), 'a Buyer is not under Buyers'
for other in ('Landlord', 'Tenant', 'Seller'):
    assert 'Okelo' not in page(f'/contacts?type={other}'), \
        f'a Buyer is showing under {other}s'
print('4. the type chosen decides which page the contact appears on')


# ─── 5. Each type has its own page ──────────────────────────────────────────
nav = page('/contacts')
for t in ('Landlord', 'Tenant', 'Buyer', 'Seller'):
    assert f'type={t}' in nav, f'there is no {t} page'
    assert cl.get(f'/contacts?type={t}').status_code == 200
print('5. Landlords, Tenants, Buyers and Sellers each have their own page')


# ─── 6. The transactions figure carries a real comparison ───────────────────
dash = page('/')
assert 'kpi-card' in dash, 'the figures are not cards'
with A.app.app_context():
    trend = A._month_on_month(A.Transaction)
assert trend['last_month'] == 2 and trend['this_month'] == 3, trend
assert trend['pct'] == 50 and trend['dir'] == 'up', trend
assert '50%' in dash and 'vs last month' in dash, \
    'the month-on-month is not shown on the dashboard'
print('6. transactions show 50% up on last month, from the real records')


# ─── 7. A fall reads as a fall ──────────────────────────────────────────────
with A.app.app_context():
    newest = A.Transaction.query.order_by(A.Transaction.id.desc()).limit(2).all()
    for t in newest:
        db.session.delete(t)
    db.session.commit()
    trend = A._month_on_month(A.Transaction)
assert trend['this_month'] == 1 and trend['dir'] == 'down' and trend['pct'] == 50, trend
assert 'is-down' in page('/'), 'a fall is not marked as one'
print('7. a fall is shown as a fall, not as a rise')


# ─── 8. Nothing to compare against is not a 100% rise ───────────────────────
with A.app.app_context():
    for t in A.Transaction.query.all():
        db.session.delete(t)
    db.session.commit()
    trend = A._month_on_month(A.Transaction)
assert trend['pct'] is None and trend['dir'] is None, \
    'a percentage was invented from a month with nothing in it'
assert trend['this_month'] == 0 and trend['last_month'] == 0
print('8. a month with nothing to compare against shows no percentage')


# ─── 9. Open enquiries is a snapshot, so it is not given a false trend ──────
dash = page('/')
card = dash[dash.index('Open enquiries'):]
card = card[:card.index('</a>')]
assert 'vs last month' not in card, \
    'a count of what is open now was compared against last month'
assert 'in this month' in card, 'the enquiries card says nothing about the month'
print('9. open enquiries reports what came in, not a false comparison')


# ─── 10. The dashboard diary is the diary, not a separate list ──────────────
dash = page('/')
assert 'minical' in dash, 'the dashboard has no diary grid'
assert 'Viewing' in dash and '1 Stanley Bridge Studios' in dash
# The hours run down the side — this is the thing that makes it a diary.
hours = re.findall(r'minical-hour[^>]*>\s*<span>(\d{2}:00)</span>', dash)
assert len(hours) >= 8, f'only {len(hours)} hour markers down the side'
assert '10:00' in hours and '09:00' in hours, hours
print(f'10. the dashboard shows a day grid with {len(hours)} hours down the side')


# ─── 11. It is the same data as the diary page ──────────────────────────────
# Moving the appointment moves it in both places, because neither keeps a copy.
with A.app.app_context():
    ev = A.DiaryEvent.query.get(EVENT_ID)
    moved = A.to_london(ev.start_at).replace(hour=16)
    ev.start_at = A.from_london(moved)
    ev.end_at = ev.start_at + timedelta(minutes=60)
    db.session.commit()
dash, full = page('/'), page('/diary?view=day')
assert '16:00' in dash and '16:00' in full, 'the move did not reach both pages'
assert 'data-start-min' in full, 'the diary page is not the grid it was'
print('11. the dashboard and the diary page read the same appointments')


# ─── 12. An appointment sits where its time puts it ─────────────────────────
dash = page('/')
block = dash[dash.index('minical-event'):]
block = block[:block.index('</a>')]
assert '--top:' in block and '--height:' in block, \
    'appointments are not positioned by time'
top = float(re.search(r'--top:\s*([\d.]+)', block).group(1))
assert 0 <= top <= 100, top
# 16:00 in a window that starts at 08:00 belongs in the lower half of the day.
assert top > 50, f'a four o\'clock appointment was placed at {top}% down the day'
print('12. an appointment is placed at the height its time puts it')


# ─── 13. Messages and tasks sit below the day, not beside it ────────────────
dash = page('/')
assert dash.index('minical') < dash.index('Messages &amp; Tasks'), \
    'messages and tasks still come before the diary'
assert dash.index('kpi-row') < dash.index('minical'), \
    'the figures no longer come first'
assert 'org-grid--two' in dash, 'the organiser still has an empty middle column'
print('13. figures, then the day, then messages and tasks')


# ─── 14. Every page still renders, and nothing was quietly dropped ──────────
for url in ('/', '/diary', '/contacts', '/contacts/new', '/properties',
            '/projects', '/enquiries', '/transactions'):
    assert cl.get(url).status_code == 200, url
dash = page('/')
for kept in ('To Let', 'Landlords To Call', 'Quick Actions', 'Recent Transactions'):
    assert kept in dash, f'{kept} disappeared from the dashboard'
print('14. every page still loads and no panel was lost')

print('\nDASHBOARD: ALL CHECKS PASSED')
