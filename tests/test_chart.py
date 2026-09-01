"""The three chart views, checked against the transactions underneath them.

Every figure is worked out by hand here first. The awkward cases the chart has
to get right are covered on purpose: empty periods, no target at all, a deal
that fell through, an archived record, a part payment, and a transaction with
no expected completion date to place it by.
"""
import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
sys.path.insert(0, "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db")

from app import (app, db, Property, Transaction, TransactionPayment, User,
                 CommissionTarget, transaction_chart, expected_stage, count_stage,
                 target_for, counting_transactions, money_gbp,
                 EXPECTED_STAGES, COUNT_STAGES, TRANSACTION_VIEWS,
                 _month_start, _month_shift, bucket_key_text)
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash
from datetime import date

THIS = _month_start(date.today())
LAST = _month_shift(THIS, -1)
NEXT = _month_shift(THIS, 1)
KEY_THIS = f'{THIS.year}-{THIS.month:02d}'
KEY_NEXT = f'{NEXT.year}-{NEXT.month:02d}'


def near(got, want, what):
    assert abs(float(got) - float(want)) < 0.005, f'{what}: got {got}, expected {want}'


with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw'), role='admin'))
    db.session.add(User(username='reader', password_hash=generate_password_hash('pw'), role='viewer'))
    p = Property(address='1 High Street', postcode='TN1 1AA')
    db.session.add(p)
    db.session.commit()
    PROP = p.id


def make(**kw):
    with app.app_context():
        kw.setdefault('property_id', PROP)
        kw.setdefault('transaction_type', 'Capital')
        t = Transaction(**kw)
        db.session.add(t)
        db.session.commit()
        return t.id


def chart(view='expected', period='month'):
    with app.app_context():
        everyone = Transaction.query.all()
        rows = [t for t in everyone if t.counts_towards_totals]
        return transaction_chart(rows, period, view, everyone=everyone)


def col(c, key):
    return [x for x in c['columns'] if x['period_key'] == key][0]


cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)

# A fee earner to assign work to, now that staff are chosen rather than typed.
with app.app_context():
    _ben = User.query.filter_by(username='admin').first()
    if _ben and not _ben.full_name:
        _ben.full_name = 'Benjamin Cowan'
        _ben.active, _ben.can_earn_fees = True, True
        db.session.commit()
    BEN_ID = _ben.id if _ben else None


# ═══ Expected Commission ════════════════════════════════════════════════════

# Terms Agreed: £400,000 at 2% = £8,000, expected next month.
A = make(reference='TR-0001', status='Terms Agreed', agreed_value=400000,
         fee_type='Percentage', fee_percent=2.0, fee_earner='B Cowan',
         client='Marsden', expected_completion_date=NEXT)
# Solicitors Instructed: £300,000 at 1% = £3,000, expected next month.
B = make(reference='TR-0002', status='Solicitors Instructed', agreed_value=300000,
         fee_type='Percentage', fee_percent=1.0, fee_earner='S Rutter',
         client='Farrier', expected_completion_date=NEXT)
# Completed, not yet invoiced: fixed £5,000, completed this month.
C = make(reference='TR-0003', status='Completed', fee_type='Fixed', fixed_fee=5000.0,
         fee_earner='B Cowan', client='Marsden', completion_date=THIS)
# Invoiced, part paid: £10,000 + VAT = £12,000, £4,000 in. Due this month.
D = make(reference='TR-0004', status='Part Paid', agreed_value=500000,
         fee_type='Percentage', fee_percent=2.0, fee_earner='B Cowan',
         client='Ashdown', completion_date=LAST, invoice_date=LAST,
         payment_due_date=THIS)
with app.app_context():
    db.session.add(TransactionPayment(transaction_id=D, amount=4000, received_on=THIS))
    db.session.commit()
# Paid in full — expects nothing more.
E = make(reference='TR-0005', status='Paid', agreed_value=100000, fee_type='Percentage',
         fee_percent=2.0, completion_date=LAST, invoice_date=LAST, payment_due_date=THIS)
with app.app_context():
    db.session.add(TransactionPayment(transaction_id=E, amount=2400, received_on=THIS))
    db.session.commit()
# Fallen Through and archived — expect nothing, ever.
F = make(reference='TR-0006', status='Fallen Through', agreed_value=900000,
         fee_type='Percentage', fee_percent=5.0, expected_completion_date=NEXT)
G = make(reference='TR-0007', status='Archived', agreed_value=800000,
         fee_type='Percentage', fee_percent=5.0, expected_completion_date=NEXT)
# Terms Agreed with no expected date — cannot be placed in a period.
H = make(reference='TR-0008', status='Terms Agreed', agreed_value=250000,
         fee_type='Percentage', fee_percent=2.0)

with app.app_context():
    assert expected_stage(Transaction.query.get(A)) == 'terms'
    assert expected_stage(Transaction.query.get(B)) == 'solicitors'
    assert expected_stage(Transaction.query.get(C)) == 'awaiting'
    assert expected_stage(Transaction.query.get(D)) == 'invoiced'
    for gone in (E, F, G):
        assert expected_stage(Transaction.query.get(gone)) is None, gone
print('1. each transaction sits in exactly one expected stage, or none at all')

e = chart('expected')
near(col(e, KEY_NEXT)['stages']['terms']['value'], 8000, 'terms agreed, next month')
near(col(e, KEY_NEXT)['stages']['solicitors']['value'], 3000, 'solicitors, next month')
near(col(e, KEY_THIS)['stages']['awaiting']['value'], 5000, 'completed awaiting invoice')
# Only the £8,000 still owed on the invoice, and never more than the commission.
near(col(e, KEY_THIS)['stages']['invoiced']['value'], 8000, 'invoiced awaiting payment')
print('2. expected commission lands in the right period from the right date')

near(e['total'], 8000 + 3000 + 5000 + 8000, 'total expected')
assert e['count'] == 4, e['count']
assert e['undated'] == 1, 'the transaction with no expected date was not reported'
assert e['money'] is True and e['zero'] == '£0'
print('3. £24,000 expected across four transactions; the undated one is reported')

# Nothing may be counted twice.
with app.app_context():
    seen = {}
    for t in counting_transactions():
        st = expected_stage(t)
        if st:
            assert t.id not in seen, f'{t.reference} counted in two stages'
            seen[t.id] = st
    # Five have a stage; the one with no expected date cannot be placed in a
    # column, which is why the chart counts four and reports the fifth.
    assert len(seen) == 5, len(seen)
print('4. no transaction appears in more than one stage')

# VAT is never counted as commission.
near(e['total'], 24000, 'the total must exclude VAT')
assert e['total'] != 28800, 'VAT crept into the expected total'
print('5. expected commission is net of VAT')


# ═══ Number of Transactions ═════════════════════════════════════════════════

c = chart('count')
assert c['money'] is False and c['zero'] == '0', 'the count view is using a money axis'
assert c['axis'] == [f'{round(c["peak"] * f):,}' for f in (1, .75, .5, .25)], c['axis']
assert '£' not in ''.join(c['axis']), 'the count axis is in pounds'
print('6. the count view uses a whole-number axis, not a currency one')

with app.app_context():
    assert count_stage(Transaction.query.get(D)) == 'billed', 'part paid should sit with billed'
    assert count_stage(Transaction.query.get(F)) == 'fallen'
    assert count_stage(Transaction.query.get(G)) is None, 'archived is not a counting stage'
totals = {}
for column in c['columns']:
    for key, _lbl, _col in COUNT_STAGES:
        totals[key] = totals.get(key, 0) + column['stages'][key]['value']
assert totals['terms'] == 2, totals
assert totals['solicitors'] == 1 and totals['completed'] == 1
assert totals['billed'] == 1 and totals['paid'] == 1 and totals['fallen'] == 1
print('7. every stage counts the right number, and fallen through is included')

assert c['total'] == 7, c['total']
with app.app_context():
    once = [t for t in Transaction.query.all() if count_stage(t)]
    assert len(once) == 7, len(once)
    assert len({t.id for t in once}) == 7, 'a transaction was counted twice'
print('8. seven transactions, each counted once')


# ═══ Performance Against Target ═════════════════════════════════════════════

t0 = chart('target')
assert t0['any_target'] is False, 'a target appeared from nowhere'
assert all(x['target'] is None and x['achieved'] is None for x in t0['columns'])
assert all(x['state'] == 'none' for x in t0['columns'])
assert t0['total_target'] is None and t0['total_achieved'] is None
print('9. with nothing entered there is no target, not a target of nought')

html = cl.get('/transactions?view=target').get_data(as_text=True)
assert 'No target set' in html, 'the page does not say there is no target'
assert 'Set targets' in html, 'there is no way to add one'
print('10. the chart says "No target set" and offers a way to add one')

# Secured this month: only C completed this month, on a £5,000 fixed fee.
near(col(t0, KEY_THIS)['secured'], 5000, 'commission secured this month')

# ── Targets, and the colours ──
r = cl.post('/transactions/targets', data={
    'year': THIS.year,
    f'month:{KEY_THIS}': '4000',                      # beaten
    f'month:{LAST.year}-{LAST.month:02d}': '20000',   # missed
}, follow_redirects=True)
assert r.status_code == 200
t1 = chart('target')
this_col = col(t1, KEY_THIS)
near(this_col['target'], 4000, 'the target saved')
near(this_col['achieved'], 125.0, 'per cent achieved')
near(this_col['variance'], 1000, 'amount above target')
assert this_col['state'] == 'over', this_col['state']
assert this_col['target_is_own'] is True
print('11. £5,000 against a £4,000 target reads as 125% and £1,000 above')

last_col = col(t1, f'{LAST.year}-{LAST.month:02d}')
near(last_col['target'], 20000, 'last month target')
# Two completed last month: £10,000 on TR-0004 and £2,000 on TR-0005.
near(last_col['secured'], 12000, 'commission secured last month')
near(last_col['achieved'], 60.0, 'per cent of a £20,000 target')
assert last_col['state'] == 'under', last_col['state']
assert last_col['variance'] < 0, 'a miss should read as below target'
print('12. a month well short of its target is marked as below')

# Amber sits between the two.
with app.app_context():
    CommissionTarget.query.filter_by(period_key=KEY_THIS).delete()
    db.session.add(CommissionTarget(period_type='month', period_key=KEY_THIS, amount=5500))
    db.session.commit()
amber = col(chart('target'), KEY_THIS)
near(amber['achieved'], round(5000 / 5500 * 100, 1), 'per cent achieved')
assert amber['state'] == 'near', f"5,000 of 5,500 should be near, got {amber['state']}"
print('13. close to target reads as near, not as a miss')


# ── Quarters come from their months unless given their own ──
with app.app_context():
    q = (THIS.month - 1) // 3 + 1
    for m in range((q - 1) * 3 + 1, (q - 1) * 3 + 4):
        key = f'{THIS.year}-{m:02d}'
        CommissionTarget.query.filter_by(period_key=key).delete()
        db.session.add(CommissionTarget(period_type='month', period_key=key, amount=1000))
    db.session.commit()
    got, own = target_for('quarter', (THIS.year, q))
    near(got, 3000, 'a quarter with no figure of its own')
    assert own is False, 'it should be marked as coming from its months'
    db.session.add(CommissionTarget(period_type='quarter',
                                    period_key=f'{THIS.year}-Q{q}', amount=9000))
    db.session.commit()
    got, own = target_for('quarter', (THIS.year, q))
    near(got, 9000, 'a quarter with its own figure')
    assert own is True, 'its own figure should be marked as its own'
print('14. a quarter adds up its months, unless it has a figure of its own')

with app.app_context():
    got, _ = target_for('year', (THIS.year, 0))
    near(got, 3000, 'a year with no figure of its own adds up its months')
    db.session.add(CommissionTarget(period_type='year', period_key=str(THIS.year), amount=50000))
    db.session.commit()
    got, own = target_for('year', (THIS.year, 0))
    near(got, 50000, 'a year with its own figure')
print('15. a year does the same')

# Clearing a box removes the target rather than setting it to nought.
cl.post('/transactions/targets', data={'year': THIS.year, f'month:{KEY_THIS}': ''},
        follow_redirects=True)
with app.app_context():
    assert CommissionTarget.query.filter_by(period_type='month', period_key=KEY_THIS).first() is None
    got, _ = target_for('month', (THIS.year, THIS.month))
    assert got is None, 'clearing a target left a nought behind'
print('16. clearing a target removes it rather than setting it to nought')


# ═══ Empty periods, and the awkward ones ════════════════════════════════════

with app.app_context():
    empty = transaction_chart([], 'month', 'expected', everyone=[])
    assert empty['peak'] == 0 and empty['total'] == 0
    assert len(empty['columns']) == 12
    assert all(not x['segments'] for x in empty['columns'])
    blank = transaction_chart([], 'month', 'count', everyone=[])
    assert blank['peak'] == 0 and blank['total'] == 0
    # A target still draws with no transactions behind it — that is the point
    # of a target — but nothing is reported as secured.
    tgt = transaction_chart([], 'month', 'target', everyone=[])
    assert tgt['total'] == 0, 'commission appeared from no transactions'
    assert all(x['secured'] == 0 and x['billed'] == 0 for x in tgt['columns'])
    assert all(x['state'] in ('under', 'none') for x in tgt['columns'])
print('17. an empty book draws an empty chart and divides by nothing')

# Nothing is expected four months out, but the column is still drawn.
far = _month_shift(THIS, 4)
quiet = col(chart('expected'), f'{far.year}-{far.month:02d}')
assert quiet['total'] == 0 and quiet['count'] == 0 and not quiet['segments']
# The expected view reaches forward, because that is where the money is.
labels = [x['period_key'] for x in chart('expected')['columns']]
assert KEY_NEXT in labels, 'the expected view cannot see next month'
assert labels[-1] > KEY_THIS, 'the expected view only looks backwards'
print('18. a period with nothing in it is empty, not missing')


# ═══ The page ═══════════════════════════════════════════════════════════════

def page(url):
    r = cl.get(url)
    assert r.status_code == 200, f'{url} returned {r.status_code}'
    return r.get_data(as_text=True)


# ── The three tabs, with Expected first ──
h = page('/transactions')
assert [k for k, _ in TRANSACTION_VIEWS] == ['expected', 'count', 'target']
for _key, label in TRANSACTION_VIEWS:
    assert f'>{label}</a>' in h, f'the {label} tab is missing'
on = re.findall(r'class="is-on"[^>]*>([^<]+)</a>', h)
assert 'Expected Commission' in h.split('fin-views')[1][:600]
assert 'aria-selected="true"' in h.split('fin-views')[1].split('Expected Commission')[0], \
    'Expected Commission is not the one selected by default'
print('19. three tabs, with Expected Commission chosen to begin with')

# ── Title, legend and axis follow the view ──
for view, title, stages in [
        ('expected', 'Expected commission', EXPECTED_STAGES),
        ('count', 'Number of transactions', COUNT_STAGES),
        ('target', 'Performance against target', None)]:
    body = page(f'/transactions?view={view}')
    assert f'<h3>{title}</h3>' in body, f'the title did not change for {view}'
    if stages:
        for _k, label, _c in stages:
            assert label in body, f'{label} missing from the {view} legend'
    else:
        for label in ['Commission secured', 'Commission Billed', 'Target']:
            assert label in body, f'{label} missing from the target legend'
print('20. the title and the legend change with the view')

money_axis = page('/transactions?view=expected').split('fin-axis')[1].split('</div>')[0]
count_axis = page('/transactions?view=count').split('fin-axis')[1].split('</div>')[0]
assert '£' in money_axis, 'the money view has no currency axis'
assert '£' not in count_axis, 'the count view is showing a currency axis'
print('21. money and counts never share an axis')

# ── The total shown matches the chart ──
assert money_gbp(chart('expected')['total']) in page('/transactions?view=expected')
assert f">{chart('count')['total']}</span>" in page('/transactions?view=count').replace(',', '')
print('22. the total on show is the one the chart added up')

# ── Tooltips carry period, stage, count and value ──
body = page('/transactions?view=expected')
tips = re.findall(r'<div class="fin-tip" role="tooltip">(.*?)</div>', body, re.S)
filled = [t for t in tips if 'Terms Agreed' in t]
assert filled, 'no tooltip names the stage its column is made of'
tip = filled[0]
assert re.search(r'<b>[^<]+</b>', tip), 'the tooltip does not name the period'
assert 'Terms Agreed' in tip, 'the tooltip does not name the stage'
assert 'transaction' in tip, 'the tooltip does not give a count'
assert '£' in tip, 'the tooltip does not give a value'
assert 'Total' in tip, 'the tooltip does not total the column'
target_tip = page('/transactions?view=target')
for word in ['Target', 'Secured', 'Billed', 'Achieved']:
    assert word in target_tip, f'the target tooltip has no {word}'
print('23. tooltips carry the period, the stage, the count and the value')

# ── Clicking a segment shows exactly what it counted ──
cnt = chart('count')
picked = None
for column in cnt['columns']:
    for seg in column['segments']:
        if seg['count']:
            picked = (column['period_key'], seg['key'], seg['count'], cnt['period'])
            break
    if picked:
        break
assert picked, 'no segment to click'
bucket, stage, expect, per = picked
drill = page(f'/transactions?view=count&stage={stage}&bucket={bucket}&period={per}&show=all')
rows = drill.split('class="fin-table"')[1].split('<tbody>')[1].split('</tbody>')[0]
assert len(re.findall(r'>(TR-\d{4})</a>', rows)) == expect, \
    f'clicking {stage} in {bucket} showed the wrong transactions'
assert 'behind one column of the chart' in drill
print('24. clicking a segment shows exactly the transactions it counted')

# ── Shared controls ──
controls = page('/transactions')
for name in ['q', 'type', 'status', 'fee_earner_id', 'client', 'property', 'from', 'to']:
    assert f'name="{name}"' in controls, f'the {name} control is missing'
assert 'Reset filters' in controls, 'there is no reset'
for label in ['>Month<', '>Quarter<', '>Year<']:
    assert label in controls, f'the {label} period control is missing'
print('25. period, date range, fee earner, type, client and reset are all there')

# ── The filters narrow the chart ──
wide = chart('expected')['total']
narrow = page(f'/transactions?view=expected&fee_earner_id={BEN_ID}')
with app.app_context():
    everyone = Transaction.query.all()
    rows = [t for t in everyone if t.counts_towards_totals and t.fee_earner_id == BEN_ID]
    only = transaction_chart(rows, 'month', 'expected', everyone=rows)
assert only['total'] < wide, 'filtering did not narrow the chart'
assert money_gbp(only['total']) in narrow, 'the chart did not follow the fee-earner filter'
print('26. the chart follows the filters')

reset = page(f'/transactions?view=count&period=quarter')
assert 'view=count' in reset and 'period=quarter' in reset, \
    'reset does not keep the view and period being looked at'
print('27. reset clears the filters but keeps the view you are on')

# ── Only the right people can set a target ──
viewer = app.test_client()
viewer.post('/login', data={'username': 'reader', 'password': 'pw'}, follow_redirects=True)
assert viewer.get('/transactions?view=target').status_code == 200
assert viewer.post('/transactions/targets',
                   data={'year': THIS.year, f'month:{KEY_THIS}': '999999'}).status_code == 403
with app.app_context():
    assert CommissionTarget.query.filter_by(period_key=KEY_THIS).first() is None, \
        'a viewer set a target'
print('28. a viewer can read the chart but cannot set a target')

# ── Nothing is written into the template ──
tpl = open("/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/"
           "cr-property-db/templates/transactions/list.html").read()
figures = [m for m in re.findall(r'£[\d,]+\.?\d*', tpl) if m != '£0']
assert not figures, f'figures written into the chart template: {figures}'
print('29. no figure is written into the chart template')

print('\nCHART VIEWS: ALL CHECKS PASSED')
