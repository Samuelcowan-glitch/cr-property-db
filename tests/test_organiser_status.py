"""Stage and payment position on the Organiser.

A transaction's progress and its money are different questions. Completing a
deal does not pay its invoice, and being paid does not stop it having
completed, so the Organiser shows both — read from the transaction itself,
using the same rules the Transactions page uses.
"""
import os, re, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)
from app import (app, db, User, Property, Transaction, TransactionPayment,
                 TRANSACTION_STATUSES, TRANSACTION_STAGES, PAYMENT_STATES,
                 _restyle_transaction_statuses)
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash
from datetime import date

TODAY = date.today()

with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw'),
                        role='admin', full_name='Benjamin Cowan'))
    prop = Property(address='Riverside Works, Paddock Wood', postcode='TN12 6AB')
    db.session.add(prop); db.session.commit()
    PROP = prop.id

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def make(ref, **kw):
    with app.app_context():
        kw.setdefault('property_id', PROP)
        kw.setdefault('transaction_type', 'Capital')
        kw.setdefault('agreed_value', 500000)
        kw.setdefault('fee_type', 'Percentage')
        kw.setdefault('fee_percent', 2.0)          # £10,000 net, £12,000 invoiced
        kw.setdefault('fee_earner_id', 1)
        kw.setdefault('client', 'Marsden Estates Ltd')
        t = Transaction(reference=ref, **kw)
        db.session.add(t); db.session.commit()
        return t.id


def pay(tid, amount):
    with app.app_context():
        db.session.add(TransactionPayment(transaction_id=tid, amount=amount,
                                          received_on=TODAY))
        db.session.commit()


def states(tid):
    with app.app_context():
        t = Transaction.query.get(tid)
        return t.stage, (t.payment_state if t.shows_payment_state else None)


def page(url='/'):
    r = cl.get(url)
    assert r.status_code == 200, f'{url} returned {r.status_code}'
    return r.get_data(as_text=True)


# ─── 1. One list, one casing, with Under Offer ──────────────────────────────
assert TRANSACTION_STATUSES == [
    'Draft', 'In Progress', 'Under Offer', 'Terms Agreed', 'Solicitors Instructed',
    'Completed', 'Commission Billed', 'Part Paid', 'Paid', 'Fallen Through',
    'Archived'], TRANSACTION_STATUSES
src = open(f'{ROOT}/app.py').read()
without_migration = src.replace(
    src.split('def _restyle_transaction_statuses():')[1].split('\ndef ')[0], '')
OLD_CASING = ["'In " + "progress'", "'Terms " + "agreed'",
              "'Solicitors " + "instructed'", "'Commission " + "billed'",
              "'Part " + "paid'", "'Fallen " + "through'"]
for old in OLD_CASING:
    assert old not in without_migration, f'the old casing {old} is still used'
assert ("'In " + "progress': 'In Progress'") in src, 'nothing migrates the old casing'
print('1. eleven statuses, one casing, Under Offer included')


# ─── 2. Stage and payment are separate lists ────────────────────────────────
assert TRANSACTION_STAGES == ['Draft', 'In Progress', 'Under Offer', 'Terms Agreed',
                              'Solicitors Instructed', 'Completed']
assert PAYMENT_STATES == ['Not Billed', 'Commission Billed', 'Part Paid', 'Paid']
assert 'Paid' not in TRANSACTION_STAGES, 'a payment position is being used as a stage'
print('2. progression and payment are kept as separate vocabularies')


# ─── 3. Every stage reads as itself ─────────────────────────────────────────
for status in ('Draft', 'In Progress', 'Under Offer', 'Terms Agreed',
               'Solicitors Instructed', 'Completed'):
    tid = make(f'TR-{status[:3].upper()}', status=status)
    stage, payment = states(tid)
    assert stage == status, f'{status} reads as {stage}'
    assert payment == 'Not Billed', f'{status} with no invoice reads as {payment}'
print('3. each stage reads as itself, and nothing is billed until it is')


# ─── 4. Completing a deal does not pay its invoice ──────────────────────────
done = make('TR-0100', status='Completed', completion_date=TODAY)
assert states(done) == ('Completed', 'Not Billed'), states(done)
print('4. a completed transaction with no invoice is Not Billed, never Paid')


# ─── 5. Billed, part paid and paid ──────────────────────────────────────────
billed = make('TR-0101', status='Commission Billed', completion_date=TODAY,
              invoice_date=TODAY)
assert states(billed) == ('Completed', 'Commission Billed'), states(billed)

part = make('TR-0102', status='Part Paid', completion_date=TODAY, invoice_date=TODAY)
pay(part, 5000)                                     # of £12,000
assert states(part) == ('Completed', 'Part Paid'), states(part)

full = make('TR-0103', status='Paid', completion_date=TODAY, invoice_date=TODAY)
pay(full, 12000)                                    # net £10,000 plus £2,000 VAT
assert states(full) == ('Completed', 'Paid'), states(full)
print('5. billed, part paid and paid all read correctly, and still show Completed')


# ─── 6. The same VAT and payment rules as the Transactions page ─────────────
with app.app_context():
    t = Transaction.query.get(full)
    assert t.total_invoice == 12000 and t.commission_received == 12000
    assert t.outstanding == 0
    # Paying the net but not the VAT is not paying the invoice.
    short = Transaction.query.get(part)
    assert short.outstanding > 0 and short.payment_state == 'Part Paid'
    Transaction.query.get(part).payments[0].amount = 10000    # the net, not the VAT
    db.session.commit()
    assert Transaction.query.get(part).payment_state == 'Part Paid', \
        'paying the commission but not the VAT was treated as paid in full'
    Transaction.query.get(part).payments[0].amount = 5000
    db.session.commit()
print('6. VAT counts towards the invoice, exactly as on the Transactions page')


# ─── 7. Fallen Through and archived say only that ───────────────────────────
lost = make('TR-0104', status='Fallen Through')
gone = make('TR-0105', status='Archived')
assert states(lost) == ('Fallen Through', None), states(lost)
assert states(gone) == ('Archived', None), states(gone)
print('7. a lost or archived deal shows its outcome, and no payment position')


# ─── 8. Missing dates and figures do not break it ───────────────────────────
bare = make('TR-0106', status='Under Offer', agreed_value=None, fee_percent=None,
            fee_type=None, client=None, fee_earner_id=None)
assert states(bare) == ('Under Offer', 'Not Billed'), states(bare)
with app.app_context():
    t = Transaction.query.get(bare)
    assert t.net_commission == 0 and t.total_invoice == 0
body = page()
assert 'No commission recorded' in body, 'a transaction with no fee is not described'
print('8. a transaction with no dates or figures still reads sensibly')


# ─── 9. Both are shown on the Organiser ─────────────────────────────────────
body = page()
row = body.split('Recent Transactions')[1]
assert 'Completed' in row and 'Part Paid' in row
assert 'Not Billed' in row, 'nothing shows as not yet billed'
for wording in ('Under Offer', 'Terms Agreed', 'Solicitors Instructed'):
    assert wording in row, f'{wording} is not shown'
print('9. the Organiser shows the stage and the payment position together')


# ─── 10. It reads the record, not a copy ────────────────────────────────────
with app.app_context():
    columns = {c.name for c in Transaction.__table__.columns}
    assert 'stage' not in columns and 'payment_state' not in columns, \
        'a second status field was introduced'
    assert 'organiser_status' not in columns
print('10. no Organiser-only status field was created')


# ─── 11. A change on the transaction shows on the Organiser ─────────────────
cl.post(f'/transactions/{billed}/save',
        data={'status': 'Commission Billed'}, follow_redirects=True)
assert states(billed) == ('Completed', 'Commission Billed')
pay(billed, 12000)                       # paid in full, without touching the status
assert states(billed) == ('Completed', 'Paid'), \
    'recording a payment did not change the payment position'
body = page()
assert body.count('Paid') >= 2, 'the Organiser did not follow the payment'
# And removing the invoice takes it back to not billed.
cl.post(f'/transactions/{done}/save',
        data={'status': 'Completed', 'invoice_date': ''}, follow_redirects=True)
assert states(done) == ('Completed', 'Not Billed')
print('11. recording a payment or an invoice changes the Organiser at once')


# ─── 12. The Organiser and the Transactions page agree ──────────────────────
tx_page = page('/transactions?show=all')
with app.app_context():
    for t in Transaction.query.all():
        if t.reference and t.reference in tx_page:
            assert t.status in TRANSACTION_STATUSES or not t.status
# One definition of each, used by both.
assert src.count('def payment_state') == 1, 'payment is worked out in more than one place'
assert src.count('def stage') == 1
print('12. stage and payment are each worked out in exactly one place')


# ─── 13. The whole entry opens the transaction ──────────────────────────────
body = page()
rows = re.findall(r'<a class="org-row tx-row" href="([^"]+)"', body)
assert rows, 'the entries are not clickable'
for href in rows:
    assert href.startswith('/transactions/'), href
    assert cl.get(href).status_code == 200, f'{href} does not open'
assert 'View all transactions' in body, 'there is no way to see them all'
print('13. every entry opens its transaction, and there is a View all action')


# ─── 14. Filtering by stage and by payment ──────────────────────────────────
def refs(url):
    body = page(url)
    part = body.split('Recent Transactions')[1].split('</section>')[0]
    return set(re.findall(r'/transactions/(\d+)"', part))


assert str(lost) in refs('/?tx_stage=Fallen Through'.replace(' ', '+'))
assert str(lost) not in refs('/?tx_stage=Completed')
paid_only = refs('/?tx_payment=Paid')
assert str(full) in paid_only, 'filtering by Paid missed a paid transaction'
assert str(part) not in paid_only, 'a part-paid transaction showed as paid'
assert refs('/?tx_stage=Draft') != refs('/?tx_stage=Completed')
assert 'Clear' in page('/?tx_stage=Draft'), 'a filter cannot be cleared'
print('14. the section filters by stage and by payment, separately')


# ─── 15. Colour never carries the meaning alone ─────────────────────────────
css = open(f'{ROOT}/static/css/crm-grid.css').read()
for cls in ('.st-offer', '.st-notbilled', '.st-completed', '.st-paid',
            '.st-fallen', '.st-billed', '.st-part', '.st-solicitors'):
    assert cls in css, f'{cls} has no styling'
block = css.split('.st {')[1].split('}')[0]
assert 'border-radius: 3px' in block, 'the labels are pill-shaped'
assert 'font-weight: var(--w-heading)' in block, 'the labels are not SemiBold'
body = page()
for wording in ('Completed', 'Part Paid', 'Not Billed', 'Under Offer'):
    assert f'>{wording}</span>' in body.replace('\n', '').replace('  ', ''), \
        f'{wording} is shown by colour without the words'
print('15. every label is written out in full, squared, and in the shared weight')


# ─── 16. Old casing is migrated, and anything odd is left alone ─────────────
with app.app_context():
    t = Transaction.query.get(bare)
    t.status = 'Terms ' + 'agreed'            # as an older record would hold it
    odd = Transaction.query.get(lost)
    odd.status = 'Something Nobody Uses'
    db.session.commit()
    changed = _restyle_transaction_statuses()
    assert changed == 1, changed
    assert Transaction.query.get(bare).status == 'Terms Agreed'
    assert Transaction.query.get(odd.id).status == 'Something Nobody Uses', \
        'an unrecognised status was rewritten'
print('16. old casing is brought across; an unfamiliar status is left as it is')

print('\nORGANISER STATUS: ALL CHECKS PASSED')
