"""Sale and tenancy progression: chasing a deal through to completion."""
import os
import sys
import tempfile
from datetime import timedelta

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/prog.db'
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
    db.session.commit()
    council = A.Council.query.first()

    def prop(address):
        p = A.Property(address=address, postcode='SW6 1AA', property_type='Office',
                       council_id=council.id)
        db.session.add(p); db.session.commit()
        return p

    def deal(address, status, next_call=None, kind='Capital', **kw):
        p = prop(address)
        t = A.Transaction(property_id=p.id, transaction_type=kind, status=status,
                          next_call=next_call, **kw)
        db.session.add(t); db.session.commit()
        return t

    IDS = {
        'overdue': deal('1 Overdue Street', 'Terms Agreed',
                        today - timedelta(days=3)).id,
        'today': deal('2 Today Road', 'Solicitors Instructed', today).id,
        'soon': deal('3 Soon Lane', 'Terms Agreed', today + timedelta(days=5)).id,
        'quiet': deal('4 No Call Booked Way', 'Solicitors Instructed', None).id,
        # Not in progression: too early, and already done.
        'early': deal('5 Still Negotiating Place', 'Under Offer', today).id,
        'done': deal('6 Completed Close', 'Completed', today).id,
        'dead': deal('7 Fallen Through Row', 'Fallen Through', today).id,
    }

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


# ─── 1. Only deals between terms agreed and completion ──────────────────────
with A.app.app_context():
    deals = A.progression_deals()
    addresses = [d.property.address for d in deals]
assert len(deals) == 4, addresses
for gone in ('5 Still Negotiating Place', '6 Completed Close', '7 Fallen Through Row'):
    assert gone not in addresses, f'{gone} should not be in progression'
for wanted in ('1 Overdue Street', '2 Today Road', '3 Soon Lane',
               '4 No Call Booked Way'):
    assert wanted in addresses, f'{wanted} is missing'
print('1. only Terms Agreed and Solicitors Instructed deals are progressing')


# ─── 2. Ordered by what needs chasing ───────────────────────────────────────
assert addresses[0] == '1 Overdue Street', f'overdue is not first: {addresses}'
assert addresses[1] == '2 Today Road', addresses
assert addresses[2] == '3 Soon Lane', addresses
assert addresses[3] == '4 No Call Booked Way', \
    'a deal with no call booked should come last, not be hidden'
print('2. overdue first, then by date, with unscheduled deals last')


# ─── 3. The summary counts what matters ─────────────────────────────────────
with A.app.app_context():
    summary = A.progression_summary()
assert summary['total'] == 4
assert summary['overdue'] == 1, summary
assert summary['due_today'] == 1, summary
assert summary['unscheduled'] == 1, summary
assert summary['by_stage']['Terms Agreed'] == 2, summary['by_stage']
assert summary['by_stage']['Solicitors Instructed'] == 2, summary['by_stage']
print('3. the summary counts each stage, the overdue, the due today and the quiet')


# ─── 4. It is on the dashboard ──────────────────────────────────────────────
def panel():
    body = cl.get('/').get_data(as_text=True)
    assert 'Progressing to completion' in body, 'the panel is not on the dashboard'
    start = body.index('prog-panel')
    return body[start:body.index('</section>', start)]

body = cl.get('/').get_data(as_text=True)
p = panel()
for wanted in ('1 Overdue Street', '2 Today Road', '4 No Call Booked Way'):
    assert wanted in p, f'{wanted} is not in the progression panel'
assert '5 Still Negotiating Place' not in p, \
    'a deal still being negotiated is in the progression panel'
assert '6 Completed Close' not in p and '7 Fallen Through Row' not in p
assert 'Overdue' in p and 'Call today' in p and 'No call booked' in p
print('4. the dashboard shows the deals, with what is overdue and what is quiet')


# ─── 5. Each row links to its transaction ───────────────────────────────────
assert f"/transactions/{IDS['overdue']}" in p, 'a row does not link through'
print('5. each row links to the transaction it is about')


# ─── 6. The solicitor fields that already existed are still used ────────────
# Adding a second set of solicitor columns would have given the model two of
# each name, and SQLAlchemy would silently keep whichever came last.
import re
import collections
src = open(os.path.join(ROOT, 'app.py')).read()
start = src.index('class Transaction(db.Model):')
end = src.index('\nclass ', start + 10)
names = re.findall(r'^\s{4}(\w+)\s*=\s*db\.Column', src[start:end], re.M)
dupes = [n for n, c in collections.Counter(names).items() if c > 1]
assert not dupes, f'the Transaction model defines these columns twice: {dupes}'
for existing in ('client_solicitor', 'client_solicitor_firm', 'client_solicitor_email',
                 'client_solicitor_phone', 'other_solicitor', 'other_solicitor_firm'):
    assert existing in names, f'{existing} was lost'
print(f'6. no column is defined twice, and all {len(names)} include the '
      'solicitor fields that already existed')


# ─── 7. Solicitors and the next call are on the record, and save ────────────
rec = cl.get(f"/transactions/{IDS['overdue']}").get_data(as_text=True)
for field in ('client_solicitor', 'client_solicitor_firm', 'client_solicitor_email',
              'client_solicitor_phone', 'other_solicitor', 'next_call',
              'next_call_note', 'target_completion'):
    assert f'name="{field}"' in rec, f'{field} is not on the transaction record'

cl.post(f"/transactions/{IDS['quiet']}/save", data={
    'client_solicitor': 'A Solicitor', 'client_solicitor_firm': 'Blackstone LLP',
    'client_solicitor_email': 'a@blackstone.example',
    'client_solicitor_phone': '020 7000 0000',
    'next_call': (today + timedelta(days=2)).isoformat(),
    'next_call_note': 'chase replies to enquiries',
    'target_completion': (today + timedelta(days=30)).isoformat()},
    follow_redirects=True)
with A.app.app_context():
    t = A.Transaction.query.get(IDS['quiet'])
    assert t.client_solicitor == 'A Solicitor', t.client_solicitor
    assert t.client_solicitor_firm == 'Blackstone LLP'
    assert t.next_call == today + timedelta(days=2), t.next_call
    assert t.next_call_note == 'chase replies to enquiries'
    assert t.target_completion == today + timedelta(days=30)
print('7. solicitor details, the next call and a target completion all save')


# ─── 8. Booking a call moves it out of the quiet list ───────────────────────
with A.app.app_context():
    summary = A.progression_summary()
assert summary['unscheduled'] == 0, \
    'a deal with a call booked is still counted as having none'
print('8. booking a call takes the deal out of the unscheduled count')


# ─── 9. Completing a deal takes it off the list ─────────────────────────────
with A.app.app_context():
    A.Transaction.query.get(IDS['overdue']).status = 'Completed'
    db.session.commit()
    after = [d.property.address for d in A.progression_deals()]
assert '1 Overdue Street' not in after, 'a completed deal is still progressing'
assert '1 Overdue Street' not in panel()
print('9. a deal that completes leaves the progression list')


# ─── 10. Nothing in progression shows an empty, honest message ──────────────
with A.app.app_context():
    for t in A.Transaction.query.all():
        t.status = 'Completed'
    db.session.commit()
assert 'Nothing between terms agreed and completion' in panel()
print('10. with nothing in flight the panel says so rather than sitting empty')

print('\nPROGRESSION: ALL CHECKS PASSED')
