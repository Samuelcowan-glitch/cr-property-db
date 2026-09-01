"""The figures added beside the headline ones.

Sales against lettings, the fee value of stock on the market, the weighted
pipeline, how long things take and how often they land. Each one is worked out
by hand here and compared with what the CRM produces, including the cases where
the honest answer is "not enough recorded to say".
"""
import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
sys.path.insert(0, "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db")

from app import (app, db, Property, Project, Listing, Transaction,
                 TransactionPayment, User, transaction_extras, stock_fee_value,
                 counting_transactions, money_gbp, PIPELINE_WEIGHTS,
                 INSTRUCTION_FOR_SALE, INSTRUCTION_TO_LET, _month_start)
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash
from datetime import date, timedelta

TODAY = date.today()


def near(got, want, what):
    assert abs(float(got) - float(want)) < 0.005, f'{what}: got {got}, expected {want}'


with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw'),
                        role='admin'))
    prop = Property(address='1 High Street', postcode='TN1 1AA')
    db.session.add(prop)
    db.session.commit()
    PROP = prop.id


def extras():
    with app.app_context():
        everyone = Transaction.query.all()
        rows = [t for t in everyone if t.counts_towards_totals]
        return transaction_extras(rows, everyone)


def make(**kw):
    with app.app_context():
        kw.setdefault('property_id', PROP)
        kw.setdefault('transaction_type', 'Capital')
        t = Transaction(**kw)
        db.session.add(t)
        db.session.commit()
        return t.id


cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


# ─── 1. Sales and lettings are counted apart ────────────────────────────────
# Two sales worth £500,000 and £320,000; two lettings worth £48,000 and £72,000.
S1 = make(reference='TR-0001', transaction_type='Capital', status='Paid',
          agreed_value=500000, fee_type='Percentage', fee_percent=2.0,
          fee_earner='B Cowan', invoice_date=TODAY, completion_date=TODAY)
S2 = make(reference='TR-0002', transaction_type='Capital', status='Terms Agreed',
          agreed_value=320000, fee_type='Percentage', fee_percent=2.0,
          fee_earner='B Cowan')
L1 = make(reference='TR-0003', transaction_type='Leasehold', status='Completed',
          agreed_value=48000, fee_type='Fixed', fixed_fee=3500.0,
          fee_earner='S Rutter', completion_date=TODAY)
L2 = make(reference='TR-0004', transaction_type='Leasehold', status='In Progress',
          agreed_value=72000, fee_type='Percentage', fee_percent=10.0,
          fee_earner='S Rutter')

e = extras()
near(e['sales']['value'], 820000, 'sales value')
near(e['lettings']['value'], 120000, 'lettings value')
assert e['sales']['count'] == 2 and e['lettings']['count'] == 2
near(e['sales']['completed_value'], 500000, 'completed sales value')
near(e['lettings']['completed_value'], 48000, 'completed lettings value')
assert e['sales']['completed'] == 1 and e['lettings']['completed'] == 1
print('1. sales £820,000 and lettings £120,000, counted apart')


# ─── 2. The two halves add up to the whole ──────────────────────────────────
from app import transaction_dashboard
with app.app_context():
    dash = transaction_dashboard()
near(e['sales']['value'] + e['lettings']['value'], dash['value_total'],
     'sales plus lettings should equal the total transaction value')
near(e['sales']['completed_value'] + e['lettings']['completed_value'],
     dash['value_completed'], 'completed sales plus lettings')
print('2. sales and lettings add up to the total transaction value')


# ─── 3. A deal is only pipeline until it completes ──────────────────────────
# In Progress £7,200 at 10%; Terms Agreed £6,400 at 50%.
near(e['pipeline'], 7200 + 6400, 'the whole pipeline, undiscounted')
near(e['weighted'], 7200 * 0.10 + 6400 * 0.50, 'the pipeline discounted by stage')
assert e['in_play'] == 2, e['in_play']
print('3. £13,600 in play discounts to £3,920 by stage')


# ─── 4. Completed and lost deals are not pipeline ───────────────────────────
cl.post(f'/transactions/{S2}/save', data={'status': 'Completed',
                                          'completion_date': str(TODAY)},
        follow_redirects=True)
e = extras()
near(e['pipeline'], 7200, 'a completed deal is still counted as pipeline')
near(e['weighted'], 720, 'the weighting did not follow the status')
cl.post(f'/transactions/{L2}/save', data={'status': 'Fallen Through'}, follow_redirects=True)
e = extras()
near(e['pipeline'], 0, 'a fallen-through deal is still in the pipeline')
near(e['weighted'], 0, 'a fallen-through deal is still weighted')
assert e['in_play'] == 0
print('4. completing or losing a deal takes it out of the pipeline at once')


# ─── 5. Won against lost ────────────────────────────────────────────────────
e = extras()
# Three completed, one fallen through.
near(e['conversion'], 3 / 4 * 100, 'conversion rate')
assert e['lost_count'] == 1, e['lost_count']
print('5. three won against one lost reads as 75.0%')


# ─── 6. Nothing settled means no rate, not nought per cent ──────────────────
with app.app_context():
    blank = transaction_extras([], [])
assert blank['conversion'] is None, 'a conversion rate was invented from nothing'
assert blank['days_to_complete'] is None and blank['days_to_paid'] is None
assert blank['weighted'] == 0 and blank['board'] == []
print('6. with nothing settled the page says so rather than showing 0%')


# ─── 7. How long a deal takes, measured from the instruction ────────────────
with app.app_context():
    p = Project(name='Vale Industrial', instruction_type=INSTRUCTION_TO_LET,
                instruction_date=TODAY - timedelta(days=90), property_id=PROP)
    db.session.add(p)
    db.session.commit()
    PID = p.id
    t = Transaction.query.get(L1)
    t.project_id = PID
    db.session.commit()
e = extras()
# L1 completes today, 90 days after its instruction. S1 and S2 have no project,
# so they fall back to their own transaction date, which is not set — they are
# left out rather than guessed at.
assert e['completions_measured'] == 1, e['completions_measured']
assert e['days_to_complete'] == 90, e['days_to_complete']
print('7. instruction to completion measures 90 days, from the dates recorded')


# ─── 8. A deal with no start date is left out, not counted as nought ────────
with app.app_context():
    t = Transaction.query.get(S1)
    t.transaction_date = TODAY - timedelta(days=30)
    db.session.commit()
e = extras()
assert e['completions_measured'] == 2, e['completions_measured']
assert e['days_to_complete'] == round((90 + 30) / 2), e['days_to_complete']
print('8. a deal with no start date is left out rather than counted as nought')


# ─── 9. How long the office waits to be paid ────────────────────────────────
with app.app_context():
    db.session.add(TransactionPayment(transaction_id=S1, amount=6000,
                                      received_on=TODAY + timedelta(days=20)))
    db.session.add(TransactionPayment(transaction_id=S1, amount=6000,
                                      received_on=TODAY + timedelta(days=40)))
    db.session.commit()
e = extras()
assert e['payments_measured'] == 2, e['payments_measured']
assert e['days_to_paid'] == 30, e['days_to_paid']
print('9. two payments at 20 and 40 days average 30 days from invoice')


# ─── 10. Each fee earner's own column ───────────────────────────────────────
e = extras()
board = {r['name']: r for r in e['board']}
assert set(board) == {'B Cowan', 'S Rutter'}, board.keys()
assert board['B Cowan']['completed'] == 2, board['B Cowan']
near(board['B Cowan']['billed'], 10000, "B Cowan's billing")
near(board['B Cowan']['received'], 12000, "B Cowan's receipts")
assert board['S Rutter']['completed'] == 1
near(board['S Rutter']['billed'], 0, 'S Rutter has raised no invoice')
assert e['board'][0]['name'] == 'B Cowan', 'the table is not ordered by billing'
print('10. each fee earner has their own completed, billed and received')


# ─── 11. The fee value of stock on the market ───────────────────────────────
with app.app_context():
    forsale = Project(name='Castle Hill', instruction_type=INSTRUCTION_FOR_SALE,
                      fee_percent=2.0, property_id=PROP)
    tolet = Project(name='Mill Lane', instruction_type=INSTRUCTION_TO_LET,
                    fee_percent=10.0, property_id=PROP)
    fixed = Project(name='Quarry Road', instruction_type=INSTRUCTION_TO_LET,
                    fee_fixed=2500.0, property_id=PROP)
    nofee = Project(name='Bell Lane', instruction_type=INSTRUCTION_TO_LET,
                    property_id=PROP)
    for p in (forsale, tolet, fixed, nofee):
        db.session.add(p)
    db.session.commit()
    db.session.add(Listing(project_id=forsale.id, listing_status='available',
                           listing_price=800000, listing_price_unit='sale'))
    db.session.add(Listing(project_id=tolet.id, listing_status='available',
                           listing_price=40000, listing_price_unit='pa'))
    db.session.add(Listing(project_id=fixed.id, listing_status='available',
                           listing_price=2000, listing_price_unit='pcm'))
    db.session.add(Listing(project_id=nofee.id, listing_status='available',
                           listing_price=30000, listing_price_unit='pa'))
    db.session.commit()

with app.app_context():
    st = stock_fee_value()
# 800,000 × 2% = 16,000; 40,000 × 10% = 4,000; a fixed £2,500. Bell Lane has no
# fee, so it is counted but not valued.
near(st['fee_total'], 16000 + 4000 + 2500, 'the fee value of stock')
near(st['sale_fee'], 16000, 'the sale half of it')
near(st['let_fee'], 4000 + 2500, 'the letting half of it')
assert st['stock_count'] == 4 and st['valued'] == 3 and st['no_fee'] == 1, st
print('11. stock on the market is worth £22,500 in fees, with one unpriced')


# ─── 12. A rent per month is charged on the year ────────────────────────────
# The fixed-fee unit above is £2,000 pcm; its asking figure must annualise.
near(st['asking_total'], 800000 + 40000 + 24000 + 30000, 'the asking total')
print('12. £2,000 a month is valued as £24,000 a year')


# ─── 13. Price on application is worth nothing to charge a fee on ───────────
with app.app_context():
    poa = Project(name='Riverside', instruction_type=INSTRUCTION_TO_LET,
                  fee_percent=10.0, property_id=PROP)
    db.session.add(poa)
    db.session.commit()
    db.session.add(Listing(project_id=poa.id, listing_status='available',
                           listing_price=None, listing_price_unit='poa'))
    db.session.commit()
with app.app_context():
    after = stock_fee_value()
near(after['fee_total'], st['fee_total'], 'a price on application changed the fee value')
assert after['no_fee'] == 2, 'the unpriced unit is not being counted'
print('13. a unit on application adds nothing to the total but is counted')


# ─── 14. Only stock that is actually available counts ───────────────────────
with app.app_context():
    gone = Project(name='Paddock Wood', instruction_type=INSTRUCTION_FOR_SALE,
                   fee_percent=2.0, property_id=PROP)
    appraisal = Project(name='Sevenoaks', instruction_type='Market Appraisal',
                        fee_percent=2.0, property_id=PROP)
    db.session.add_all([gone, appraisal])
    db.session.commit()
    db.session.add(Listing(project_id=gone.id, listing_status='let',
                           listing_price=900000, listing_price_unit='sale'))
    db.session.add(Listing(project_id=appraisal.id, listing_status='available',
                           listing_price=900000, listing_price_unit='sale'))
    db.session.commit()
with app.app_context():
    final = stock_fee_value()
near(final['fee_total'], st['fee_total'],
     'stock that is let, or only being appraised, is being valued as available')
assert final['stock_count'] == 5, final['stock_count']
print('14. let units and market appraisals are not counted as stock')


# ─── 14b. A unit with no instruction behind it is still stock ───────────────
with app.app_context():
    db.session.add(Listing(project_id=None, listing_status='available',
                           listing_price=95000, listing_price_unit='sale'))
    db.session.commit()
    loose = stock_fee_value()
near(loose['fee_total'], final['fee_total'],
     'a unit with no instruction was given a fee out of nowhere')
assert loose['stock_count'] == final['stock_count'] + 1, \
    'a unit with no instruction was dropped from stock rather than counted'
assert loose['no_fee'] == final['no_fee'] + 1, \
    'a unit with no instruction is not being reported as unpriced'
print('14b. a unit with no instruction is counted as stock, and reported unpriced')


# ─── 15. A fee can be entered on a project and it reaches the figure ────────
r = cl.post(f'/projects/{PID}/edit', data={'fee_percent': '3'}, follow_redirects=True)
assert r.status_code == 200, r.status_code
with app.app_context():
    near(Project.query.get(PID).fee_percent, 3.0, 'the fee did not save on the project')
src = open("/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/"
           "cr-property-db/templates/projects/_record_boxes.html").read()
assert 'name="fee_percent"' in src and 'name="fee_fixed"' in src, \
    'there is nowhere on the project page to enter a fee'
print('15. a fee can be entered on the project and it saves')


# ─── 16. The page shows them all ────────────────────────────────────────────
html = cl.get('/transactions').get_data(as_text=True)
for label in ['Sales value', 'Lettings value', 'Fee value of stock',
              'Weighted pipeline', 'Deals won', 'Instruction to completion',
              'Invoice to payment', 'By fee earner']:
    assert label in html, f'"{label}" is missing from the page'
assert money_gbp(e['sales']['value']) in html, 'the sales value is not shown'
assert money_gbp(e['lettings']['value']) in html, 'the lettings value is not shown'
assert money_gbp(final['fee_total']) in html, 'the stock fee value is not shown'
assert 'B Cowan' in html and 'S Rutter' in html, 'the fee earner table is empty'
print('16. every new figure appears on the page')


# ─── 17. The weighting is stated, not hidden ────────────────────────────────
for status, weight in PIPELINE_WEIGHTS.items():
    if weight:
        assert status in html, f'the {status} weighting is not shown'
assert 'Terms Agreed' in html and '50%' in html, 'the discount rates are not stated'
print('17. the pipeline discounts are shown on the page, not buried in the code')


# ─── 18. Nothing on the page is a figure typed in by hand ───────────────────
tpl = open("/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/"
           "cr-property-db/templates/transactions/list.html").read()
money = [m for m in re.findall(r'£[\d,]+\.?\d*', tpl) if m != '£0']
assert not money, f'the template has figures written into it: {money}'
print('18. no figure is written into the template')

print('\nADDED METRICS: ALL CHECKS PASSED')
