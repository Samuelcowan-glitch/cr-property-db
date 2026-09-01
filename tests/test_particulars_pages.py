"""Four-page particulars, the shared first two pages, and Key Terms."""
import io, os, re, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)

import app as A
import particulars as pp
import zoopla_feed as zf
import pymupdf
from PIL import Image, ImageDraw
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db
KT = A.key_terms_list

TERMS = ('New full repairing and insuring lease\n'
         'Available immediately\n'
         'Air-conditioned throughout\n'
         '24-hour access\n'
         'Self-contained with its own entrance\n'
         'Three allocated parking spaces\n'
         'A seventh term that must not be printed')


def shot(colour, w=1400, h=950):
    b = io.BytesIO()
    Image.new('RGB', (w, h), colour).save(b, 'JPEG')
    return b.getvalue()


def plan(label='RECEPTION 8.20m x 6.10m'):
    im = Image.new('RGB', (1600, 1100), 'white')
    d = ImageDraw.Draw(im)
    d.rectangle([40, 40, 1560, 1060], outline='black', width=5)
    d.text((60, 60), label, fill='black')
    d.text((60, 1070), 'Scale 1:100', fill='black')
    b = io.BytesIO()
    im.save(b, 'PNG')
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

    def build(key, terms=TERMS, photos=7, floorplan=True, **kw):
        p = A.Property(address=f'{key.title()} House, London SW6 1AA',
                       postcode='SW6 1AA', property_type='Office', size=1636,
                       council_id=council.id,
                       floor_plan_data=plan() if floorplan else None,
                       floor_plan_filename='Plan.png' if floorplan else None)
        db.session.add(p); db.session.commit()
        pr = A.Project(name=key, property_id=p.id, fee_earner_id=1,
                       instruction_type=A.INSTRUCTION_TO_LET)
        db.session.add(pr); db.session.commit()
        l = A.Listing(project_id=pr.id, property_id=p.id, set_as_to_let=True,
                      listing_price=57260, listing_price_unit='pa',
                      strapline=f'{key.upper()} STRAPLINE | FULHAM',
                      blurb='A well presented ground floor commercial unit.',
                      location_description='Situated on Peterborough Road.',
                      key_terms=terms, epc_band='C', **kw)
        db.session.add(l); db.session.commit()
        shapes = [(1400, 950), (900, 1400), (1400, 950), (1400, 950),
                  (900, 1400), (1400, 950), (1400, 950), (1400, 950)]
        for i in range(photos):
            w, h = shapes[i % len(shapes)]
            db.session.add(A.ListingPhoto(
                listing_id=l.id, file_data=shot((110 + i * 12, 130, 150), w, h),
                filename=f'{i}.jpg', file_mime='image/jpeg', file_size=1,
                sort_order=i))
        db.session.commit()
        IDS[key] = {'prop': p.id, 'proj': pr.id, 'listing': l.id,
                    'photos': [x.id for x in A.ListingPhoto.query
                               .filter_by(listing_id=l.id)
                               .order_by(A.ListingPhoto.sort_order).all()]}

    build('full')
    build('fewterms', terms='One term\nTwo term')
    build('noterms', terms=None)
    build('noplan', floorplan=False)
    build('fewpics', photos=2)

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def render(key, pages=2, **extra):
    data = {'pages': str(pages), 'photo_ids': IDS[key]['photos']}
    data.update(extra)
    r = cl.post(f"/projects/{IDS[key]['proj']}/particulars/preview", data=data)
    assert r.status_code == 200, r.status_code
    return pymupdf.open(stream=r.get_data(), filetype='pdf')


def words(page):
    return re.sub(r'\s+', ' ', page.get_text()).strip()


def images_on(page):
    return [page.get_image_bbox(i) for i in page.get_images(full=True)]


def content_images(page):
    """Photographs and floorplans, not the little logo in the footer."""
    return [b for b in images_on(page) if b.height > 60]


# ─── 1. Key Terms are parsed however they were typed ────────────────────────
assert KT('One\nTwo\nThree') == ['One', 'Two', 'Three']
assert KT('• One\n• Two') == ['One', 'Two'], KT('• One\n• Two')
assert KT('One · Two · Three') == ['One', 'Two', 'Three']
assert KT('- Self-contained unit\n- Air-conditioned') == \
    ['Self-contained unit', 'Air-conditioned'], 'a hyphen inside a term was split'
assert KT('1. First\n2. Second') == ['First', 'Second']
assert KT('Same\nsame\nSAME') == ['Same'], 'a duplicate term was kept'
assert KT(None) == [] and KT('') == []
print('1. key terms are read from lines, bullets, dots or a numbered list')


# ─── 2. Wording, capitalisation and order are untouched ─────────────────────
typed = 'EPC rating C\nNew FRI lease\nair-conditioned\n24/7 access'
assert KT(typed) == ['EPC rating C', 'New FRI lease', 'air-conditioned', '24/7 access'], \
    'the terms were reworded, recapitalised or reordered'
print('2. wording, capitalisation and order are exactly as entered')


# ─── 3. The particulars take Key Terms from the Key Terms field alone ───────
with A.app.app_context():
    data = A.particulars_data(A.Project.query.get(IDS['full']['proj']))
assert data['key_terms'][0] == 'New full repairing and insuring lease'
assert 'STRAPLINE' not in ' '.join(data['key_terms']), 'the strapline became a term'
assert 'well presented' not in ' '.join(data['key_terms']), 'the description became a term'
assert 'Peterborough' not in ' '.join(data['key_terms']), 'the location became a term'
assert 'TO LET' not in ' '.join(data['key_terms']), 'the instruction became a term'
src = open(f'{ROOT}/app.py').read()
start = src.index("'key_terms': key_terms_list(")
expr = src[start:src.index("'key_terms_all'", start)]
for wrong in ('strapline', 'blurb', 'description', 'location', 'instruction'):
    assert wrong not in expr, f'{wrong} can reach the key terms: {expr}'
assert "getattr(listing, 'key_terms'" in expr, expr
print('3. key terms come from the Key Terms field and nothing stands in for it')


# ─── 4. At most six, in saved order, no duplicates ──────────────────────────
assert len(data['key_terms']) == 6, len(data['key_terms'])
assert 'seventh term' not in ' '.join(data['key_terms']), 'a seventh term printed'
assert data['key_terms'] == KT(TERMS)[:6], 'the order changed'
assert len(set(data['key_terms'])) == 6
print('4. the first six saved terms print, in order, and no more')


# ─── 5. Fewer than six, and none at all ─────────────────────────────────────
with A.app.app_context():
    few = A.particulars_data(A.Project.query.get(IDS['fewterms']['proj']))
    none = A.particulars_data(A.Project.query.get(IDS['noterms']['proj']))
assert few['key_terms'] == ['One term', 'Two term']
assert none['key_terms'] == [], none['key_terms']
text = words(render('noterms', 2)[1])
assert 'Key Terms' not in text, 'an empty Key Terms box was printed'
assert 'Business Rates' in text and 'Rent' in text, \
    'removing the empty box took the rest of the page with it'
print('5. fewer terms shrink the box; none removes it and the page closes up')


# ─── 6. Key Terms missing is reported before anything is made ───────────────
with A.app.app_context():
    gaps = A.particulars_gaps(none, 3)
assert 'Key Terms missing' in gaps, gaps
page = cl.get(f"/projects/{IDS['noterms']['proj']}/particulars").get_data(as_text=True)
assert 'Key Terms missing' in page
assert f"/projects/{IDS['noterms']['proj']}" in page, 'no way back to enter them'
with A.app.app_context():
    assert 'Key Terms missing' not in A.particulars_gaps(data, 3)
print('6. missing key terms are reported, with a way back to enter them')


# ─── 7. Bullets are printed as bullets, one per term ────────────────────────
text = words(render('full', 2)[1])
assert 'Key Terms' in text
for term in KT(TERMS)[:6]:
    assert term in text, f'{term!r} is not on the brochure'
assert 'A seventh term' not in text
# Each is its own bullet rather than a run-on sentence.
raw = render('full', 2)[1].get_text()
bullets = [l for l in raw.split('\n') if l.strip().startswith('•')]
assert len(bullets) >= 6, f'terms were joined instead of bulleted: {bullets}'
print('7. each term prints as its own bullet, never combined')


# ─── 8. Pages one and two are identical in both formats ─────────────────────
two, four = render('full', 2), render('full', 4)
assert two.page_count == 2 and four.page_count == 4
for n in (0, 1):
    assert words(two[n]) == words(four[n]), \
        f'page {n + 1} differs between the two-page and four-page versions'
    a = [tuple(round(v) for v in b) for b in images_on(two[n])]
    b = [tuple(round(v) for v in x) for x in images_on(four[n])]
    assert a == b, f'page {n + 1} uses different pictures in the two formats'
print('8. pages one and two are the same page one and page two in both formats')


# ─── 9. There is only one implementation of them ────────────────────────────
psrc = open(f'{ROOT}/particulars.py').read()
assert psrc.count('def cover_page(') == 1 and psrc.count('def detail_page(') == 1
build_fn = psrc[psrc.index('def build('):psrc.index('def photo_plan(')]
assert build_fn.count('cover_page(canvas') == 1, 'the cover is drawn in two places'
assert build_fn.count('detail_page(canvas') == 1, 'page two is drawn in two places'
assert 'with_terms' not in psrc and 'gallery_page' not in psrc, \
    'the old four-page-only layout is still there to drift'
print('9. one cover and one page two, called once — the formats cannot drift')


# ─── 10. Page three is photographs and nothing else ─────────────────────────
three = words(four[2])
assert 'FURTHER PHOTOGRAPHS' in three.upper()
for repeated in ('Description', 'Location', 'Key Terms', 'Service Charge',
                 'Business Rates', 'Rent', 'EPC', 'Misrepresentation'):
    assert repeated not in three, f'{repeated} is repeated on page three'
assert len(content_images(four[2])) >= 2, 'page three has no photographs'
print('10. page three carries photographs only, with no repeated copy')


# ─── 11. No photograph appears twice ────────────────────────────────────────
with A.app.app_context():
    order = [x.file_data for x in A.ListingPhoto.query
             .filter_by(listing_id=IDS['full']['listing'])
             .order_by(A.ListingPhoto.sort_order).all()]
plan_map = pp.photo_plan(order, 4)
used = plan_map['cover'] + plan_map['detail'] + plan_map['gallery']
assert len(used) == len({id(x) for x in used}), 'a photograph is used twice'
assert plan_map['cover'] == order[:1]
assert plan_map['detail'] == order[1:4], 'page two does not take the next three'
assert plan_map['gallery'] == order[4:7], 'page three does not take the rest'
print('11. the cover, page two and page three take different photographs')


# ─── 12. Photographs stay inside the printable area ─────────────────────────
W, H = pp.PW, pp.PH
for count in range(1, 7):
    for x, y, w, h in pp.gallery_layout([shot((120, 130, 150))] * count):
        assert x >= pp.MARGIN - 1 and x + w <= W - pp.MARGIN + 1, \
            f'a gallery cell runs off the side with {count} photographs'
        assert y >= 0 and y + h <= H, \
            f'a gallery cell runs off the top or bottom with {count} photographs'
        assert y >= 40, 'a gallery cell overlaps the footer'
# The floorplan is drawn whole, so it really must be inside the page.
for box in content_images(four[3]):
    assert box.x0 >= -1 and box.x1 <= W + 1, 'the floorplan runs off the side'
    assert box.y0 >= -1 and box.y1 <= H + 1, 'the floorplan runs off the page'
    assert box.y1 <= H - 40, 'the floorplan overlaps the footer'
# And no text on any page falls outside it.
for n in range(4):
    for block in four[n].get_text('blocks'):
        x0, y0, x1, y1 = block[:4]
        assert x0 >= -1 and x1 <= W + 1 and y0 >= -1 and y1 <= H + 1, \
            f'text runs off page {n + 1}: {block[4]!r}'
print('12. gallery cells, the floorplan and all text stay inside the page')


# ─── 13. Landscape and portrait both get a sensible cell ────────────────────
with A.app.app_context():
    tall = [shot((120, 130, 150), 900, 1400), shot((140, 130, 150), 900, 1400)]
    wide = [shot((120, 130, 150), 1400, 900), shot((140, 130, 150), 1400, 900)]
tall_boxes = pp.gallery_layout(tall)
wide_boxes = pp.gallery_layout(wide)
assert len(tall_boxes) == 2 and len(wide_boxes) == 2
assert tall_boxes[0][2] < tall_boxes[0][3], 'two portraits were given wide cells'
assert wide_boxes[0][2] > wide_boxes[0][3], 'two landscapes were given tall cells'
for count in (1, 2, 3, 4, 5, 6):
    boxes = pp.gallery_layout([shot((120, 130, 150))] * count)
    assert len(boxes) == count, f'{count} photographs produced {len(boxes)} cells'
    for x, y, w, h in boxes:
        assert w > 0 and h > 0
print('13. the gallery layout follows the number and the shape of the pictures')


# ─── 14. Too few photographs uses a smaller layout, never a repeat ──────────
short = render('fewpics', 4)
assert short.page_count == 4
imgs = content_images(short[2])
assert len(imgs) == 0, 'page three invented photographs it did not have'
assert 'No further photographs' in words(short[2]), \
    'page three was left blank with no explanation'
page = cl.get(f"/projects/{IDS['fewpics']['proj']}/particulars").get_data(as_text=True)
assert 'Not enough photographs' in page, 'the screen does not warn about page three'
print('14. too few photographs gives a fitting layout and says so, never a repeat')


# ─── 15. The floorplan is on page four, whole and uncropped ─────────────────
plan_page = four[3]
assert 'FLOORPLAN' in words(plan_page).upper()
boxes = content_images(plan_page)
assert boxes, 'no floorplan was drawn'
box = boxes[0]
assert abs((box.width / box.height) - (1600 / 1100)) < 0.02, \
    'the floorplan was stretched or squashed'
# Contained, not covered: the whole image fits in its space, so no edge — and
# so no room label, dimension or scale bar — can have been trimmed off.
area_w, area_h = pp.PW - pp.MARGIN * 2, plan_page.rect.height
assert box.width <= area_w + 1 and box.height <= area_h + 1, \
    'the floorplan was scaled past its box and cropped'
psrc_fit = psrc[psrc.index('def floorplan_page('):]
psrc_fit = psrc_fit[:psrc_fit.index('\ndef ')] if '\ndef ' in psrc_fit else psrc_fit
assert psrc_fit.count('fit=True') >= 2, 'a floorplan is drawn without fit=True'
assert 'fit=False' not in psrc_fit
print('15. the floorplan is fitted whole, with its labels and scale bar intact')


# ─── 16. Nothing is printed over the floorplan ──────────────────────────────
plan_box = content_images(plan_page)[0]
for block in plan_page.get_text('blocks'):
    x0, y0, x1, y1 = block[:4]
    overlaps = not (x1 < plan_box.x0 or x0 > plan_box.x1
                    or y1 < plan_box.y0 or y0 > plan_box.y1)
    assert not overlaps, f'text sits on top of the floorplan: {block[4]!r}'
print('16. no branding or text is laid over the floorplan')


# ─── 17. Two floorplans sit side by side ────────────────────────────────────
with A.app.app_context():
    listing = A.Listing.query.get(IDS['full']['listing'])
    listing.floor_plan_data = plan('FIRST FLOOR 6.00m x 4.20m')
    listing.floor_plan_filename = 'First floor.png'
    db.session.commit()
    available = A.project_floorplans(A.Project.query.get(IDS['full']['proj']))
assert len(available) == 2, [f['name'] for f in available]
both = render('full', 4, floorplan_keys=[f['key'] for f in available])
assert len(content_images(both[3])) == 2, 'only one of the two floorplans was drawn'
page = cl.get(f"/projects/{IDS['full']['proj']}/particulars").get_data(as_text=True)
assert 'First floor.png' in page and 'Plan.png' in page, \
    'the screen does not offer a choice of floorplan'
print('17. two floorplans are offered, and both print side by side when chosen')


# ─── 18. Choosing one floorplan uses that one ───────────────────────────────
one = render('full', 4, floorplan_keys=[available[0]['key']])
assert len(content_images(one[3])) == 1
print('18. choosing a single floorplan prints only that one')


# ─── 19. A missing floorplan is warned about, not silently blank ────────────
page = cl.get(f"/projects/{IDS['noplan']['proj']}/particulars").get_data(as_text=True)
assert 'No floorplan uploaded' in page
assert 'two-page' in page and 'no_floorplan_ok' in page, \
    'the screen offers no way out of the problem'
r = cl.post(f"/projects/{IDS['noplan']['proj']}/particulars/download",
            data={'pages': '4', 'photo_ids': IDS['noplan']['photos']},
            follow_redirects=True)
assert 'No floorplan has been uploaded' in r.get_data(as_text=True), \
    'four-page particulars were produced with no floorplan and no warning'
print('19. four pages without a floorplan is refused until it is confirmed')


# ─── 20. Confirming goes ahead, and page four says what happened ────────────
r = cl.post(f"/projects/{IDS['noplan']['proj']}/particulars/download",
            data={'pages': '4', 'photo_ids': IDS['noplan']['photos'],
                  'no_floorplan_ok': '1'})
assert r.status_code == 200 and r.get_data()[:5] == b'%PDF-'
doc = pymupdf.open(stream=r.get_data(), filetype='pdf')
assert doc.page_count == 4
assert 'No floorplan has been uploaded' in words(doc[3]), \
    'page four was left silently blank'
# Two pages never asks.
r = cl.post(f"/projects/{IDS['noplan']['proj']}/particulars/download",
            data={'pages': '2', 'photo_ids': IDS['noplan']['photos']})
assert r.status_code == 200 and r.get_data()[:5] == b'%PDF-'
print('20. going ahead is allowed once confirmed, and page four explains itself')


# ─── 21. The user can choose and reorder the terms for one document ─────────
picked = ['24-hour access', 'New full repairing and insuring lease']
doc = render('full', 2, key_terms=picked)
raw = doc[1].get_text()
first = raw.index('24-hour access')
second = raw.index('New full repairing and insuring lease')
assert first < second, 'the chosen order was not followed'
assert 'Three allocated parking spaces' not in raw, 'an unchosen term printed'
print('21. the terms chosen for a document print in the order chosen')


# ─── 22. A term that was never saved cannot be injected ─────────────────────
doc = render('full', 2, key_terms=['Free Ferrari with every letting'])
raw = doc[1].get_text()
assert 'Ferrari' not in raw, 'a term that is not on the record reached the brochure'
assert 'New full repairing' in raw, 'the saved terms were dropped as well'
print('22. only saved terms can be printed; nothing can be injected by request')


# ─── 23. Never more than six, however many are asked for ────────────────────
doc = render('full', 2, key_terms=KT(TERMS))
raw = doc[1].get_text()
printed = sum(1 for t in KT(TERMS) if t in raw)
assert printed == 6, f'{printed} terms printed'
print('23. asking for all seven still prints six')


# ─── 24. The website feed sends the terms as a list ─────────────────────────
feed = cl.get('/api/listings').get_json()
row = next((x for x in feed if 'Full House' in (x.get('address') or '')), None)
if row:
    assert row['keyTerms'].count(chr(10)) >= 5, \
        'the website is still sent one run-on string'
    assert isinstance(row['keyTermsList'], list) and len(row['keyTermsList']) == 7
    assert row['keyTermsList'][0] == 'New full repairing and insuring lease'
    # The website splits on newlines, so this now yields one bullet per term.
    assert len([t for t in row['keyTerms'].split(chr(10)) if t.strip()]) == 7
print('24. the website receives one term to a line, and a list beside it')


# ─── 25. Zoopla gets one term per feature slot ──────────────────────────────
with A.app.app_context():
    listing = A.Listing.query.get(IDS['full']['listing'])
    features = zf._features(listing)
filled = [features[f'FEATURE{i}'] for i in range(1, 11) if features[f'FEATURE{i}']]
assert len(filled) >= 7, f'only {len(filled)} feature slots filled'
assert features['FEATURE1'] == 'New full repairing and insuring lease'
assert features['FEATURE2'] == 'Available immediately'
assert chr(10) not in features['FEATURE1'], 'a slot holds more than one term'
assert len(features['FEATURE1']) < 60, 'every term was crammed into one slot'
print('25. Zoopla receives each key term in its own feature slot')


# ─── 26. Saving normalises whatever was typed ───────────────────────────────
cl.post(f"/listings/{IDS['fewterms']['listing']}/edit",
        data={'key_terms': '• Alpha · Beta\n- Gamma'}, follow_redirects=True)
with A.app.app_context():
    stored = A.Listing.query.get(IDS['fewterms']['listing']).key_terms
assert stored == chr(10).join(['Alpha', 'Beta', 'Gamma']), repr(stored)
print('26. however the terms are typed, they are stored one to a line')


# ─── 27. The screen shows where every photograph goes ───────────────────────
page = cl.get(f"/projects/{IDS['full']['proj']}/particulars").get_data(as_text=True)
for label in ('Cover', 'Page 2', 'Page 3', 'Not used'):
    assert label in page, f'the screen never mentions {label!r}'
assert 'data-pt-photo' in page and 'draggable' in page, 'photographs cannot be reordered'
assert 'data-pt-terms' in page, 'the key terms cannot be reordered'
js = open(f'{ROOT}/static/js/particulars.js').read()
assert 'is-gallery' in js and 'is-cover' in js, 'the tiles never say where they land'
print('27. the screen shows cover, page two, page three and excluded photographs')


# ─── 28. Both formats still download, and print at full size ────────────────
for pages in (2, 4):
    r = cl.post(f"/projects/{IDS['full']['proj']}/particulars/download",
                data={'pages': str(pages), 'photo_ids': IDS['full']['photos']})
    assert r.status_code == 200 and r.get_data()[:5] == b'%PDF-'
    doc = pymupdf.open(stream=r.get_data(), filetype='pdf')
    assert doc.page_count == pages
    for page in doc:
        # A4 landscape at 100%: 842 x 595pt, within a rounding point.
        assert abs(page.rect.width - 842) < 2 and abs(page.rect.height - 595) < 2, \
            f'page is {page.rect.width}x{page.rect.height}pt, not A4 landscape'
print('28. both formats download as four-square A4 landscape at 100%')


# ─── 29. Nothing else on the record was disturbed ───────────────────────────
with A.app.app_context():
    l = A.Listing.query.get(IDS['full']['listing'])
    assert l.strapline == 'FULL STRAPLINE | FULHAM'
    assert l.blurb and l.location_description
    assert l.key_terms == TERMS, 'making a brochure rewrote the saved key terms'
print('29. making a brochure changes nothing on the instruction')

print('\nPARTICULARS PAGES AND KEY TERMS: ALL CHECKS PASSED')
