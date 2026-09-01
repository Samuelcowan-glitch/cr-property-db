"""The Business Rates Calculator, and what it puts on a brochure."""
import io, os, re, sys, tempfile
from decimal import Decimal

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)

import app as A
import business_rates as br
import pymupdf
from PIL import Image
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db
date = A.date


def shot():
    b = io.BytesIO()
    Image.new('RGB', (900, 700), (150, 160, 175)).save(b, 'JPEG')
    return b.getvalue()


IDS = {}
with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    db.session.add(A.User(username='admin', password_hash=generate_password_hash('pw'),
                          role='admin', full_name='Benjamin Cowan',
                          email='bc@cowanandrutter.co.uk'))
    db.session.add(A.User(username='looker', password_hash=generate_password_hash('pw'),
                          role='viewer', full_name='A Viewer'))
    db.session.commit()

    HF = A.Council.query.filter(A.Council.name.like('%Hammersmith%')).first()
    KC = A.Council.query.filter(A.Council.name.like('%Kensington%')).first()
    IDS['hf'], IDS['kc'] = HF.id, KC.id

    def build(key, council_id, instruction, **listing_kw):
        p = A.Property(address=f'{key.title()} House, London', postcode='SW6 1AA',
                       property_type='Office', size=1200, council_id=council_id)
        db.session.add(p); db.session.commit()
        pr = A.Project(name=key, property_id=p.id, fee_earner_id=1,
                       instruction_type=instruction)
        db.session.add(pr); db.session.commit()
        l = A.Listing(project_id=pr.id, property_id=p.id, listing_status='available',
                      blurb='A unit.', location_description='Off the Kings Road.',
                      strapline=f'{key.upper()} UNIT', **listing_kw)
        db.session.add(l); db.session.commit()
        db.session.add(A.ListingPhoto(listing_id=l.id, file_data=shot(), filename='a.jpg',
                                      file_mime='image/jpeg', file_size=1, sort_order=0))
        db.session.commit()
        IDS[key] = {'prop': p.id, 'proj': pr.id, 'listing': l.id,
                    'photo': A.ListingPhoto.query.filter_by(listing_id=l.id)
                                                 .first().id}

    build('hflet', IDS['hf'], A.INSTRUCTION_TO_LET,
          set_as_to_let=True, listing_price=45000, listing_price_unit='pa')
    build('kcsale', IDS['kc'], A.INSTRUCTION_FOR_SALE,
          set_as_for_sale=True, sale_price=900000)
    build('both', IDS['hf'], A.INSTRUCTION_FOR_SALE,
          set_as_for_sale=True, set_as_to_let=True, sale_price=900000,
          listing_price=45000, listing_price_unit='pa')
    build('nocouncil', None, A.INSTRUCTION_TO_LET,
          set_as_to_let=True, listing_price=30000, listing_price_unit='pa')

    MULT = {(m.tax_year, m.multiplier_type): m.id
            for m in A.RatesMultiplier.query.all()}

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def calc(prop_id, **kw):
    return cl.post(f'/properties/{prop_id}/rates/calculate', data=kw).get_json()


def save(prop_id, **kw):
    return cl.post(f'/properties/{prop_id}/rates/save', data=kw, follow_redirects=True)


def prop(key):
    return A.Property.query.get(IDS[key]['prop'])


def pdf_text(key, pages=2):
    r = cl.post(f"/projects/{IDS[key]['proj']}/particulars/preview",
                data={'pages': str(pages), 'photo_ids': [IDS[key]['photo']]})
    assert r.status_code == 200, r.status_code
    doc = pymupdf.open(stream=r.get_data(), filetype='pdf')
    return re.sub(r'\s+', ' ', ' '.join(p.get_text() for p in doc))


# ─── 1. Money is exact, never a float ───────────────────────────────────────
assert br.to_pence('12,500.50') == 1250050
assert br.to_pence('£45,000') == 4500000
assert br.to_pence('0.1') == 10 and br.to_pence('0.2') == 20
assert br.to_pence('nonsense') is None, 'unreadable text became a number'
assert br.to_pence('') is None
assert br.from_pence(1250050) == Decimal('12500.50')
assert br.money(1250050) == '£12,500.50'
# The classic float failure, which this must not reproduce.
total = 0
for _ in range(10):
    total += br.to_pence('0.1')
assert total == 100 and br.money(total) == '£1.00', br.money(total)
assert 0.1 * 10 != 1.0000000000000002 or True   # (float, for contrast)
src = open(f'{ROOT}/business_rates.py').read()
assert 'float(' not in src.replace('floating-point', ''), \
    'the calculation module converts to float somewhere'
print('1. money is whole pence throughout; no float can round a total off')


# ─── 2. Multipliers are read both ways round ────────────────────────────────
assert br.to_multiplier('0.555') == 55500
assert br.to_multiplier('55.5p') == 55500, 'a multiplier quoted in pence was misread'
assert br.multiplier_str(49900) == '0.499'
assert br.to_multiplier('rubbish') is None
print('2. a multiplier is read whether written 0.555 or 55.5p')


# ─── 3. The basic calculation ───────────────────────────────────────────────
r = br.calculate(br.to_pence('120000'), 55500)
assert r['base'] == br.to_pence('66600'), br.money(r['base'])
assert r['total'] == r['base'] and r['monthly'] == br.to_pence('5550')
r = br.calculate(br.to_pence('45000'), 49900)
assert br.money(r['base']) == '£22,455.00', br.money(r['base'])
print('3. rateable value x multiplier, to the penny')


# ─── 4. Rounding is half-up, and only at the end ────────────────────────────
# 12,345 x 0.555 = 6,851.475 -> 6,851.48, not 6,851.47.
r = br.calculate(br.to_pence('12345'), 55500)
assert br.money(r['base']) == '£6,851.48', br.money(r['base'])
r = br.calculate(br.to_pence('1'), 55500)
assert br.money(r['base']) == '£0.56', br.money(r['base'])
print('4. rounding is half-up and happens once, at the end')


# ─── 5. Every multiplier type, on both councils ─────────────────────────────
with A.app.app_context():
    for year in ('2023/24', '2024/25', '2025/26'):
        for kind in ('Standard', 'Small business'):
            mid = MULT[(year, kind)]
            row = A.RatesMultiplier.query.get(mid)
            out = br.calculate(br.to_pence('60000'), row.value)
            assert out['base'] > 0, f'{year} {kind} produced nothing'
print('5. every multiplier on record, across three tax years, calculates')


# ─── 6. Thresholds: the small business multiplier stops at £51,000 ──────────
with A.app.app_context():
    for rv, expect in (('50999', 'Small business'), ('51000', 'Standard'),
                       ('51001', 'Standard'), ('1', 'Small business')):
        pick = A.suggest_multiplier_for('2025/26', br.to_pence(rv))
        assert pick['multiplier_type'] == expect, f'RV {rv} suggested {pick["name"]}'
print('6. the threshold at £51,000 is exact — 50,999 small, 51,000 standard')


# ─── 7. Percentage and fixed reliefs ────────────────────────────────────────
r = br.calculate(br.to_pence('20000'), 49900, relief_percent=Decimal('100'))
assert r['total'] == 0, br.money(r['total'])
r = br.calculate(br.to_pence('20000'), 49900, relief_percent=Decimal('50'))
assert br.money(r['total']) == '£4,990.00', br.money(r['total'])
r = br.calculate(br.to_pence('20000'), 49900, relief_amount_pence=br.to_pence('1000'))
assert br.money(r['total']) == '£8,980.00', br.money(r['total'])
print('7. a relief works as a percentage and as a fixed amount')


# ─── 8. Supplements, transitional and other adjustments ─────────────────────
r = br.calculate(br.to_pence('100000'), 55500,
                 supplement_pence=br.to_pence('2000'),        # London BRS at 2p
                 transitional_pence=br.to_pence('-1500'),
                 other_pence=br.to_pence('250'))
assert br.money(r['base']) == '£55,500.00'
assert br.money(r['total']) == '£56,250.00', br.money(r['total'])
assert r['adjustments'] == br.to_pence('750')
print('8. supplements add, a negative transitional subtracts, and both show')


# ─── 9. A liability is never negative ───────────────────────────────────────
r = br.calculate(br.to_pence('10000'), 49900, relief_amount_pence=br.to_pence('99999'))
assert r['total'] == 0, br.money(r['total'])
assert r['floored'], 'the estimate went negative without saying so'
print('9. deductions larger than the bill give zero, and say they were held there')


# ─── 10. Nothing is applied that was not asked for ──────────────────────────
r = br.calculate(br.to_pence('20000'), 49900)
assert r['relief'] == 0, 'a relief was applied on its own'
assert r['adjustments'] == 0, 'an adjustment was applied on its own'
assert r['total'] == r['base']
src = open(f'{ROOT}/app.py').read()
for phrase in ('Small Business Rate Relief', 'Retail, Hospitality'):
    block = src[src.index('def _rates_inputs'):src.index('def _rates_result')]
    assert phrase not in block, f'{phrase} is being decided by the server'
print('10. no relief, supplement or transitional relief is ever applied silently')


# ─── 11. Calculating saves nothing ──────────────────────────────────────────
before = None
with A.app.app_context():
    before = A.RatesCalculation.query.count()
out = calc(IDS['hflet']['prop'], tax_year='2025/26', rateable_value='120000',
           multiplier_id=MULT[('2025/26', 'Standard')])
assert out['ok'] and out['result']['total'] == '£66,600.00', out
with A.app.app_context():
    assert A.RatesCalculation.query.count() == before, 'calculating saved a record'
print('11. calculating shows the figure and saves nothing')


# ─── 12. The assumptions are shown before anything is saved ─────────────────
out = calc(IDS['hflet']['prop'], tax_year='2025/26', rateable_value='120000',
           multiplier_id=MULT[('2025/26', 'Standard')],
           relief_type='Small Business Rate Relief', relief_percent='50',
           supplement='2400', supplement_label='London BRS', transitional='-500')
joined = ' '.join(out['assumptions'])
assert 'eligibility' in joined, 'nothing said the relief was not checked'
assert 'supplement' in joined.lower(), 'the supplement was applied without comment'
assert 'ransitional' in joined, 'the transitional adjustment was applied silently'
assert 'estimate' in joined.lower(), 'nowhere says this is an estimate'
assert 'not verified' in joined or 'has not been verified' in joined, \
    'an unverified multiplier was used without saying so'
print('12. every assumption is listed before the figure can be saved')


# ─── 13. Saving keeps the inputs, and the server does the sum again ─────────
save(IDS['hflet']['prop'], tax_year='2025/26', rateable_value='120000',
     multiplier_id=MULT[('2025/26', 'Standard')],
     relief_type='Charitable Rate Relief', relief_percent='80',
     notes='INTERNAL: landlord will not budge')
with A.app.app_context():
    c = prop('hflet').current_rates
    assert c.rateable_value == br.to_pence('120000')
    assert c.multiplier_value == 55500 and c.multiplier_name == 'Standard multiplier'
    assert c.relief_type == 'Charitable Rate Relief'
    assert Decimal(c.relief_percent) == Decimal('80')
    assert br.money(c.base_payable) == '£66,600.00'
    assert br.money(c.estimated_payable) == '£13,320.00', br.money(c.estimated_payable)
    assert c.tax_year == '2025/26' and c.calculated_on and c.calculated_by == 'admin'
print('13. saving records every input, the tax year, and who did it')


# ─── 14. A total posted from the browser is ignored ─────────────────────────
save(IDS['hflet']['prop'], tax_year='2025/26', rateable_value='10000',
     multiplier_id=MULT[('2025/26', 'Small business')],
     estimated_payable='1', total='1', base_payable='1')
with A.app.app_context():
    c = prop('hflet').current_rates
    assert br.money(c.estimated_payable) == '£4,990.00', br.money(c.estimated_payable)
    assert br.money(c.base_payable) == '£4,990.00'
print('14. a figure posted by the browser is discarded; the server works it out')


# ─── 15. History is kept, and only one estimate is current ──────────────────
with A.app.app_context():
    rows = prop('hflet').rates_calculations
    assert len(rows) >= 2, 'the earlier calculation was overwritten'
    assert sum(1 for r in rows if r.is_current) == 1, 'more than one is current'
    old = [r for r in rows if not r.is_current][0]
    assert old.rateable_value == br.to_pence('120000'), 'history was altered'
print('15. an earlier calculation becomes history rather than being overwritten')


# ─── 16. Changing the tax year preserves the earlier year ───────────────────
save(IDS['hflet']['prop'], tax_year='2024/25', rateable_value='10000',
     multiplier_id=MULT[('2024/25', 'Small business')])
with A.app.app_context():
    years = {r.tax_year for r in prop('hflet').rates_calculations}
    assert '2025/26' in years and '2024/25' in years, years
    assert prop('hflet').current_rates.tax_year == '2024/25'
print('16. calculating for a new tax year keeps the old year on the record')


# ─── 17. A multiplier from another tax year is refused ──────────────────────
out = calc(IDS['hflet']['prop'], tax_year='2025/26', rateable_value='50000',
           multiplier_id=MULT[('2023/24', 'Standard')])
assert not out['ok'] and 'multiplier_id' in out['errors'], out
print('17. a multiplier belonging to another tax year is refused, not used')


# ─── 18. Overriding the multiplier is allowed, flagged and audited ──────────
save(IDS['kcsale']['prop'], tax_year='2025/26', rateable_value='80000',
     multiplier_override='1', multiplier_value='0.612',
     override_reason='Rated as part of a larger assessment')
with A.app.app_context():
    c = prop('kcsale').current_rates
    assert c.multiplier_overridden and c.multiplier_value == 61200, c.multiplier_value
    assert c.override_reason.startswith('Rated as part')
    assert br.money(c.estimated_payable) == '£48,960.00', br.money(c.estimated_payable)
    entry = (A.AuditLog.query.filter_by(action='rates-calculated')
             .order_by(A.AuditLog.id.desc()).first())
    assert 'overridden' in (entry.detail or ''), entry.detail
out = calc(IDS['kcsale']['prop'], tax_year='2025/26', rateable_value='80000',
           multiplier_override='1', multiplier_value='0.612')
assert out['result']['overridden'] and out['result']['multiplier_name'] == 'Entered by hand'
assert any('by hand' in a for a in out['assumptions'])
print('18. a hand-entered multiplier is used, flagged on screen and audited')


# ─── 19. Rubbish in is refused, not guessed at ──────────────────────────────
for bad in ({'rateable_value': 'lots'}, {'rateable_value': '-5000'},
            {'rateable_value': ''}):
    fields = {'tax_year': '2025/26', 'multiplier_id': MULT[('2025/26', 'Standard')]}
    fields.update(bad)
    out = calc(IDS['hflet']['prop'], **fields)
    assert not out['ok'] and 'rateable_value' in out['errors'], (bad, out)
out = calc(IDS['hflet']['prop'], tax_year='2025/26', rateable_value='50000',
           multiplier_id=MULT[('2025/26', 'Standard')], relief_percent='150')
assert 'relief_percent' in out['errors'], out
out = calc(IDS['hflet']['prop'], tax_year='2025/26', rateable_value='50000',
           multiplier_override='1', multiplier_value='55.5')
assert 'multiplier_value' in out['errors'], 'a multiplier of 55.5 was accepted'
print('19. an unreadable or impossible entry is refused rather than guessed at')


# ─── 20. A missing rateable value stops a save ──────────────────────────────
with A.app.app_context():
    before = A.RatesCalculation.query.filter_by(property_id=IDS['both']['prop']).count()
save(IDS['both']['prop'], tax_year='2025/26', rateable_value='',
     multiplier_id=MULT[('2025/26', 'Standard')])
with A.app.app_context():
    after = A.RatesCalculation.query.filter_by(property_id=IDS['both']['prop']).count()
assert after == before, 'an estimate was saved with no rateable value'
print('20. nothing is saved without a rateable value')


# ─── 21. There is no council-confirmation anywhere ──────────────────────────
assert not hasattr(A, 'property_rates_confirm'), 'the confirm route still exists'
r = cl.post(f"/properties/{IDS['hflet']['prop']}/rates/confirm",
            data={'rates_confirmed': '1', 'rates_confirmed_amount': '4100'})
assert r.status_code == 404, f'the confirm route still answers ({r.status_code})'
page = cl.get(f"/properties/{IDS['hflet']['prop']}").get_data(as_text=True)
for gone in ('Council-confirmed', 'Confirmed by council', 'rates_confirmed',
             'Date confirmed', 'Confirmation note', 'council figure'):
    assert gone not in page, f'{gone!r} is still on the property page'
assert 'Business Rates Calculator' in page, 'the calculator went too'
print('21. the council-confirmation box, fields and route are all gone')


# ─── 22. An old confirmed value cannot override the estimate ────────────────
with A.app.app_context():
    p = prop('hflet')
    # A record from before this was removed still holds these columns.
    p.rates_confirmed = True
    p.rates_confirmed_amount = br.to_pence('999999')
    p.rates_confirmed_on = date(2026, 5, 1)
    db.session.commit()
    assert p.rates_for_brochure == p.current_rates.estimated_payable, \
        'an old council figure overrode the estimate'
    said = A.rates_paragraph(p, 'TO LET')
assert '999,999' not in said, 'an old council figure reached the brochure'
assert said.startswith('The estimated rates payable'), said
text = pdf_text('hflet', 2)
assert '999,999' not in text, 'an old council figure reached the printed document'
with A.app.app_context():
    # The column is left alone rather than destroyed.
    assert prop('hflet').rates_confirmed_amount == br.to_pence('999999'), \
        'historical data was deleted rather than ignored'
print('22. an old confirmed value is ignored, not used, and not deleted')


# ─── 23. Hammersmith & Fulham wording, exactly ──────────────────────────────
with A.app.app_context():
    said = A.rates_paragraph(prop('hflet'), 'TO LET')
    figure = A.brochure_money(prop('hflet').rates_for_brochure)
assert said == (f'The estimated rates payable for the current year are {figure}, '
                'subject to the occupier’s circumstances and any applicable reliefs '
                'or adjustments. Prospective tenants are advised to confirm this '
                'information with the London Borough of Hammersmith & Fulham by '
                'telephoning 020 8753 6681.'), said
assert 'We have been advised' not in said
print('23. the Hammersmith & Fulham wording is exactly as specified')


# ─── 24. Kensington and Chelsea wording, exactly ────────────────────────────
with A.app.app_context():
    said = A.rates_paragraph(prop('kcsale'), 'TO LET')
assert said == ('The estimated rates payable for the current year are £48,960, '
                'subject to the occupier’s circumstances and any applicable reliefs '
                'or adjustments. Prospective tenants are advised to confirm this '
                'information with the Royal Borough of Kensington and Chelsea by '
                'telephoning 020 7361 2828.'), said
print('24. the Kensington and Chelsea wording is exactly as specified')


# ─── 25. Sale particulars do not address only tenants ───────────────────────
assert A.rates_audience('FOR SALE') == 'Prospective purchasers and occupiers'
assert A.rates_audience('FOR SALE | TO LET') == 'Prospective purchasers and tenants'
assert A.rates_audience('TO LET') == 'Prospective tenants'
with A.app.app_context():
    sale = A.rates_paragraph(prop('kcsale'), 'FOR SALE')
assert 'Prospective purchasers and occupiers' in sale
assert 'Prospective tenants' not in sale, 'a sale brochure addressed tenants'
print('25. sale wording addresses purchasers, never tenants alone')


# ─── 26. A known council with no estimate sends the reader to them ──────────
with A.app.app_context():
    p = A.Property(address='Bare House', postcode='W6 9JU',
                   property_type='Office', council_id=IDS['hf'])
    db.session.add(p); db.session.commit()
    said = A.rates_paragraph(p, 'TO LET')
assert said.startswith('Prospective tenants are advised to contact the London '
                       'Borough of Hammersmith & Fulham to confirm the business '
                       'rates payable.'), said
assert '020 8753 6681' in said, 'the council telephone number was left out'
assert '£0' not in said and '£' not in said.split('020')[0], \
    'a nought was quoted where there is no estimate'
print('26. a council with no estimate gets a referral, with its number, and no £0')


# ─── 27. No council means no council named, and no other borough's number ───
with A.app.app_context():
    said = A.rates_paragraph(prop('nocouncil'), 'TO LET')
assert said == ('Prospective tenants are advised to make their own enquiries with '
                'the relevant local authority to confirm the business rates '
                'payable.'), said
assert 'Hammersmith' not in said and 'Kensington' not in said
assert '020 8753' not in said and '020 7361' not in said, \
    'a telephone number was printed for a council that was not chosen'
print('27. with no local authority, no council is named and no number is printed')


# ─── 28. The postcode is never used to guess a council ──────────────────────
block = src[src.index('def rates_paragraph'):src.index('def rates_summary')]
assert 'postcode' not in block or 'not guessed' in block or 'Guessing' in block
with A.app.app_context():
    assert prop('nocouncil').council is None, 'a council was assigned from the postcode'
print('28. a council is never guessed from the postcode')


# ─── 29. The brochure carries the paragraph, on both formats ────────────────
for pages in (2, 4):
    text = pdf_text('hflet', pages)
    assert 'Business Rates' in text, f'no rates box on the {pages}-page brochure'
    assert 'The estimated rates payable for the current year' in text, \
        f'the rates wording is missing from the {pages}-page brochure'
    assert '020 8753 6681' in text, 'the council number is not on the brochure'
    assert 'We have been advised' not in text, \
        f'the {pages}-page brochure claims the council supplied the figure'
    assert 'confirm' in text.lower()
print('29. both formats carry the rates box, the figure and the number')


# ─── 30. The right council per property, never the other one ────────────────
text = pdf_text('kcsale', 2)
assert 'Kensington and Chelsea' in text and '020 7361 2828' in text
assert 'Hammersmith' not in text and '020 8753' not in text, \
    "another borough's details reached this brochure"
assert 'estimated rates payable' in text.lower()
print('30. each brochure names its own council and no other')


# ─── 31. Sale wording reaches the PDF ───────────────────────────────────────
text = pdf_text('kcsale', 4)
assert 'Prospective purchasers and occupiers' in text, text[:400]
assert 'Prospective tenants are' not in text
print('31. the sale audience wording reaches the printed document')


# ─── 32. Sale or letting addresses purchasers and tenants ───────────────────
save(IDS['both']['prop'], tax_year='2025/26', rateable_value='70000',
     multiplier_id=MULT[('2025/26', 'Standard')])
text = pdf_text('both', 2)
assert 'Prospective purchasers and tenants' in text, text[:400]
print('32. a sale-or-letting brochure addresses purchasers and tenants')


# ─── 33. Internal notes never reach a brochure ──────────────────────────────
with A.app.app_context():
    c = prop('hflet').rates_calculations
    assert any('INTERNAL' in (r.notes or '') for r in c), 'the test note was not stored'
for pages in (2, 4):
    text = pdf_text('hflet', pages)
    assert 'INTERNAL' not in text and 'will not budge' not in text, \
        'a calculation note reached the brochure'
    assert 'Charitable Rate Relief' not in text, 'the relief type reached the brochure'
print('33. calculation notes and relief workings stay off the brochure')


# ─── 34. The preview shows the figures and the exact paragraph ──────────────
page = cl.get(f"/projects/{IDS['hflet']['proj']}/particulars").get_data(as_text=True)
for expected in ('Business rates', 'Local authority', 'Hammersmith',
                 'Tax year', 'Rateable value', 'Multiplier',
                 'Estimated business rates payable',
                 'The estimated rates payable for the current year'):
    assert expected in page, f'{expected!r} is not on the preview'
for gone in ('Council-confirmed', 'Confirmed by council', 'We have been advised'):
    assert gone not in page, f'{gone!r} is still on the preview'
assert 'Property Overview' in page and f"/properties/{IDS['hflet']['prop']}\"" in page, \
    'no way back to the property record from the preview'
print('34. the preview shows every figure and the paragraph that will be printed')


# ─── 35. A missing local authority is warned about before generating ────────
with A.app.app_context():
    data = A.particulars_data(A.Project.query.get(IDS['nocouncil']['proj']))
    gaps = A.particulars_gaps(data, 1)
assert any('Local authority' in g for g in gaps), gaps
page = cl.get(f"/projects/{IDS['nocouncil']['proj']}/particulars").get_data(as_text=True)
assert 'Local authority' in page and 'Some details are missing' in page
with A.app.app_context():
    ok = A.particulars_gaps(A.particulars_data(
        A.Project.query.get(IDS['hflet']['proj'])), 1)
assert not any('Local authority' in g for g in ok), ok
print('35. a property with no local authority is flagged before anything is made')


# ─── 36. Downloading still works and carries the rates note ─────────────────
r = cl.post(f"/projects/{IDS['hflet']['proj']}/particulars/download",
            data={'pages': '4', 'photo_ids': [IDS['hflet']['photo']],
                  'no_floorplan_ok': '1'})
assert r.status_code == 200 and r.get_data()[:5] == b'%PDF-'
text = re.sub(r'\s+', ' ', ' '.join(
    p.get_text() for p in pymupdf.open(stream=r.get_data(), filetype='pdf')))
assert '020 8753 6681' in text
print('36. the downloaded document carries the rates note too')


# ─── 37. The council is stored on the property, and inherited ───────────────
with A.app.app_context():
    p = prop('hflet')
    assert p.council_id == IDS['hf']
    project = A.Project.query.get(IDS['hflet']['proj'])
    assert project.property.council.name == p.council.name, \
        'a project did not inherit its property’s council'
    assert not hasattr(A.Project, 'council_id'), \
        'the council is being stored against projects as well'
print('37. the council is held once, on the property, and inherited by projects')


# ─── 38. A council can be added without touching the property table ─────────
with A.app.app_context():
    before = {c['name'] for c in
              __import__('sqlalchemy').inspect(db.engine).get_columns('properties')}
r = cl.post('/admin/rates/council',
            data={'name': 'London Borough of Hounslow', 'short_name': 'Hounslow',
                  'phone': '020 8583 5708', 'active': '1'}, follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    new = A.Council.query.filter_by(name='London Borough of Hounslow').first()
    assert new, 'the council was not added'
    after = {c['name'] for c in
             __import__('sqlalchemy').inspect(db.engine).get_columns('properties')}
    assert before == after, 'adding a council changed the property table'
    assert new in A.councils(), 'the new council is not offered on properties'
page = cl.get(f"/properties/{IDS['hflet']['prop']}").get_data(as_text=True)
assert 'Hounslow' in page, 'a new council did not appear on the property form'
print('38. a council is added centrally, with no change to the property table')


# ─── 39. A multiplier is added for a future year without a code change ──────
r = cl.post('/admin/rates/multiplier',
            data={'tax_year': '2026/27', 'name': 'Standard multiplier',
                  'multiplier_type': 'Standard', 'value': '0.561',
                  'rv_min': '51000', 'source': 'https://www.gov.uk/', 'active': '1',
                  'verified': '1', 'verified_on': '2026-04-02'},
            follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    row = A.RatesMultiplier.query.filter_by(tax_year='2026/27').first()
    assert row and row.value == 56100 and row.verified_on, 'the multiplier was not added'
    pick = A.suggest_multiplier_for('2026/27', br.to_pence('90000'))
    assert pick and pick['value'] == 56100, pick
print('39. next year’s multiplier is a row, not a release, and is used at once')


# ─── 40. An unverified multiplier says so ───────────────────────────────────
with A.app.app_context():
    row = A.RatesMultiplier.query.filter_by(tax_year='2025/26',
                                            name='Standard multiplier').first()
    assert row.verified_on is None, 'a seeded multiplier claims to be verified'
page = cl.get('/admin/rates').get_data(as_text=True)
assert 'not verified' in page, 'the reference page does not flag unverified figures'
out = calc(IDS['hflet']['prop'], tax_year='2025/26', rateable_value='50000',
           multiplier_id=MULT[('2025/26', 'Standard')])
assert any('not been verified' in a for a in out['assumptions'])
print('40. a multiplier nobody has checked is described as unverified everywhere')


# ─── 41. Seeding twice changes nothing ──────────────────────────────────────
with A.app.app_context():
    counts = (A.Council.query.count(), A.RatesMultiplier.query.count())
    hf = A.Council.query.get(IDS['hf'])
    hf.phone = '020 0000 0000'
    db.session.commit()
    A.seed_rates_reference()
    assert (A.Council.query.count(), A.RatesMultiplier.query.count()) == counts, \
        'seeding again added duplicates'
    assert A.Council.query.get(IDS['hf']).phone == '020 0000 0000', \
        'seeding overwrote an office correction'
    hf = A.Council.query.get(IDS['hf'])
    hf.phone = '020 8753 6681'
    db.session.commit()
print('41. seeding again adds nothing and overwrites no correction')


# ─── 42. Correcting a council reaches every brochure at once ────────────────
cl.post('/admin/rates/council',
        data={'id': IDS['hf'], 'name': 'London Borough of Hammersmith & Fulham',
              'short_name': 'Hammersmith & Fulham', 'phone': '020 8753 9999',
              'active': '1'}, follow_redirects=True)
text = pdf_text('hflet', 2)
assert '020 8753 9999' in text and '020 8753 6681' not in text, \
    'a corrected telephone number did not reach the brochure'
with A.app.app_context():
    entry = (A.AuditLog.query.filter_by(action='council-edited')
             .order_by(A.AuditLog.id.desc()).first())
    assert entry and 'telephone changed' in (entry.detail or ''), entry
cl.post('/admin/rates/council',
        data={'id': IDS['hf'], 'name': 'London Borough of Hammersmith & Fulham',
              'short_name': 'Hammersmith & Fulham', 'phone': '020 8753 6681',
              'active': '1'}, follow_redirects=True)
print('42. correcting a number once corrects every brochure, and is audited')


# ─── 43. The local authority is required on the property form ───────────────
r = cl.post(f"/properties/{IDS['hflet']['prop']}/edit",
            data={'address': 'Changed House', 'postcode': 'SW6 1AA', 'council_id': ''},
            follow_redirects=True)
assert 'Choose the local authority' in r.get_data(as_text=True)
with A.app.app_context():
    assert prop('hflet').council_id == IDS['hf'], 'the council was cleared'
    assert prop('hflet').address != 'Changed House', 'the rest of the edit went through'
page = cl.get(f"/properties/{IDS['hflet']['prop']}").get_data(as_text=True)
assert 'name="council_id"' in page and 'required' in page
assert 'data-search' in page, 'the local authority list is not searchable'
print('43. the local authority is required, and the list is searchable')


# ─── 44. A partial form does not blank the council ──────────────────────────
cl.post(f"/properties/{IDS['hflet']['prop']}/edit",
        data={'description': 'Edited from a partial form'}, follow_redirects=True)
with A.app.app_context():
    p = prop('hflet')
    assert p.council_id == IDS['hf'], 'a partial save cleared the council'
    assert p.description == 'Edited from a partial form'
    assert p.current_rates is not None, 'a property save destroyed the calculation'
print('44. a form that does not carry the council leaves it, and the estimate, alone')


# ─── 45. A council id that is not a council is refused ──────────────────────
cl.post(f"/properties/{IDS['hflet']['prop']}/edit",
        data={'address': 'Hflet House, London', 'postcode': 'SW6 1AA',
              'council_id': '999999'}, follow_redirects=True)
with A.app.app_context():
    assert prop('hflet').council_id == IDS['hf'], 'an invented council id was stored'
print('45. a council id that is not on record is refused, not stored')


# ─── 46. Permissions ────────────────────────────────────────────────────────
viewer = A.app.test_client()
viewer.post('/login', data={'username': 'looker', 'password': 'pw'},
            follow_redirects=True)
for path, method in ((f"/properties/{IDS['hflet']['prop']}/rates/calculate", 'post'),
                     (f"/properties/{IDS['hflet']['prop']}/rates/save", 'post'),
                     ('/admin/rates', 'get'),
                     ('/admin/rates/council', 'post'),
                     ('/admin/rates/multiplier', 'post'),
                     (f"/properties/{IDS['hflet']['prop']}/edit", 'post')):
    r = getattr(viewer, method)(path, data={})
    assert r.status_code == 403, f'a viewer reached {path} ({r.status_code})'
r = viewer.get(f"/properties/{IDS['hflet']['prop']}")
assert r.status_code == 200, 'a viewer cannot see a property at all'
with A.app.app_context():
    assert A.AuditLog.query.filter_by(action='denied').count() > 0
print('46. a viewer cannot calculate, save, confirm or edit reference data')


# ─── 47. Everything that changed a figure is in the audit log ───────────────
with A.app.app_context():
    actions = {a.action for a in A.AuditLog.query.all()}
    for expected in ('rates-calculated', 'council-added',
                     'council-edited', 'multiplier-added', 'denied'):
        assert expected in actions, f'{expected} was never audited'
    entry = A.AuditLog.query.filter_by(action='rates-calculated').first()
    assert entry.username == 'admin' and '£' in (entry.detail or '')
    # The old confirmation history is not erased by removing the feature.
    assert A.AuditLog.query.count() > 5
print('47. calculating and changing reference data are audited; history is kept')


# ─── 48. The rates paragraph fits inside its box on the page ────────────────
with A.app.app_context():
    # A council with a long name and a long note, to crowd the panel.
    long_name = 'The Royal Borough of Kensington and Chelsea Business Rates Service'
    c = A.Council(name=long_name, phone='020 7361 2828')
    db.session.add(c); db.session.commit()
    p = prop('both'); was = p.council_id
    p.council_id = c.id
    l = A.Listing.query.get(IDS['both']['listing'])
    l.key_terms = ('A new full repairing and insuring lease for a term to be '
                   'agreed, subject to periodic upward-only rent reviews and '
                   'outside the security of tenure provisions of the Landlord '
                   'and Tenant Act 1954.')
    db.session.commit()
for pages in (2, 4):
    r = cl.post(f"/projects/{IDS['both']['proj']}/particulars/preview",
                data={'pages': str(pages), 'photo_ids': [IDS['both']['photo']]})
    doc = pymupdf.open(stream=r.get_data(), filetype='pdf')
    for page_no, pg in enumerate(doc):
        for block in pg.get_text('blocks'):
            x0, y0, x1, y1 = block[:4]
            assert x0 >= -1 and x1 <= pg.rect.width + 1, \
                f'text runs off the side of page {page_no + 1} ({pages}-page)'
            assert y0 >= -1 and y1 <= pg.rect.height + 1, \
                f'text runs off the top or bottom of page {page_no + 1}'
    whole = re.sub(r'\s+', ' ', ' '.join(pg.get_text() for pg in doc))
    # The paragraph is present in full, not cut off partway.
    assert 'to confirm this information with' in whole, \
        f'the rates note was truncated on the {pages}-page brochure'
    assert whole.count('Misrepresentation Act 1967') == 1, 'the disclaimer was lost'
    for heading in ('Business Rates', 'Rent', 'Price'):
        assert heading in whole, f'{heading} was pushed out by the rates note'
with A.app.app_context():
    prop('both').council_id = was
    db.session.commit()
print('48. a long rates note wraps inside its box without pushing anything off')


# ─── 49. The calculation is repeated on the server, not trusted ─────────────
js = open(f'{ROOT}/static/js/business-rates.js').read()
for banned in ('* multiplier', 'parseFloat', 'toFixed'):
    assert banned not in js, f'the browser is doing arithmetic ({banned})'
assert 'fetch(' in js and 'calcUrl' in js
handler = src[src.index('def property_rates_save'):src.index('def property_rates_suggest')]
assert '_rates_result(inputs)' in handler, 'the save route does not recalculate'
for field in ('estimated_payable', 'total'):
    assert f"form.get('{field}')" not in handler, \
        f'the save route reads {field} from the request'
print('49. the browser does no arithmetic, and the server recalculates before saving')


# ─── 50. Nothing else about the property or project was disturbed ───────────
with A.app.app_context():
    p = prop('kcsale')
    assert p.address and p.postcode == 'SW6 1AA' and p.property_type == 'Office'
    project = A.Project.query.get(IDS['kcsale']['proj'])
    assert project.instruction_type == A.INSTRUCTION_FOR_SALE
    listing = A.Listing.query.get(IDS['kcsale']['listing'])
    assert listing.sale_price == 900000 and listing.strapline
print('50. property, project and listing records are otherwise untouched')

# ─── 51. The calculator opens on a year it can actually work in ─────────────
# It used to open on the newest year offered, which was a future year with no
# multipliers on record — so every option was hidden and no figure could ever
# be produced. The calculator looked broken when the table was merely empty.
with A.app.app_context():
    opens_on = A.default_tax_year()
    assert A.multiplier_rows(opens_on), \
        f'the calculator opens on {opens_on}, which has no multipliers'
page = cl.get(f"/properties/{IDS['hflet']['prop']}").get_data(as_text=True)
years = re.search(r'name="tax_year".*?</select>', page, re.S).group(0)
chosen = re.search(r'<option value="([^"]+)"\s*\n?\s*selected', years)
assert chosen, 'no tax year is selected at all'
with A.app.app_context():
    assert A.multiplier_rows(chosen.group(1)), \
        f'the page opens on {chosen.group(1)}, which has no multipliers'
print(f'51. the calculator opens on {opens_on}, a year with multipliers on record')


# ─── 52. A year with no multipliers explains itself ─────────────────────────
with A.app.app_context():
    empty = next((y for y in br.tax_year_options(date.today())
                  if not A.multiplier_rows(y)), None)
assert empty, 'no empty year to test with'
r = cl.get(f"/properties/{IDS['hflet']['prop']}/rates/suggest?tax_year={empty}"
           f"&rateable_value=50000").get_json()
assert r['empty'] and r['message'], r
assert 'No multiplier is on record' in r['message'] and empty in r['message']
out = calc(IDS['hflet']['prop'], tax_year=empty, rateable_value='50000')
assert not out['ok'] and 'No multiplier is on record' in out['errors']['multiplier_id'], out
assert f'{empty} — no multiplier yet' in page or 'no multiplier yet' in page, \
    'the year list does not mark years with nothing on record'
js = open(f'{ROOT}/static/js/business-rates.js').read()
assert 'data-br-empty' in js, 'the screen never shows the empty-year message'
print('52. a tax year with nothing on record says so instead of going silent')


# ─── 53. The whole calculator works with CSRF live ──────────────────────────
# Every other check here runs with TESTING on, which skips the CSRF check
# entirely — and this fetch, with its token in a header, is the one request in
# the CRM that path was never exercised on.
A.app.config['TESTING'] = False
try:
    fresh = A.app.test_client()
    login = fresh.get('/login').get_data(as_text=True)
    tok = re.search(r'name="_csrf" value="([^"]+)"', login)
    assert tok, 'the login form carries no token'
    fresh.post('/login', data={'username': 'admin', 'password': 'pw',
                               '_csrf': tok.group(1)}, follow_redirects=True)
    body = fresh.get(f"/properties/{IDS['hflet']['prop']}").get_data(as_text=True)
    meta = re.search(r'<meta name="csrf-token" content="([^"]+)">', body)
    assert meta, 'the page carries no token for a fetch to send'

    # Exactly what the browser sends.
    r = fresh.post(f"/properties/{IDS['hflet']['prop']}/rates/calculate",
                   data={'tax_year': '2025/26', 'rateable_value': '120000',
                         'multiplier_id': MULT[('2025/26', 'Standard')]},
                   headers={'X-CSRF-Token': meta.group(1),
                            'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code == 200, f'the calculator is refused in production ({r.status_code})'
    assert r.get_json()['result']['total'] == '£66,600.00', r.get_json()
    # And without a token it is still refused.
    r = fresh.post(f"/properties/{IDS['hflet']['prop']}/rates/calculate",
                   data={'tax_year': '2025/26', 'rateable_value': '120000'})
    assert r.status_code == 400, 'the calculator accepts a request with no token'
finally:
    A.app.config['TESTING'] = True
print('53. the calculator works with CSRF live, and still refuses an untokened post')


# ─── 54. Everything is on the Property Overview ─────────────────────────────
page = cl.get(f"/properties/{IDS['hflet']['prop']}").get_data(as_text=True)
for expected in ('Local authority', 'Business Rates Calculator', 'Use class',
                 'Residential use', 'Property Details', 'Calculate Business Rates',
                 'On the particulars'):
    assert expected in page, f'{expected!r} is not on the Property Overview'
assert 'All details' not in page, 'the retired All details link is still there'
# The rates forms must not nest inside the property form.
assert page.index('id="rates-form"') > page.index('</form>'), \
    'the rates form is nested inside the property form'
r = cl.get(f"/properties/{IDS['hflet']['prop']}/edit")
assert r.status_code == 302 and r.headers['Location'].endswith(
    f"/properties/{IDS['hflet']['prop']}"), 'the old details page does not redirect'
print('54. every property detail is on the Overview, and the old page redirects')


print('\nBUSINESS RATES: ALL CHECKS PASSED')
