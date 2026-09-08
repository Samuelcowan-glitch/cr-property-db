"""The Cowan & Rutter mark is the same size on every page.

It used to be drawn at 52pt on the cover, 72pt beside the contact details and
22pt in the footer, so four pages of one document looked like three different
ones. This measures the mark as actually rendered on every page of both
formats.
"""
import io
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/logo.db'
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


def shot(colour=(150, 160, 175), w=1400, h=950):
    b = io.BytesIO()
    Image.new('RGB', (w, h), colour).save(b, 'JPEG')
    return b.getvalue()


def plan():
    b = io.BytesIO()
    Image.new('RGB', (1600, 1100), 'white').save(b, 'PNG')
    return b.getvalue()


with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          email='bc@cowanandrutter.co.uk',
                          password_hash=generate_password_hash('pw')))
    db.session.commit()
    council = A.Council.query.first()
    prop = A.Property(address='Marlin House, 40 Peterborough Road, London SW6 3BN',
                      postcode='SW6 3BN', property_type='Office', size=1636,
                      council_id=council.id, floor_plan_data=plan(),
                      floor_plan_filename='Plan.png')
    db.session.add(prop); db.session.commit()
    project = A.Project(name='Marlin', property_id=prop.id, fee_earner_id=1,
                        instruction_type=A.INSTRUCTION_TO_LET)
    db.session.add(project); db.session.commit()
    listing = A.Listing(project_id=project.id, property_id=prop.id,
                        set_as_to_let=True, listing_price=57260,
                        listing_price_unit='pa', epc_band='C',
                        strapline='GROUND FLOOR COMMERCIAL UNIT | FULHAM SW6',
                        blurb='A well presented unit.',
                        location_description='Off the Kings Road.',
                        key_terms='New FRI lease\nAvailable now')
    db.session.add(listing); db.session.commit()
    for i in range(7):
        db.session.add(A.ListingPhoto(listing_id=listing.id, file_data=shot(),
                                      filename=f'{i}.jpg', file_mime='image/jpeg',
                                      file_size=1, sort_order=i))
    db.session.commit()
    PID = project.id
    PHOTOS = [x.id for x in A.ListingPhoto.query.order_by(A.ListingPhoto.sort_order)]

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def render(pages):
    r = cl.post(f'/projects/{PID}/particulars/preview',
                data={'pages': str(pages), 'photo_ids': PHOTOS})
    assert r.status_code == 200, r.status_code
    return pymupdf.open(stream=r.get_data(), filetype='pdf')


from PIL import Image as _Image
with _Image.open(pp.LOGO) as _im:
    LOGO_PIXELS = _im.size          # (1106, 765) — unique to the mark


def logos_on(page):
    """Every drawn instance of the logo file on this page.

    Matched on the source image's pixel dimensions, not its shape: a
    photograph can share the mark's aspect ratio, and one of the test
    photographs does.
    """
    out = []
    for info in page.get_images(full=True):
        xref = info[0]
        try:
            src = page.parent.extract_image(xref)
        except Exception:
            continue
        if (src.get('width'), src.get('height')) != LOGO_PIXELS:
            continue
        box = page.get_image_bbox(info)
        if box.height > 0:
            out.append(box)
    return out


# ─── 1. One constant, used everywhere ───────────────────────────────────────
src = open(os.path.join(ROOT, 'particulars.py')).read()
assert 'LOGO_HEIGHT = ' in src, 'there is no single logo size'
calls = [line for line in src.split('\n') if 'draw_logo(' in line and 'def ' not in line]
for call in calls:
    assert 'height=' not in call or 'LOGO_HEIGHT' in call, \
        f'a call still passes its own size: {call.strip()}'
print(f'1. every one of the {len(calls)} draw_logo calls uses the one size')


# ─── 2. The same height on every page of both formats ───────────────────────
for pages in (2, 4):
    doc = render(pages)
    heights = {}
    for n, page in enumerate(doc, 1):
        found = logos_on(page)
        assert found, f'{pages}-page: no mark on page {n}'
        heights[n] = round(found[0].height, 1)
    sizes = set(heights.values())
    assert len(sizes) == 1, \
        f'{pages}-page particulars draw the mark at {sorted(sizes)} — {heights}'
    only = sizes.pop()
    assert abs(only - pp.LOGO_HEIGHT) < 1.5, \
        f'rendered at {only}pt, expected {pp.LOGO_HEIGHT}pt'
    print(f'2. the {pages}-page particulars draw it at {only}pt on all '
          f'{len(heights)} pages')


# ─── 3. And the same between the two formats ────────────────────────────────
two = round(logos_on(render(2)[0])[0].height, 1)
four = round(logos_on(render(4)[0])[0].height, 1)
assert two == four, f'cover mark differs: {two}pt two-page, {four}pt four-page'
print(f'3. the two-page and four-page covers agree at {two}pt')


# ─── 4. It stays inside the page and clear of the footer rule ───────────────
doc = render(4)
for n, page in enumerate(doc, 1):
    for box in logos_on(page):
        assert box.x0 >= -1 and box.x1 <= page.rect.width + 1, \
            f'the mark runs off the side of page {n}'
        assert box.y0 >= -1 and box.y1 <= page.rect.height + 1, \
            f'the mark runs off page {n}'


# ─── 5. Nothing on the added pages overlaps the deeper footer ───────────────
for n in (3, 4):
    page = doc[n - 1]
    floor = page.rect.height - pp.FOOTER_TOP     # pymupdf counts from the top
    marks = [(round(b.x0), round(b.y0)) for b in logos_on(page)]
    for info in page.get_images(full=True):
        box = page.get_image_bbox(info)
        if (round(box.x0), round(box.y0)) in marks:
            continue
        assert box.y1 <= floor + 1, \
            f'page {n}: a picture runs into the footer band'
print('4. the mark stays on the page, and nothing runs into the footer')

print('\nLOGO SIZE: ALL CHECKS PASSED')
