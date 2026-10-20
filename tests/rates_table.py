# -*- coding: utf-8 -*-
"""Business rates calculator — the table of checks, run on demand.

Named rates_table.py rather than test_*.py on purpose: run_all.sh counts a
test as passed only if it prints PASSED, and this one prints a table. Run it
directly:  python3 tests/rates_table.py

Nothing is mocked. The multipliers are read out of app.py's MULTIPLIER_SEED
with ast, so what is tested is the table the CRM actually seeds, and the
multiplier for each case is chosen by business_rates.suggest_multiplier
rather than picked by the test.

Every expected figure below is worked out by hand from the published rules,
not from the code.
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import business_rates as br

YEAR = '2026/27'


def seed_rows():
    """MULTIPLIER_SEED out of app.py, without importing Flask."""
    tree = ast.parse(io.open(os.path.join(HERE, 'app.py'), encoding='utf-8').read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, 'id', None) == 'MULTIPLIER_SEED' for t in node.targets):
            rows = []
            for i, tup in enumerate(ast.literal_eval(node.value)):
                ty, name, kind, value, rv_min, rv_max = tup[:6]
                rows.append({'id': i + 1, 'tax_year': ty, 'name': name,
                             'multiplier_type': kind, 'value': value,
                             'category': tup[6] if len(tup) > 6 else None,
                             'rv_min': rv_min, 'rv_max': rv_max,
                             'starts_on': None, 'verified_on': None})
            return rows
    raise SystemExit('MULTIPLIER_SEED not found in app.py')


ROWS = [r for r in seed_rows() if r['tax_year'] == YEAR]
if not ROWS:
    raise SystemExit('FAIL: no multipliers seeded for the current tax year %s' % YEAR)


def run(rv_pounds, declared, manual_pct=None, property_type=None):
    rv = br.to_pence(rv_pounds)
    pick = br.suggest_multiplier(ROWS, rv, property_type)
    if not pick:
        return None, None
    res = br.calculate(rv, pick['value'], relief_percent=manual_pct,
                       sbrr_declared=declared)
    return pick, res


# case, RV, declared, manual %, expected multiplier, expected relief %,
# expected relief, expected total
CASES = [
    ('RV 11,000, eligible — 100% relief, nothing payable',
     11000, True, None, '0.432', '100', '£4,752.00', '£0.00'),
    ('RV 12,000, the boundary — 100% relief',
     12000, True, None, '0.432', '100', '£5,184.00', '£0.00'),
    ('RV 13,500, eligible — taper at (15000-13500)/30 = 50%',
     13500, True, None, '0.432', '50', '£2,916.00', '£2,916.00'),
    ('RV 14,999, eligible — top of the taper, 0.033%',
     14999, True, None, '0.432', '0.033', '£2.14', '£6,477.43'),
    ('RV 15,000, eligible — above the taper, no SBRR',
     15000, True, None, '0.432', None, '—', '£6,480.00'),
    ('RV 20,000, eligible — no SBRR, small business multiplier still used',
     20000, True, None, '0.432', None, '—', '£8,640.00'),
    ('RV 60,000 — standard multiplier, no SBRR',
     60000, True, None, '0.480', None, '—', '£28,800.00'),
    ('RV 500,000 — high-value multiplier 50.8p, no SBRR',
     500000, True, None, '0.508', None, '—', '£254,000.00'),
    ('RV 750,000 — high-value multiplier, well above the threshold',
     750000, True, None, '0.508', None, '—', '£381,000.00'),
    ('RV 11,000, NOT declared — no relief, reason stated',
     11000, False, None, '0.432', None, '—', '£4,752.00'),
    ('RV 12,000, NOT declared — no relief, reason stated',
     12000, False, None, '0.432', None, '—', '£5,184.00'),
    ('RV 13,500, NOT declared — no relief, reason stated',
     13500, False, None, '0.432', None, '—', '£5,832.00'),
    ('RV 14,999, NOT declared — no relief, reason stated',
     14999, False, None, '0.432', None, '—', '£6,479.57'),
    ('RV 13,500, eligible, manual 25% — the manual figure wins over 50%',
     13500, True, '25', '0.432', '25', '£1,458.00', '£4,374.00'),
    ('RV 11,000, eligible, manual 10% — manual beats automatic 100%',
     11000, True, '10', '0.432', '10', '£475.20', '£4,276.80'),
]

W = (62, 11, 9, 9, 13, 13, 7)
head = ('Case', 'Multiplier', 'Relief %', 'exp', 'Relief', 'Total', 'Result')
print()
print('  '.join(h.ljust(w) for h, w in zip(head, W)))
print('  '.join('-' * w for w in W))

failures = []
for (name, rv, declared, manual, exp_mult, exp_pct, exp_relief, exp_total) in CASES:
    pick, res = run(rv, declared, manual)
    if res is None:
        failures.append((name, 'no multiplier could be chosen'))
        print('  '.join(x.ljust(w) for x, w in zip(
            (name, '—', '—', str(exp_pct), '—', '—', 'FAIL'), W)))
        continue
    got_mult = br.multiplier_str(res['multiplier'])
    got_pct = (br.percent_str(res['relief_percent_used'])
               if res['relief_percent_used'] is not None else None)
    got_relief = br.money(res['relief']) if res['relief'] else '—'
    got_total = br.money(res['total'])
    want_source = 'manual' if manual else ('automatic' if exp_pct else None)

    problems = []
    if got_mult != exp_mult:
        problems.append('multiplier %s, expected %s' % (got_mult, exp_mult))
    if got_pct != exp_pct:
        problems.append('relief %s%%, expected %s%%' % (got_pct, exp_pct))
    if got_relief != exp_relief:
        problems.append('relief %s, expected %s' % (got_relief, exp_relief))
    if got_total != exp_total:
        problems.append('total %s, expected %s' % (got_total, exp_total))
    if res['relief_source'] != want_source:
        problems.append('source %r, expected %r' % (res['relief_source'], want_source))
    # Where no relief was applied, the calculator must SAY why.
    if not res['relief'] and not (res['relief_note'] or '').strip():
        problems.append('no reason given for applying no relief')
    if not declared and res['sbrr']['band'] == 'not-declared' \
            and 'only business property' not in res['relief_note']:
        problems.append('reason does not name the missing declaration')
    if manual and 'overrides the automatic' not in res['relief_note'] \
            and 'entered by hand' not in res['relief_note']:
        problems.append('breakdown does not say the manual figure was used')

    ok = not problems
    if not ok:
        failures.append((name, '; '.join(problems)))
    print('  '.join(x.ljust(w) for x, w in zip(
        (name, got_mult, str(got_pct), str(exp_pct), got_relief, got_total,
         'PASS' if ok else 'FAIL'), W)))

# ── Things the table cannot show ──
print()
extra = []


def check(label, got, want):
    extra.append((label, got == want, 'got %r, wanted %r' % (got, want)))


check('2026/27 is seeded at all', len(ROWS), 5)
check('five multipliers for 2026/27',
      sorted(r['value'] for r in ROWS), [38200, 43000, 43200, 48000, 50800])
check('threshold is RV £51,000 in pence',
      [r['rv_max'] for r in ROWS if r['value'] == 43200], [5_100_000])
check('high-value starts at RV £500,000',
      [r['rv_min'] for r in ROWS if r['value'] == 50800], [50_000_000])
# RV 30,000 is under the 51,000 threshold, so the right answer is the small
# business multiplier at 43.2p — and emphatically NOT the 38.2p small business
# RHL one, which needs a declaration of use the CRM cannot make.
check('RHL multipliers are never suggested on their own (RV 30,000 \u2192 43.2p)',
      br.suggest_multiplier(ROWS, br.to_pence(30000))['value'], 43200)
check('RV 100,000 takes the standard 48p, not the RHL 43p',
      br.suggest_multiplier(ROWS, br.to_pence(100000))['value'], 48000)
check('RHL multiplier is still available to pick by hand',
      sorted(r['value'] for r in ROWS if r['category']), [38200, 43000])
check('an RHL property at RV 600,000 pays the high-value rate',
      br.suggest_multiplier(ROWS, br.to_pence(600000),
                            'Retail, Hospitality or Leisure')['value'], 50800)
check('taper formula at RV 14,000 (gov.uk quotes 33%)',
      str(br.small_business_rate_relief(1_400_000, True)['percent']), '33.333')
check('no SBRR without a rateable value',
      br.small_business_rate_relief(None, True)['applies'], False)
check('seeding twice adds nothing new (names are unique per year)',
      len(set((r['tax_year'], r['name']) for r in seed_rows())),
      len(seed_rows()))
check('2025/26 standard multiplier still 55.5p',
      [r['value'] for r in seed_rows()
       if r['tax_year'] == '2025/26' and r['multiplier_type'] == 'Standard'],
      [55500])

for label, ok, detail in extra:
    print('%-62s  %s%s' % (label, 'PASS' if ok else 'FAIL',
                           '' if ok else '  (' + detail + ')'))
    if not ok:
        failures.append((label, detail))

print()
if failures:
    print('%d FAILED:' % len(failures))
    for name, why in failures:
        print('  - %s: %s' % (name, why))
    sys.exit(1)
print('All %d checks pass.' % (len(CASES) + len(extra)))
