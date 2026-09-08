"""Floor plans render, and the finished brochure fits in an email.

Two faults this covers, both reproduced before they were fixed:

  - A floor plan arrives as a PDF more often than not, because that is what a
    surveyor sends. ReportLab cannot read a PDF, so draw_image fell into its
    exception branch and painted a grey box where the plan should be.

  - Photographs were embedded at whatever size they arrived. A phone produces
    4000x3000 at nine megabytes, and a four-page brochure came to eighty —
    too large to attach to an email.
"""
import hashlib
import io
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/broch.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
import particulars as pp
import pymupdf
from PIL import Image
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db


def big_photo(seed):
    """A photograph as a phone produces one: 4000x3000, several megabytes."""
    im = Image.new('RGB', (4000, 3000))
    px = im.load()
    for y in range(0, 3000, 3):
        for x in range(0, 4000, 3):
            px[x, y] = ((x + seed * 37) % 256, (y + seed * 11) % 256,
                        (x * y // 900) % 256)
    b = io.BytesIO()
    im.save(b, 'JPEG', quality=95)
    return b.getvalue()


def plan_image():
    im = Image.new('RGB', (2400, 1700), 'white')
    b = io.BytesIO()
    im.save(b, 'PNG')
    return b.getvalue()


def plan_pdf():
    """What a surveyor sends."""
    from reportlab.pdfgen import canvas as pdfcanvas
    b = io.BytesIO()
    c = pdfcanvas.Canvas(b, pagesize=(842, 595))
    c.rect(40, 40, 762, 500)
    c.setFont('Helvetica', 9)
    c.drawString(70, 505, 'RECEPTION 8.20m x 6.10m')
    c.drawString(70, 55, 'Scale 1:100')
    c.showPage()
    c.save()
    return b.getvalue()


PHOTOS = [big_photo(i) for i in range(4)]

with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    A._migrate_progression_columns()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          email='bc@cowanandrutter.co.uk',
                          password_hash=generate_password_hash('pw')))
    db.session.commit()
    council = A.Council.query.first()

    def build(key, plan_bytes, plan_name):
        p = A.Property(address=f'{key} House, London SW6 1AA', postcode='SW6 1AA',
                       property_type='Office', size=1636, council_id=council.id,
                       floor_plan_data=plan_bytes, floor_plan_filename=plan_name)
        db.session.add(p); db.session.commit()
        pr = A.Project(name=key, property_id=p.id, fee_earner_id=1,
                       instruction_type=A.INSTRUCTION_TO_LET)
        db.session.add(pr); db.session.commit()
        l = A.Listing(project_id=pr.id, property_id=p.id, set_as_to_let=True,
                      listing_price=57260, listing_price_unit='pa', epc_band='C',
                      service_charge=4.5,
                      strapline=f'{key.upper()} | FULHAM', blurb='A unit.',
                      location_description='Off the Kings Road.',
                      key_terms='New FRI lease\nAvailable now')
        db.session.add(l); db.session.commit()
        for i in range(7):
            db.session.add(A.ListingPhoto(
                listing_id=l.id, file_data=PHOTOS[i % len(PHOTOS)],
                filename=f'{i}.jpg', file_mime='image/jpeg', file_size=1,
                sort_order=i))
        db.session.commit()
        return {'proj': pr.id,
                'photos': [x.id for x in A.ListingPhoto.query.filter_by(listing_id=l.id)]}

    PNG = build('PngPlan', plan_image(), 'plan.png')
    PDF = build('PdfPlan', plan_pdf(), 'plan.pdf')
    NONE = build('NoPlan', None, None)

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def make(ids, pages, url='download', **extra):
    data = {'pages': str(pages), 'photo_ids': ids['photos']}
    data.update(extra)
    r = cl.post(f"/projects/{ids['proj']}/particulars/{url}", data=data)
    assert r.status_code == 200, r.status_code
    return r.get_data()


def content_images(page):
    return [b for b in (page.get_image_bbox(i) for i in page.get_images(full=True))
            if b.height > 60]


# ─── 1. A PDF floor plan is drawn, not left as a grey box ───────────────────
raw = make(PDF, 4)
doc = pymupdf.open(stream=raw, filetype='pdf')
plan_page = doc[3]
drawn = content_images(plan_page)
assert drawn, 'a PDF floor plan produced nothing on page four'
assert 'FLOORPLAN' in plan_page.get_text().upper()
print('1. a floor plan supplied as a PDF is drawn on page four')


# ─── 2. Its contents survive — this is the point of a floor plan ────────────
src = None
for info in plan_page.get_images(full=True):
    if plan_page.get_image_bbox(info) == drawn[0]:
        src = doc.extract_image(info[0])
assert src, 'the drawn plan has no embedded image behind it'
embedded = Image.open(io.BytesIO(src['image'])).convert('L')
extremes = embedded.getextrema()
assert extremes[1] - extremes[0] > 40, \
    f'the embedded plan is nearly one flat tone {extremes} — a placeholder'
dark = sum(1 for p in embedded.getdata() if p < 128)
assert dark > 200, f'only {dark} dark pixels — the plan drew nothing'
print('2. the plan has real content in it, not a flat fill')


# ─── 3. No grey placeholder anywhere in the document ────────────────────────
grey = tuple(round(c * 255) for c in (pp.PANEL_GREY.red, pp.PANEL_GREY.green,
                                      pp.PANEL_GREY.blue))


def grey_boxes(page):
    out = []
    for shape in page.get_drawings():
        fill = shape.get('fill')
        if not fill or shape['rect'].width < 200 or shape['rect'].height < 150:
            continue
        if tuple(round(c * 255) for c in fill) == grey:
            out.append(shape['rect'])
    return out


for ids, label in ((PDF, 'PDF plan'), (PNG, 'image plan')):
    d = pymupdf.open(stream=make(ids, 4), filetype='pdf')
    for n in (3, 4):
        boxes = grey_boxes(d[n - 1])
        assert not boxes, \
            f'{label}: a grey placeholder is on page {n} at {boxes[0]}'
print('3. no grey placeholder on the photographs or floor plan pages')


# ─── 4. An image floor plan still works ─────────────────────────────────────
d = pymupdf.open(stream=make(PNG, 4), filetype='pdf')
assert content_images(d[3]), 'a PNG floor plan stopped working'
print('4. a floor plan supplied as an image still works')


# ─── 5. Proportions are kept ────────────────────────────────────────────────
d = pymupdf.open(stream=make(PDF, 4), filetype='pdf')
box = content_images(d[3])[0]
assert abs((box.width / box.height) - (842 / 595)) < 0.05, \
    f'the plan was stretched: drawn at {box.width:.0f}x{box.height:.0f}'
print('5. the plan keeps its proportions and is not stretched')


# ─── 6. The file is small enough to email ───────────────────────────────────
sizes = {}
for pages in (2, 4):
    raw = make(PDF, pages)
    sizes[pages] = len(raw)
    mb = len(raw) / 1_000_000
    assert mb < 5, f'the {pages}-page brochure is {mb:.1f} MB — too big to email'
print(f'6. {sizes[2]/1_000_000:.2f} MB for two pages and '
      f'{sizes[4]/1_000_000:.2f} MB for four — both email-friendly')


# ─── 7. Nothing is embedded at full resolution ──────────────────────────────
original = max(len(p) for p in PHOTOS)
assert original > 3_000_000, 'the test photographs are not large enough to prove this'
d = pymupdf.open(stream=make(PDF, 4), filetype='pdf')
biggest = 0
for page in d:
    for info in page.get_images(full=True):
        src = d.extract_image(info[0])
        biggest = max(biggest, len(src['image']))
assert biggest < original / 4, \
    f'an image is embedded at {biggest/1_000_000:.1f} MB against a ' \
    f'{original/1_000_000:.1f} MB original — it is not being resized'
print(f'7. the largest embedded image is {biggest/1_000_000:.2f} MB, from a '
      f'{original/1_000_000:.1f} MB original')


# ─── 8. A repeated photograph is stored once ────────────────────────────────
# The same file appears several times in these fixtures; it must be embedded
# once and referred to, not copied per placement.
d = pymupdf.open(stream=make(PDF, 4), filetype='pdf')
placements = sum(len(p.get_images(full=True)) for p in d)
distinct = len({info[0] for p in d for info in p.get_images(full=True)})
assert distinct < placements, \
    f'{placements} placements and {distinct} objects — every one is its own copy'
digests = set()
for page in d:
    for info in page.get_images(full=True):
        digests.add(hashlib.sha1(d.extract_image(info[0])['image']).hexdigest())
assert len(digests) == distinct, 'the same bytes are stored under two objects'
print(f'8. {placements} placements share {distinct} embedded objects — '
      'the mark and repeated photographs are stored once')


# ─── 9. Photographs are still sharp enough ──────────────────────────────────
# A full-bleed cover is 842pt wide. At 150 dpi that is about 1750 pixels;
# anything much under a thousand would show.
d = pymupdf.open(stream=make(PDF, 4), filetype='pdf')
cover = max(content_images(d[0]), key=lambda b: b.width * b.height)
src = None
for info in d[0].get_images(full=True):
    if d[0].get_image_bbox(info) == cover:
        src = d.extract_image(info[0])
assert src, 'could not find the cover photograph'
assert src['width'] >= 1000, \
    f"the cover photograph is only {src['width']}px wide — too soft"
print(f"9. the cover photograph is embedded at {src['width']}x{src['height']}px "
      '— sharp on screen and in print')


# ─── 10. The layout has not changed ─────────────────────────────────────────
d = pymupdf.open(stream=make(PDF, 4), filetype='pdf')
whole = ' '.join(' '.join(p.get_text().split()) for p in d)
for expected in ('PDFPLAN', 'Location', 'Description',
                 'Key Terms', 'Business Rates', 'Service Charge', 'EPC', 'Rent',
                 'FURTHER PHOTOGRAPHS', 'FLOORPLAN', 'Misrepresentation Act 1967',
                 'Benjamin Cowan'):
    assert expected in whole, f'{expected!r} is missing — the layout changed'
assert d.page_count == 4
two = pymupdf.open(stream=make(PDF, 2), filetype='pdf')
assert two.page_count == 2
assert ' '.join(two[0].get_text().split()) == ' '.join(d[0].get_text().split()), \
    'page one no longer matches between the two formats'
print('10. every section is still present and the two formats still share page one')


# ─── 11. A property with no floor plan behaves as before ────────────────────
raw = make(NONE, 4, no_floorplan_ok='1')
d = pymupdf.open(stream=raw, filetype='pdf')
assert 'No floorplan has been uploaded' in ' '.join(d[3].get_text().split())
assert d.page_count == 4
print('11. a property with no plan still says so rather than showing a box')


# ─── 12. The preview and the download are the same document ─────────────────
prev = make(PDF, 4, url='preview')
dl = make(PDF, 4, url='download')
a = pymupdf.open(stream=prev, filetype='pdf')
b = pymupdf.open(stream=dl, filetype='pdf')
assert a.page_count == b.page_count
for i in range(a.page_count):
    assert ' '.join(a[i].get_text().split()) == ' '.join(b[i].get_text().split()), \
        f'preview and download differ on page {i + 1}'
assert len(content_images(a[3])) == len(content_images(b[3])), \
    'the floor plan differs between preview and download'
print('12. the preview and the downloaded file are the same document')

print('\nBROCHURE IMAGES: ALL CHECKS PASSED')
