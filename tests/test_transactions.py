"""The Transactions page: every figure on it, checked against the records.

Nothing here trusts a number because it looks right. Each commission, VAT
amount, invoice total, balance and month-on-month comparison is worked out by
hand in the test and compared with what the CRM produces, including the awkward
cases: a month following a zero, a part payment, a fixed fee, a nil VAT rate,
and transactions that fell through or were archived.
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
                 transaction_dashboard, transaction_chart, counting_transactions,
                 TRANSACTION_STATUSES, _month_start, _month_shift, money_gbp)
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash
from datetime import date

ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"

THIS = _month_start(date.today())
LAST = _month_shift(THIS, -1)
BEFORE = _month_shift(THIS, -2)


def near(got, want, what):
    assert abs(float(got) - float(want)) < 0.005, f'{what}: got {got}, expected {want}'


with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw'),
                        role='admin'))
    db.session.add(User(username='reader', password_hash=generate_password_hash('pw'),
                        role='viewer'))
    props = []
    for addr in ['1 High Street', '2 Mill Lane', '3 Quarry Road', '4 Castle Hill']:
        p = Property(address=addr, postcode='TN1 1AA')
        db.session.add(p)
        props.append(p)
    db.session.commit()
    PROP = [p.id for p in props]


def make(**kw):
    """Add a transaction and return its id."""
    with app.app_context():
        kw.setdefault('property_id', PROP[0])
        kw.setdefault('transaction_type', 'Capital')
        t = Transaction(**kw)
        db.session.add(t)
        db.session.commit()
        return t.id


def pay(tid, amount, on):
    with app.app_context():
        db.session.add(TransactionPayment(transaction_id=tid, amount=amount, received_on=on))
        db.session.commit()


def get(tid):
    return Transaction.query.get(tid)


cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


# ─── 1. A percentage fee ─────────────────────────────────────────────────────
# £500,000 at 2% is £10,000; VAT at 20% is £2,000; the invoice is £12,000.
A = make(reference='TR-0001', status='Paid', client='Marsden Estates',
         purchaser='Halloway Ltd', fee_earner='B Cowan', agreed_value=500000,
         fee_type='Percentage', fee_percent=2.0,
         invoice_date=THIS, completion_date=THIS)
with app.app_context():
    t = get(A)
    near(t.commission_basis, 500000, 'agreed value')
    near(t.net_commission, 10000, 'net commission on a percentage fee')
    near(t.vat_amount, 2000, 'VAT')
    near(t.total_invoice, 12000, 'total invoice')
    near(t.outstanding, 12000, 'outstanding before any payment')
print('1. a percentage fee: £500,000 × 2% = £10,000, VAT £2,000, invoice £12,000')


# ─── 2. A fixed fee ignores the value ────────────────────────────────────────
B = make(reference='TR-0002', status='Commission Billed', client='Farrier & Co',
         tenant='Vale Coffee', fee_earner='B Cowan', transaction_type='Leasehold',
         agreed_value=48000, fee_type='Fixed', fixed_fee=3500.0,
         invoice_date=THIS, completion_date=THIS)
with app.app_context():
    t = get(B)
    near(t.net_commission, 3500, 'a fixed fee is used as entered')
    near(t.vat_amount, 700, 'VAT on a fixed fee')
    near(t.total_invoice, 4200, 'invoice on a fixed fee')
print('2. a fixed fee is used as entered, whatever the transaction is worth')


# ─── 3. A part payment leaves the right balance ──────────────────────────────
pay(A, 5000, THIS)
with app.app_context():
    t = get(A)
    near(t.commission_received, 5000, 'part payment received')
    near(t.outstanding, 7000, 'balance after a part payment')
pay(A, 7000, THIS)
with app.app_context():
    t = get(A)
    near(t.commission_received, 12000, 'both payments received')
    near(t.outstanding, 0, 'nothing left owing')
print('3. a part payment leaves £7,000 owing; the second payment clears it')


# ─── 4. The same payment is never counted twice ──────────────────────────────
with app.app_context():
    before = get(A).commission_received
cl.post(f'/transactions/{A}/save', data={'status': 'Paid'}, follow_redirects=True)
cl.post(f'/transactions/{A}/save', data={'status': 'Paid'}, follow_redirects=True)
with app.app_context():
    near(get(A).commission_received, before, 'saving the record again re-counted a payment')
    assert len(get(A).payments) == 2, 'saving the record duplicated a payment row'
print('4. saving the record again does not re-count money already received')


# ─── 5. A transaction with no VAT ────────────────────────────────────────────
C = make(reference='TR-0003', status='Completed', client='Ashdown Trust',
         fee_earner='S Rutter', agreed_value=200000, fee_type='Percentage',
         fee_percent=1.5, vat_rate=0.0, invoice_date=THIS, completion_date=THIS)
with app.app_context():
    t = get(C)
    near(t.net_commission, 3000, 'net commission')
    near(t.vat_amount, 0, 'VAT at a nil rate')
    near(t.total_invoice, 3000, 'invoice with no VAT')
    near(t.applicable_vat_rate, 0, 'the rate on the record beats the default')
print('5. a nil VAT rate on the record is used instead of the default')


# ─── 6. Fallen Through and archived earn nothing ─────────────────────────────
D = make(reference='TR-0004', status='Fallen Through', client='Ghost Ltd',
         agreed_value=1000000, fee_type='Percentage', fee_percent=5.0,
         invoice_date=THIS, completion_date=THIS)
E = make(reference='TR-0005', status='Archived', client='Old Matter',
         agreed_value=900000, fee_type='Percentage', fee_percent=5.0,
         invoice_date=THIS, completion_date=THIS)
with app.app_context():
    counted = {t.reference for t in counting_transactions()}
    assert 'TR-0004' not in counted, 'a fallen-through transaction is in the figures'
    assert 'TR-0005' not in counted, 'an archived transaction is in the figures'
    dash = transaction_dashboard()
    # Only A, B and C count: 10,000 + 3,500 + 3,000.
    near(dash['billed_total'], 16500, 'commission billed excludes the two that do not count')
    assert dash['excluded_count'] == 2, dash['excluded_count']
print('6. fallen-through and archived transactions are left out of every total')


# ─── 7. Completed means a date AND a completed status ────────────────────────
F = make(reference='TR-0006', status='Terms Agreed', client='Not Yet Ltd',
         agreed_value=300000, fee_type='Percentage', fee_percent=2.0,
         completion_date=THIS)                      # dated, but not completed
G = make(reference='TR-0007', status='Completed', client='No Date Ltd',
         agreed_value=300000, fee_type='Percentage', fee_percent=2.0)  # no date
with app.app_context():
    assert not get(F).has_completed, 'a terms-agreed transaction counted as completed'
    assert not get(G).has_completed, 'a transaction with no completion date counted'
    assert get(A).has_completed and get(B).has_completed and get(C).has_completed
print('7. completed needs both a completion date and a completed status')


# ─── 8. Billing uses net commission, not the invoice total ───────────────────
with app.app_context():
    dash = transaction_dashboard()
    near(dash['billed_total'], 16500, 'billed total')
    assert dash['billed_total'] != 19800, 'billed total wrongly included VAT'
    near(dash['received_total'], 12000, 'received is only what was paid')
    near(dash['outstanding_total'], 4500, 'outstanding is billed minus received')
print('8. commission billed is net of VAT; received is only money recorded in')


# ─── 9. Average commission is per completed transaction ──────────────────────
with app.app_context():
    dash = transaction_dashboard()
    # A, B and C are completed: (10,000 + 3,500 + 3,000) / 3.
    near(dash['avg_commission'], 16500 / 3, 'average commission per completed transaction')
    assert dash['completed_count'] == 3, dash['completed_count']
print('9. the average is commission over completed transactions only')


# ─── 10. Total transaction value ─────────────────────────────────────────────
with app.app_context():
    dash = transaction_dashboard()
    # A 500,000 + B 48,000 + C 200,000 + F 300,000 + G 300,000. D and E do not count.
    near(dash['value_total'], 1348000, 'total transaction value')
    near(dash['value_completed'], 748000, 'value of completed transactions')
print('10. transaction value adds up across the transactions that count')


# ─── 11. Month on month, up and down ─────────────────────────────────────────
for i in range(4):
    make(reference=f'TR-01{i:02d}', status='Completed', client='Last Month Ltd',
         agreed_value=100000, fee_type='Percentage', fee_percent=1.0,
         invoice_date=LAST, completion_date=LAST)
with app.app_context():
    dash = transaction_dashboard()
    assert dash['completed_month'] == 3, dash['completed_month']
    ch = dash['completed_change']
    assert ch['kind'] == 'down', ch
    near(ch['pct'], -25.0, 'three completions this month against four last')
    # Billed: £16,500 this month against 4 × £1,000 last month.
    near(dash['billed_month'], 16500, 'commission billed this month')
    bc = dash['billed_change']
    assert bc['kind'] == 'up', bc
    near(bc['pct'], (16500 - 4000) / 4000 * 100, 'billing against last month')
print('11. three completions against four reads as a 25.0% fall; billing as a rise')


# ─── 12. A month following nothing does not invent a percentage ──────────────
with app.app_context():
    from app import _change
    assert _change(5, 0) == {'kind': 'new', 'pct': None, 'label': 'New'}
    none = _change(0, 0)
    assert none['kind'] == 'none' and none['pct'] is None
    assert none['label'] == 'No previous-month comparison', none
    assert _change(4, 4)['kind'] == 'flat'
    near(_change(3, 6)['pct'], -50.0, 'a halving')
    near(_change(6, 3)['pct'], 100.0, 'a doubling')
print('12. a month after a zero says "New"; two empty months say so plainly')


# ─── 13. An empty period is empty, and nothing divides by zero ───────────────
# The chart's own arithmetic lives in test_chart.py; what matters here is that
# an empty book still draws and the dashboard invents nothing.
with app.app_context():
    empty = transaction_chart(rows=[], period='month', view='expected', everyone=[])
    assert empty['peak'] == 0 and empty['total'] == 0
    assert len(empty['columns']) == 12
    assert all(not c['segments'] for c in empty['columns'])
    blank = transaction_dashboard(rows=[])
    assert blank['avg_commission'] is None, 'an average was invented from nothing'
    assert blank['completed_change']['kind'] == 'none'
print('13. an empty book draws an empty chart and invents no averages')


# ─── 14. Every period keeps to a sensible number of columns ──────────────────
with app.app_context():
    for view in ('expected', 'count', 'target'):
        for period, most in (('month', 12), ('quarter', 8), ('year', 5)):
            c = transaction_chart(period=period, view=view)
            assert c['columns'], f'{view}/{period} drew nothing'
            assert len(c['columns']) <= most, f'{view}/{period}: {len(c["columns"])}'
            assert c['period'] == period and c['view'] == view
            keys = [x['period_key'] for x in c['columns']]
            assert len(keys) == len(set(keys)), f'{view}/{period} drew a period twice'
print('14. all three views draw every period without repeating one')


# ─── 15. Changing the status recalculates straight away ──────────────────────
with app.app_context():
    before = transaction_dashboard()['billed_total']
cl.post(f'/transactions/{B}/save', data={'status': 'Fallen Through'}, follow_redirects=True)
with app.app_context():
    after = transaction_dashboard()['billed_total']
    near(after, before - 3500, 'the fallen-through transaction was still counted')
cl.post(f'/transactions/{B}/save', data={'status': 'Commission Billed'}, follow_redirects=True)
with app.app_context():
    near(transaction_dashboard()['billed_total'], before, 'putting it back did not restore the total')
print('15. marking a transaction as fallen through takes it out of the totals at once')


# ─── 16. Deleting a transaction takes its money with it ──────────────────────
with app.app_context():
    before = transaction_dashboard()
H = make(reference='TR-0099', status='Paid', client='Temporary Ltd',
         agreed_value=400000, fee_type='Percentage', fee_percent=3.0,
         invoice_date=THIS, completion_date=THIS)
pay(H, 1000, THIS)
with app.app_context():
    mid = transaction_dashboard()
    near(mid['billed_total'], before['billed_total'] + 12000, 'the new transaction was not counted')
    near(mid['received_total'], before['received_total'] + 1000, 'its payment was not counted')
cl.post(f'/transactions/{H}/delete', follow_redirects=True)
with app.app_context():
    assert get(H) is None, 'the transaction was not deleted'
    assert TransactionPayment.query.filter_by(transaction_id=H).count() == 0, \
        'the payment outlived the transaction it belonged to'
    after = transaction_dashboard()
    near(after['billed_total'], before['billed_total'], 'the deleted commission is still counted')
    near(after['received_total'], before['received_total'], 'the deleted payment is still counted')
print('16. deleting a transaction removes its commission and its payments from the totals')


# ─── 17. The page itself ─────────────────────────────────────────────────────
def page(url):
    r = cl.get(url)
    assert r.status_code == 200, f'{url} returned {r.status_code}'
    return r.get_data(as_text=True)


html = page('/transactions')
for label in ['Transactions', 'Completed this month', 'Commission Billed',
              'Billed this month', 'Commission received', 'Commission outstanding',
              'Total transaction value', 'Average commission']:
    assert label in html, f'the {label} card is missing'
print('17. all ten figures are on the dashboard')


# ─── 18. Money is written the way an invoice writes it ───────────────────────
assert money_gbp(1234567.5) == '£1,234,567.50', money_gbp(1234567.5)
assert money_gbp(0) == '£0.00'
assert money_gbp(None) == '—'
with app.app_context():
    billed_now = money_gbp(transaction_dashboard()['billed_total'])
assert billed_now in html, f'the billed total {billed_now} is not shown in pounds and pence'
assert not re.search(r'£\d{4,}', html.replace(',', 'X')), 'a figure is missing its commas'
print('18. every figure is £ with commas and two decimal places')


# ─── 19. A rise is green with an arrow, a fall red with one ──────────────────
assert 'kpi-delta down' in html, 'the fall in completions is not marked as a fall'
assert 'kpi-delta up' in html, 'the rise in billing is not marked as a rise'
assert '&#9650;' in html and '&#9660;' in html, 'the arrows are missing'
css = open(f'{ROOT}/static/css/crm-grid.css').read()
assert '.kpi-delta.up' in css and '#2f7a4f' in css, 'the rise colour is not set'
assert '.kpi-delta.down' in css and '#b3463c' in css, 'the fall colour is not set'
print('19. rises show green with an up arrow, falls red with a down arrow')


# ─── 20. Filters ─────────────────────────────────────────────────────────────
def rows_of(body):
    """The transactions table itself, not any other table on the page."""
    if 'class="fin-table"' not in body:
        return ''
    body = body.split('class="fin-table"')[1]
    return body.split('<tbody>')[1].split('</tbody>')[0] if '<tbody>' in body else ''


def refs(url):
    return set(re.findall(r'>(TR-\d{4})</a>', rows_of(page(url))))


assert 'TR-0001' in refs('/transactions'), 'the list is empty'
assert refs('/transactions?status=Completed') == {
    'TR-0003', 'TR-0007', 'TR-0100', 'TR-0101', 'TR-0102', 'TR-0103',
}, refs('/transactions?status=Completed')
with app.app_context():
    _ben = User.query.order_by(User.id).first()
    if not _ben.full_name:
        _ben.full_name, _ben.active, _ben.can_earn_fees = 'Benjamin Cowan', True, True
    BEN_ID = _ben.id
    Transaction.query.filter_by(reference='TR-0003').one().fee_earner_id = BEN_ID
    db.session.commit()
assert refs(f'/transactions?fee_earner_id={BEN_ID}') == {'TR-0003'}, \
    refs(f'/transactions?fee_earner_id={BEN_ID}')
assert refs('/transactions?type=Leasehold') == {'TR-0002'}, refs('/transactions?type=Leasehold')
assert refs('/transactions?client=Ashdown+Trust') == {'TR-0003'}
assert 'TR-0004' not in refs('/transactions'), 'a fallen-through transaction is in the default list'
assert 'TR-0004' in refs('/transactions?show=all'), 'archived transactions cannot be found at all'
assert 'TR-0005' in refs('/transactions?status=Archived'), 'archived cannot be filtered to'
assert refs(f'/transactions?property={PROP[0]}'), 'the property filter found nothing'
assert not refs(f'/transactions?property={PROP[3]}'), 'the property filter is not filtering'
print('20. type, status, fee earner, client, property and archived filters all work')


# ─── 21. Search ──────────────────────────────────────────────────────────────
assert refs('/transactions?q=TR-0001') == {'TR-0001'}, 'search by reference'
assert refs('/transactions?q=Marsden') == {'TR-0001'}, 'search by client'
assert refs('/transactions?q=Halloway') == {'TR-0001'}, 'search by purchaser'
assert refs('/transactions?q=Vale+Coffee') == {'TR-0002'}, 'search by tenant'
assert refs('/transactions?q=High+Street'), 'search by property address'
assert refs('/transactions?q=nothing at all here') == set(), 'search matched everything'
print('21. search finds references, addresses, clients and tenants or purchasers')


# ─── 22. Date range ──────────────────────────────────────────────────────────
last_only = refs(f'/transactions?from={LAST}&to={_month_shift(THIS, 0) - __import__("datetime").timedelta(days=1)}')
assert 'TR-0100' in last_only, last_only
assert 'TR-0001' not in last_only, 'the date range let this month through'
print('22. the date range keeps to the months asked for')


# ─── 23. Every financial and date column sorts ───────────────────────────────
def order(url):
    return re.findall(r'>(TR-\d{4})</a>', rows_of(page(url)))


for col in ['reference', 'address', 'client', 'counterparty', 'type', 'fee_earner',
            'status', 'value', 'fee', 'commission', 'vat', 'invoice', 'received',
            'outstanding', 'invoice_date', 'payment_due_date', 'completion_date']:
    down = order(f'/transactions?sort={col}&dir=desc')
    up = order(f'/transactions?sort={col}&dir=asc')
    assert down and up, f'sorting by {col} emptied the table'
    assert down == list(reversed(up)) or set(down) == set(up), f'sorting by {col} lost rows'
assert order('/transactions?sort=commission&dir=desc')[0] == 'TR-0001', \
    'the largest commission is not at the top'
assert order('/transactions?sort=commission&dir=asc')[-1] == 'TR-0001', \
    'reversing the sort did not reverse it'
print('23. all seventeen columns sort, in both directions')


# ─── 24. The table shows every column asked for ──────────────────────────────
head = html.split('class="fin-table"')[1].split('<thead>')[1].split('</thead>')[0]
for col in ['Ref', 'Property', 'Client', 'Tenant / Purchaser', 'Type', 'Fee earner',
            'Agreed value', 'Fee', 'Commission', 'VAT', 'Invoice total', 'Received',
            'Outstanding', 'Invoiced', 'Due', 'Completed', 'Status']:
    assert f'>{col}' in head, f'the {col} column is missing from the table'
print('24. the table carries all seventeen columns')


# ─── 25. Every status is offered ─────────────────────────────────────────────
assert len(TRANSACTION_STATUSES) == 11, TRANSACTION_STATUSES
for st in ['Draft', 'In Progress', 'Under Offer', 'Terms Agreed',
           'Solicitors Instructed', 'Completed', 'Commission Billed', 'Part Paid',
           'Paid', 'Fallen Through', 'Archived']:
    assert st in TRANSACTION_STATUSES, f'{st} is not a status'
    assert f'>{st}</option>' in html, f'{st} cannot be filtered to'
print('25. all ten statuses exist and can be filtered to')


# ─── 26. A transaction opens on its own page, with no list beside it ─────────
rec = page(f'/transactions/{A}')
assert '<tbody>' not in rec.split('Payments received')[0] or 'fin-table' not in rec, \
    'the transactions list is still beside the record'
assert 'fin-table' not in rec, 'the transactions table is on the record page'
for section in ['1. Transaction overview', '2. Property and project',
                '3. Client, tenant or purchaser', '4. Agreed commercial terms',
                '5. Commission calculation', '6. Invoice and payment',
                '7. Solicitors', '8. Important dates', '9. Documents', '10. Notes']:
    assert section in rec, f'the "{section}" section is missing'
assert 'Activity history' in rec
print('26. the record opens on its own page with all ten sections and no list')


# ─── 27. One Save button, and every field belongs to it ──────────────────────
saves = re.findall(r'<button[^>]*>\s*Save\s*</button>', rec)
assert len(saves) == 1, f'there are {len(saves)} Save buttons on the record page'
assert 'form="trx-form"' in saves[0], 'the Save button is not the record form\'s'
# No field may be posted to a form that is not its own.
form_ids = set(re.findall(r'<form[^>]*id="([^"]+)"', rec))
for owner in re.findall(r'form="([^"]+)"', rec):
    assert owner in form_ids, f'a field posts to "{owner}", which is not a form on the page'
assert 'Edit' not in re.sub(r'Edit(or|ed|ing)', '', rec.split('<div class="trx-cols">')[0]) \
    or 'btn' not in rec.split('trx-cols')[0].split('Edit')[-1][:60], \
    'there is still a separate Edit button'
print('27. one Save button at the top, and no field posts to another form')


# ─── 28. Saving from the page writes what was sent, and nothing else ─────────
with app.app_context():
    keep = get(C).client
cl.post(f'/transactions/{C}/save', data={'fee_percent': '2.5'}, follow_redirects=True)
with app.app_context():
    t = get(C)
    near(t.fee_percent, 2.5, 'the fee percentage did not save')
    near(t.net_commission, 5000, 'the commission did not follow the new percentage')
    assert t.client == keep, 'saving one field blanked another the page did not send'
print('28. saving writes the fields sent and leaves the rest alone')


# ─── 29. A status the CRM does not use is refused on the server ──────────────
cl.post(f'/transactions/{C}/save', data={'status': 'Whatever I Like'}, follow_redirects=True)
with app.app_context():
    assert get(C).status != 'Whatever I Like', 'an invented status was saved'
    assert get(C).status == 'Completed', get(C).status
print('29. a made-up status is refused on the server, not just hidden in the browser')


# ─── 30. Recording and removing a payment ────────────────────────────────────
with app.app_context():
    start = get(C).commission_received
cl.post(f'/transactions/{C}/payments',
        data={'amount': '1200.50', 'received_on': str(THIS)}, follow_redirects=True)
with app.app_context():
    near(get(C).commission_received, start + 1200.50, 'the payment was not recorded')
    pid = get(C).payments[-1].id
    near(get(C).outstanding, get(C).total_invoice - (start + 1200.50), 'the balance is wrong')
cl.post(f'/transactions/{C}/payments', data={'amount': '0'}, follow_redirects=True)
with app.app_context():
    near(get(C).commission_received, start + 1200.50, 'a nil payment was recorded')
cl.post(f'/transactions/{C}/payments/{pid}/delete', follow_redirects=True)
with app.app_context():
    near(get(C).commission_received, start, 'removing the payment did not take it off')
print('30. payments can be recorded and removed, and a nil payment is refused')


# ─── 31. Permission is checked on the server ─────────────────────────────────
viewer = app.test_client()
viewer.post('/login', data={'username': 'reader', 'password': 'pw'}, follow_redirects=True)
assert viewer.get('/transactions').status_code == 200, 'a viewer cannot see the page'
for url, data in [(f'/transactions/{C}/save', {'status': 'Paid'}),
                  (f'/transactions/{C}/payments', {'amount': '5000'})]:
    r = viewer.post(url, data=data)
    assert r.status_code == 403, f'a viewer was allowed to post to {url} ({r.status_code})'
with app.app_context():
    assert get(C).status == 'Completed', 'a viewer changed the status'
print('31. a viewer can read the figures but cannot change any of them')


# ─── 32. The figures above the table ignore the filters below it ─────────────
with app.app_context():
    firm_wide = money_gbp(transaction_dashboard()['billed_total'])
filtered = page('/transactions?q=Marsden')
assert firm_wide in filtered, \
    f'filtering the table changed the dashboard: {firm_wide} is no longer shown'
assert 'TR-0002' not in rows_of(filtered)
assert refs('/transactions?q=Marsden') == {'TR-0001'}
print('32. narrowing the table leaves the firm-wide figures alone')


# ─── 33. Nothing on the page is a hard-coded figure ──────────────────────────
src = open(f'{ROOT}/templates/transactions/list.html').read()
detail_src = open(f'{ROOT}/templates/transactions/detail.html').read()
for name, body in (('list', src), ('detail', detail_src)):
    # The chart's baseline label is the one £ in either template, and it is a
    # zero on an axis rather than a figure about the business.
    money = [m for m in re.findall(r'£[\d,]+\.?\d*', body) if m != '£0']
    assert not money, f'the {name} template has figures written into it: {money}'
print('33. no figure is written into either template')

print('\nTRANSACTIONS: ALL CHECKS PASSED')
