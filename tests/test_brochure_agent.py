"""Every brochure names one agent, and names him in one order.

The document used to print whoever the instruction was booked to, so two
properties handled by different people gave an enquirer two different people
to ring. The fee earner is who the office credits with the work; it is not the
name that belongs on a marketing document. This books an instruction to
somebody else entirely and checks the brochure still says Benjamin Cowan.
"""
import io
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/agent.db'
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


def shot():
    b = io.BytesIO()
    Image.new('RGB', (1400, 950), (150, 160, 175)).save(b, 'JPEG')
    return b.getvalue()


def plan():
    b = io.BytesIO()
    Image.new('RGB', (1600, 1100), 'white').save(b, 'PNG')
    return b.getvalue()


OTHER = 'Priya Raghunathan'

with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          email='bc@cowanandrutter.co.uk',
                          password_hash=generate_password_hash('pw')))
    db.session.commit()
    # The instruction is booked to somebody who is not the agent on the page.
    other = A.User(username='priya', role='admin', full_name=OTHER,
                   email='priya@cowanandrutter.co.uk',
                   password_hash=generate_password_hash('pw'))
    db.session.add(other); db.session.commit()
    council = A.Council.query.first()
    prop = A.Property(address='Marlin House, 40 Peterborough Road, London SW6 3BN',
                      postcode='SW6 3BN', property_type='Office', size=1636,
                      council_id=council.id, floor_plan_data=plan(),
                      floor_plan_filename='Plan.png')
    db.session.add(prop); db.session.commit()
    project = A.Project(name='Marlin', property_id=prop.id, fee_earner_id=other.id,
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


EXPECTED = ['Benjamin Cowan',
            'M: 07557 380291',
            'T: 020 7349 6666',
            'bc@cowanandrutter.co.uk',
            'www.cowanandrutter.co.uk']


# ─── 1. The same agent, in both formats, whoever holds the instruction ──────
for pages in (2, 4):
    doc = render(pages)
    whole = '\n'.join(page.get_text() for page in doc)
    assert OTHER not in whole, \
        f'{pages}-page: the brochure names the fee earner {OTHER}'
    assert 'priya@cowanandrutter.co.uk' not in whole, \
        f'{pages}-page: the brochure carries the fee earner’s address'
    assert 'Benjamin Cowan' in whole, f'{pages}-page: the agent is not named'
    print(f'1. the {pages}-page brochure names Benjamin Cowan, not the fee earner')


# ─── 2. The lines read in the order asked for ───────────────────────────────
doc = render(4)
page = doc[1]
lines = [ln.strip() for ln in page.get_text().split('\n') if ln.strip()]
found = [ln for ln in lines if ln in EXPECTED]
assert found == EXPECTED, f'the contact lines read {found}'
print('2. name, mobile, telephone, address, website — in that order')


# ─── 3. The mobile is his, and sits directly under the name ─────────────────
i = lines.index('Benjamin Cowan')
assert lines[i + 1] == 'M: 07557 380291', \
    f'the line under the name is {lines[i + 1]!r}'
assert lines[i + 2] == 'T: 020 7349 6666', \
    f'the line under the mobile is {lines[i + 2]!r}'
print('3. the mobile comes under the name and above the telephone number')


# ─── 4. The deeper block still clears the rule over the small print ─────────
H = page.rect.height
words = page.get_text('words')


def word(term):
    hit = [w for w in words if w[4] == term]
    assert hit, f'page two does not say {term!r}'
    return hit[0]


last = H - word('www.cowanandrutter.co.uk')[3]
small_print = H - word('Misrepresentation')[1] + 4
assert last > small_print, \
    f'the last contact line at {last:.0f} sits on the small print at {small_print:.0f}'
print(f'4. the last line clears the small print by {last - small_print:.0f}pt')

print('\nBROCHURE AGENT: ALL CHECKS PASSED')
