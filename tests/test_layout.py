"""The centred cover heading, and the Property Overview grid."""
import io, os, re, sys, tempfile
from html.parser import HTMLParser

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)

import app as A
import particulars as pp
import pymupdf
from PIL import Image
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db
CSS = open(f'{ROOT}/static/css/crm-grid.css').read()

LONG_ADDRESS = ('Ground and Lower Ground Floor, Riverside Works, '
                '118-124 Paddock Wood Industrial Estate, Tonbridge, Kent')
LONG_STRAP = ('EXCEPTIONALLY WELL APPOINTED GROUND AND LOWER GROUND FLOOR '
              'COMMERCIAL SHOWROOM PREMISES | FOR SALE | TO LET | CHELSEA SW3')


def shot(colour=(150, 160, 175)):
    b = io.BytesIO()
    Image.new('RGB', (1400, 950), colour).save(b, 'JPEG')
    return b.getvalue()


IDS = {}
with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    db.session.add(A.User(username='admin', password_hash=generate_password_hash('pw'),
                          role='admin', full_name='Benjamin Cowan',
                          email='bc@cowanandrutter.co.uk'))
    db.session.commit()
    council = A.Council.query.first()

    def build(key, address, strapline, photos=4, **kw):
        p = A.Property(address=address, postcode='SW6 1AA', property_type='Office',
                       size=1636, council_id=council.id)
        db.session.add(p); db.session.commit()
        pr = A.Project(name=key, property_id=p.id, fee_earner_id=1,
                       instruction_type=A.INSTRUCTION_TO_LET)
        db.session.add(pr); db.session.commit()
        l = A.Listing(project_id=pr.id, property_id=p.id, set_as_to_let=True,
                      listing_price=57260, listing_price_unit='pa',
                      strapline=strapline, blurb='A unit.',
                      location_description='Off the Kings Road.',
                      key_terms='New FRI lease\nAvailable now', epc_band='C', **kw)
        db.session.add(l); db.session.commit()
        for i in range(photos):
            db.session.add(A.ListingPhoto(listing_id=l.id, file_data=shot(),
                                          filename=f'{i}.jpg', file_mime='image/jpeg',
                                          file_size=1, sort_order=i))
        db.session.commit()
        IDS[key] = {'prop': p.id, 'proj': pr.id,
                    'photos': [x.id for x in A.ListingPhoto.query
                               .filter_by(listing_id=l.id).all()]}

    build('normal', 'Unit 2, Marlin House, 40 Peterborough Road, London SW6 3BN',
          'GROUND FLOOR COMMERCIAL UNIT | FULHAM SW6')
    build('long', LONG_ADDRESS, LONG_STRAP)
    build('bare', 'A House', None, photos=0)

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def cover_of(key, pages=2, url='preview'):
    r = cl.post(f"/projects/{IDS[key]['proj']}/particulars/{url}",
                data={'pages': str(pages), 'photo_ids': IDS[key]['photos'],
                      'no_floorplan_ok': '1'})
    assert r.status_code == 200, r.status_code
    return pymupdf.open(stream=r.get_data(), filetype='pdf'), r.get_data()


def lines_on(page):
    """Every text line with its box, in reading order down the page."""
    out = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            text = ''.join(s['text'] for s in line['spans']).strip()
            if text:
                out.append({'text': text, 'bbox': line['bbox'],
                            'size': round(line['spans'][0]['size'], 1)})
    return sorted(out, key=lambda l: l['bbox'][1])


PAGE_CENTRE = pp.PW / 2


# ─── 1. The three elements are in the required order ────────────────────────
doc, _ = cover_of('normal')
cover = doc[0]
band = [l for l in lines_on(cover) if l['bbox'][1] > cover.rect.height - 130]
texts = [l['text'] for l in band]
addr_i = next(i for i, t in enumerate(texts) if 'Marlin House' in t)
strap_i = next(i for i, t in enumerate(texts) if 'GROUND FLOOR' in t)
assert addr_i < strap_i, f'the strapline is above the address: {texts}'
print('1. the address is first and the strapline last, in that order')


# ─── 2. The grey rule sits between them ─────────────────────────────────────
addr_box = band[addr_i]['bbox']
strap_box = band[strap_i]['bbox']
rules = [d for d in cover.get_drawings()
         if d['rect'].y0 > addr_box[3] - 2 and d['rect'].y1 < strap_box[1] + 2
         and d['rect'].width > 40 and d['rect'].height < 4]
assert rules, 'no grey rule between the address and the strapline'
rule = rules[0]['rect']
print('2. the grey divider sits between the address and the strapline')


# ─── 3. All three share the page's centre point ─────────────────────────────
for name, box in (('address', addr_box), ('strapline', strap_box)):
    mid = (box[0] + box[2]) / 2
    assert abs(mid - PAGE_CENTRE) < 2, \
        f'the {name} is centred on {mid:.0f}, not the page centre {PAGE_CENTRE:.0f}'
rule_mid = (rule.x0 + rule.x1) / 2
assert abs(rule_mid - PAGE_CENTRE) < 2, \
    f'the rule is centred on {rule_mid:.0f}, not {PAGE_CENTRE:.0f}'
assert rule.x0 > 1, 'the rule is left-aligned against the page edge'
print('3. address, rule and strapline share the page centre, not a column centre')


# ─── 4. Spacing above and below the rule matches ────────────────────────────
above = rule.y0 - addr_box[3]
below = strap_box[1] - rule.y1
assert abs(above - below) < 6, \
    f'the rule is not evenly spaced: {above:.1f} above, {below:.1f} below'
print(f'4. the rule has matching space above and below ({above:.0f} and {below:.0f})')


# ─── 5. The rule never runs past the group ──────────────────────────────────
group_left = min(addr_box[0], strap_box[0])
group_right = max(addr_box[2], strap_box[2])
assert rule.x0 >= group_left - 1 and rule.x1 <= group_right + 1, \
    'the rule extends beyond the heading group'
assert rule.width <= 231, f'the rule is {rule.width:.0f}pt, past its maximum'
print('5. the rule stays inside the group and within its maximum width')


# ─── 6. Long text wraps, and every wrapped line stays centred ───────────────
doc, _ = cover_of('long')
cover = doc[0]
band = [l for l in lines_on(cover) if l['bbox'][1] > cover.rect.height - 200]
wrapped = [l for l in band if l['text'].isupper() and 'APPROX' not in l['text'].upper()]
assert len(wrapped) >= 2, f'a very long strapline did not wrap: {wrapped}'
for line in band:
    if 'Approx' in line['text']:
        continue                      # the floor area is not part of the group
    mid = (line['bbox'][0] + line['bbox'][2]) / 2
    assert abs(mid - PAGE_CENTRE) < 3, \
        f'wrapped line {line["text"][:40]!r} is centred on {mid:.0f}'
print('6. long addresses and straplines wrap, and every line stays centred')


# ─── 7. The | separators survive exactly ────────────────────────────────────
strap_text = ' '.join(l['text'] for l in band
                      if l['text'].isupper() and 'APPROX' not in l['text'].upper())
assert '|' in strap_text, 'the | separators were removed'
assert strap_text.count('|') == LONG_STRAP.count('|'), \
    f'separators lost: {strap_text.count("|")} of {LONG_STRAP.count("|")}'
for wrong in (' / ', ' – ', ' — ', ' - '):
    assert wrong not in strap_text, f'a separator was turned into {wrong!r}'
# And nothing was broken onto its own line in place of a separator.
assert 'FOR SALE | TO LET' in strap_text, \
    f'a separator was replaced by a line break: {strap_text!r}'
print('7. the | separators are printed as they were entered')


# ─── 8. No price, and no space held where one used to be ────────────────────
doc, _ = cover_of('normal')
front = ' '.join(l['text'] for l in lines_on(doc[0]))
assert '£' not in front, f'a price is on the cover: {front}'
assert 'application' not in front.lower()
csrc = open(f'{ROOT}/particulars.py').read()
block = csrc[csrc.index('def cover_page('):]
block = block[:block.index('\ndef ')]
assert "data.get('price')" not in block and "'rent'" not in block, \
    'the cover still reads a price'
print('8. no rent, price or reserved space for one on the cover')


# ─── 9. Nothing in the group overlaps the logo, floor area or page edge ─────
for key in ('normal', 'long'):
    doc, _ = cover_of(key)
    page = doc[0]
    band = [l for l in lines_on(page) if l['bbox'][1] > page.rect.height - 210]
    size_line = next((l for l in band if 'Approx' in l['text']), None)
    logo = [b for b in (page.get_image_bbox(i) for i in page.get_images(full=True))
            if b.y0 > page.rect.height - 200]
    for line in band:
        x0, y0, x1, y1 = line['bbox']
        assert x0 >= 0 and x1 <= page.rect.width, \
            f'{key}: a heading line runs off the page'
        assert y1 <= page.rect.height, f'{key}: a heading line runs off the bottom'
        if size_line and line is not size_line:
            assert x1 <= size_line['bbox'][0] + 1, \
                f'{key}: the heading overlaps the floor area'
        for box in logo:
            overlaps = not (x1 < box.x0 or x0 > box.x1 or y1 < box.y0 or y0 > box.y1)
            assert not overlaps, f'{key}: the heading overlaps the mark'
print('9. the group clears the mark, the floor area and both page edges')


# ─── 10. One cover component, and the download matches the preview ──────────
assert csrc.count('def cover_page(') == 1, 'more than one cover component'
calls = csrc.count('cover_page(canvas') - csrc.count('def cover_page(canvas')
assert calls == 1, f'the cover is drawn from {calls} places'
two, two_bytes = cover_of('normal', 2)
four, four_bytes = cover_of('normal', 4)
assert lines_on(two[0]) == lines_on(four[0]), \
    'page one differs between the two-page and four-page particulars'
_, dl = cover_of('normal', 2, url='download')
prev_lines = [l['text'] for l in lines_on(pymupdf.open(stream=two_bytes,
                                                       filetype='pdf')[0])]
dl_lines = [l['text'] for l in lines_on(pymupdf.open(stream=dl, filetype='pdf')[0])]
assert prev_lines == dl_lines, 'the downloaded cover differs from the preview'
print('10. one cover component; preview, download and both formats agree')


# ─── 11. A property with no strapline still centres its address ─────────────
doc, _ = cover_of('bare')
band = [l for l in lines_on(doc[0]) if l['bbox'][1] > doc[0].rect.height - 130]
assert band, 'the cover has no heading at all'
for line in band:
    if 'Approx' in line['text']:
        continue
    mid = (line['bbox'][0] + line['bbox'][2]) / 2
    assert abs(mid - PAGE_CENTRE) < 3, 'the heading is not centred without a strapline'
print('11. a cover with no strapline still centres what it has')


# ─── 12. The container fault is fixed at its cause ──────────────────────────
page_rule = CSS[CSS.index('.rec-page {'):]
page_rule = page_rule[:page_rule.index('}')]
assert 'margin-inline: auto' in page_rule, \
    '.rec-page is still capped and left-aligned'
assert 'width: 100%' in page_rule and 'box-sizing: border-box' in page_rule
proj = CSS[CSS.index('.rec-cols-proj {'):]
proj = proj[:proj.index('}')]
assert 'margin-inline: auto' in proj, '.rec-cols-proj is still left-aligned'
print('12. the capped-then-left-aligned containers now centre their spare width')


# ─── 13. The Overview no longer borrows the project's fixed rail ────────────
html = open(f'{ROOT}/templates/properties/detail.html').read()
assert 'rec-cols-proj' not in html, \
    "the Property Overview still uses the Project record's 320px rail"
assert 'prop-grid' in html
grid = CSS[CSS.index('.prop-grid {'):]
grid = grid[:grid.index('}')]
assert 'repeat(2, minmax(0, 1fr))' in grid, 'the grid is not two balanced columns'
assert 'width: 100%' in grid and 'box-sizing: border-box' in grid
assert 'align-items: start' in grid, 'boxes are stretched to a common height'
assert '.prop-grid > * { min-width: 0; }' in CSS, 'a long word can force a column wide'
print('13. the Overview has its own full-width, two-column grid')


# ─── 14. It stacks on narrow screens, by container and by viewport ──────────
assert '@container (max-width: 900px)' in CSS and '@media (max-width: 900px)' in CSS
after = CSS[CSS.index('@container (max-width: 900px)'):]
assert 'minmax(0, 1fr)' in after[:200], 'the grid does not fall to one column'
assert 'overflow-wrap: anywhere' in CSS
print('14. it falls to one column on tablets and phones, with no sideways scroll')


# ─── 15. Detailed sections span the full width ──────────────────────────────
assert '.prop-span { grid-column: 1 / -1; }' in CSS
for section in ('rates_calculator', 'Property Details', 'client_details'):
    assert section in html
spans = html.count('prop-span')
assert spans >= 3, f'only {spans} sections span the width'
assert 'prop-span' in html[html.index('Property Details') - 400:
                           html.index('Property Details')], \
    'Property Details does not span the width'
print('15. the calculator, the record and the client details span both columns')


# ─── 16. Every field previously on All Property Details is still here ───────
page = cl.get(f"/properties/{IDS['normal']['prop']}").get_data(as_text=True)
for field in ('Address', 'Postcode', 'Local authority', 'Property type', 'Area',
              'Floor area', 'Basis', 'Use class', 'Residential use',
              'Beds / baths', 'Description', 'Added', 'Client',
              'Business Rates Calculator', 'Rateable value', 'Tax year',
              'Multiplier', 'Relief', 'Additional adjustments',
              'Estimated business rates payable', 'Instructions', 'Photos',
              'Client details'):
    assert field in page, f'{field!r} is missing from the Property Overview'
print('16. every field from the old All Property Details page is still there')


# ─── 17. The removed boxes have not come back ───────────────────────────────
for gone in ('Council-confirmed', 'Confirmed by council', 'rates_confirmed',
             'All details'):
    assert gone not in page, f'{gone!r} has reappeared'
assert page.count('Client details') == 1, 'the duplicate client box is back'
print('17. no council confirmation box and no duplicate client details')


# ─── 18. The page is valid, closed markup ───────────────────────────────────
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


for key in ('normal', 'long', 'bare'):
    body = cl.get(f"/properties/{IDS[key]['prop']}").get_data(as_text=True)
    p = Balance()
    p.feed(body)
    assert not p.bad, f'{key}: mismatched tags {p.bad[:3]}'
    assert not p.stack, f'{key}: unclosed {p.stack[:3]}'
print('18. the Overview renders as valid, fully closed markup')


# ─── 19. A property with almost nothing on it still lays out ────────────────
body = cl.get(f"/properties/{IDS['bare']['prop']}").get_data(as_text=True)
assert 'Property Details' in body and 'Business Rates Calculator' in body
assert 'prop-grid' in body
print('19. a nearly empty property still lays out on the same grid')


# ─── 20. A very long address does not force the page sideways ───────────────
body = cl.get(f"/properties/{IDS['long']['prop']}").get_data(as_text=True)
assert LONG_ADDRESS in body
assert 'overflow-wrap: anywhere' in CSS
# No fixed pixel width was reintroduced anywhere in the Overview's own rules.
own = CSS[CSS.index('/* ── Property Overview'):]
declarations = re.sub(r'@\w+\s*\([^)]*\)', '', own)
fixed = re.findall(r'(?<!max-)(?<!min-)width:\s*\d{3,}px', declarations)
assert not fixed, f'a fixed pixel width crept into the Overview rules: {fixed}'
props = re.findall(r'(?<![-\w])(position:\s*absolute|transform:)', declarations)
assert not props, f'the layout is being propped up rather than fixed: {props}'
assert not re.search(r'margin[^:]*:\s*-\d', declarations), \
    'a negative margin is compensating for the layout'
print('20. a long address wraps; no fixed widths or absolute positioning added')


# ─── 21. Form fields: label above, and they fit their box ───────────────────
pf = CSS[CSS.index('.pf-grid {'):]
pf = pf[:pf.index('}')]
assert 'repeat(auto-fit, minmax(190px, 1fr))' in pf, \
    'fields do not reflow from three across to one'
assert '.pf-field { display: flex; flex-direction: column' in CSS, \
    'labels are not above their inputs'
assert 'width: 100%; box-sizing: border-box; min-width: 0;' in CSS, \
    'an input can overflow its cell'
print('21. fields put the label above, reflow by width, and stay in their box')


# ─── 22. The calculator still works after the move ──────────────────────────
with A.app.app_context():
    mid = A.RatesMultiplier.query.filter_by(tax_year='2025/26',
                                            name='Standard multiplier').first().id
out = cl.post(f"/properties/{IDS['normal']['prop']}/rates/calculate",
              data={'tax_year': '2025/26', 'rateable_value': '120000',
                    'multiplier_id': mid}).get_json()
assert out['ok'] and out['result']['total'] == '£66,600.00', out
assert 'Additional adjustments' in page and 'data-br-result' in page
print('22. the rates calculator still calculates from its new place on the grid')


# ─── 23. Both formats still produce a document ──────────────────────────────
for pages in (2, 4):
    doc, raw = cover_of('long', pages, url='download')
    assert raw[:5] == b'%PDF-' and doc.page_count == pages
    for page in doc:
        assert abs(page.rect.width - 842) < 2 and abs(page.rect.height - 595) < 2
print('23. both formats still download, at A4 landscape and full size')

print('\nCOVER AND OVERVIEW LAYOUT: ALL CHECKS PASSED')
