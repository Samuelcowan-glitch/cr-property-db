"""The Project Save button, Download PDF, and Save to Brochure."""
import io, os, re, sys, tempfile
from html.parser import HTMLParser

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)

import app as A
import pymupdf
from PIL import Image
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db


def shot():
    b = io.BytesIO()
    Image.new('RGB', (1200, 800), (150, 160, 175)).save(b, 'JPEG')
    return b.getvalue()


def plan():
    b = io.BytesIO()
    Image.new('RGB', (1600, 1100), 'white').save(b, 'PNG')
    return b.getvalue()


with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    db.session.add(A.User(username='admin', password_hash=generate_password_hash('pw'),
                          role='admin', full_name='Benjamin Cowan',
                          email='bc@cowanandrutter.co.uk'))
    db.session.add(A.User(username='looker', password_hash=generate_password_hash('pw'),
                          role='viewer', full_name='A Viewer'))
    db.session.commit()
    council = A.Council.query.first()
    prop = A.Property(address='Marlin House, 40 Peterborough Road, London SW6 3BN',
                      postcode='SW6 3BN', property_type='Office', size=1636,
                      council_id=council.id, floor_plan_data=plan(),
                      floor_plan_filename='Plan.png')
    db.session.add(prop); db.session.commit()
    project = A.Project(name='Marlin', property_id=prop.id, fee_earner_id=1,
                        instruction_type=A.INSTRUCTION_TO_LET, status='Active')
    db.session.add(project); db.session.commit()
    listing = A.Listing(project_id=project.id, property_id=prop.id,
                        set_as_to_let=True, listing_price=50000,
                        listing_price_unit='pa', strapline='ORIGINAL STRAPLINE',
                        blurb='Original description.',
                        location_description='Original location.',
                        key_terms='Original one\nOriginal two', service_charge=3.0,
                        epc_band='C')
    db.session.add(listing); db.session.commit()
    for i in range(6):
        db.session.add(A.ListingPhoto(listing_id=listing.id, file_data=shot(),
                                      filename=f'{i}.jpg', file_mime='image/jpeg',
                                      file_size=1, sort_order=i))
    db.session.commit()
    PID, LID, PROPID = project.id, listing.id, prop.id
    PHOTOS = [x.id for x in A.ListingPhoto.query.order_by(A.ListingPhoto.sort_order)]

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def page():
    return cl.get(f'/projects/{PID}').get_data(as_text=True)


def particulars_page():
    return cl.get(f'/projects/{PID}/particulars').get_data(as_text=True)


def listing_now():
    return A.Listing.query.get(LID)


# ─── 1. One Save button, and no duplicate ───────────────────────────────────
body = page()
saves = re.findall(r'<button[^>]*data-save[^>]*>([^<]*)</button>', body)
assert len(saves) == 1, f'{len(saves)} Save buttons: {saves}'
assert 'Save Listing' not in body, 'the duplicate Save Listing button is still there'
assert body.count('Save Changes') <= 1
print('1. one Save button on the page, and the duplicate is gone')


# ─── 2. It is in the header, targeting the record form ──────────────────────
btn = re.search(r'<button[^>]*data-save[^>]*>', body).group(0)
assert 'form="project-form"' in btn, f'the Save button targets nothing: {btn}'
assert 'btn-primary' in btn and 'btn-save' in btn, 'not the primary button style'
header = body[:body.index('data-save')]
assert header.rindex('rec-toolbar') > header.rindex('rec-cols') if 'rec-cols' in header \
    else True
print('2. it sits in the record toolbar and targets the record form')


# ─── 3. Every editable section is attached to that one form ─────────────────
attached = set(re.findall(r'name="([^"]+)"[^>]*form="project-form"', body))
attached |= set(re.findall(r'form="project-form"[^>]*name="([^"]+)"', body))
for field in ('status', 'instruction_type', 'client_contact_id', 'fee_earner_id',
              'strapline', 'blurb', 'location_description', 'key_terms',
              'listing_price', 'service_charge', 'property_id'):
    assert field in attached, f'{field!r} is not attached to the Save button'
print(f'3. all {len(attached)} editable fields post to the one Save button')


# ─── 4. One Save writes every section ───────────────────────────────────────
cl.post(f'/projects/{PID}/edit', data={
    'status': 'On Hold', 'instruction_type': A.INSTRUCTION_FOR_SALE,
    'strapline': 'EDITED | FOR SALE | SW6',
    'key_terms': 'Edited one\nEdited two\nEdited three',
    'blurb': 'Edited description.', 'location_description': 'Edited location.',
    'listing_price': '61000', 'listing_price_unit': 'pa',
    'service_charge': '4.5'}, follow_redirects=True)
with A.app.app_context():
    p, l = A.Project.query.get(PID), listing_now()
    assert p.status == 'On Hold', p.status
    assert p.instruction_type == A.INSTRUCTION_FOR_SALE
    assert l.strapline == 'EDITED | FOR SALE | SW6', l.strapline
    assert l.key_terms == 'Edited one\nEdited two\nEdited three', repr(l.key_terms)
    assert l.blurb == 'Edited description.'
    assert l.location_description == 'Edited location.'
    assert l.listing_price == 61000 and l.service_charge == 4.5
print('4. one Save writes the instruction AND every marketing field')


# ─── 5. A form without those fields still leaves them alone ─────────────────
cl.post(f'/projects/{PID}/edit', data={'status': 'Active'}, follow_redirects=True)
with A.app.app_context():
    l = listing_now()
    assert l.strapline == 'EDITED | FOR SALE | SW6', 'a partial save blanked the strapline'
    assert l.key_terms and l.blurb, 'a partial save blanked the marketing fields'
    assert A.Project.query.get(PID).status == 'Active'
print('5. a form that does not carry the marketing fields leaves them untouched')


# ─── 6. Invalid values are refused and nothing is lost ──────────────────────
cl.post(f'/projects/{PID}/edit', data={
    'instruction_type': 'Not A Real Type', 'strapline': 'SHOULD NOT SAVE'},
    follow_redirects=True)
with A.app.app_context():
    p, l = A.Project.query.get(PID), listing_now()
    assert p.instruction_type == A.INSTRUCTION_FOR_SALE, 'an invalid type was saved'
    assert l.strapline == 'EDITED | FOR SALE | SW6', \
        'a rejected save wrote half the form anyway'
print('6. an invalid value is refused, and nothing else is written')


# ─── 7. There are no nested forms, and the markup closes ────────────────────
assert not re.search(r'<form[^>]*>(?:(?!</form>).)*<form', body, re.S), 'nested forms'
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


for name, html in (('project', page()), ('particulars', particulars_page())):
    p = Balance()
    p.feed(html)
    assert not p.bad, f'{name}: {p.bad[:3]}'
    assert not p.stack, f'{name}: unclosed {p.stack[:3]}'
print('7. no nested forms, and both pages close every tag')


# ─── 8. Permissions ─────────────────────────────────────────────────────────
viewer = A.app.test_client()
viewer.post('/login', data={'username': 'looker', 'password': 'pw'},
            follow_redirects=True)
for path in (f'/projects/{PID}/edit', f'/projects/{PID}/particulars/download',
             f'/projects/{PID}/particulars/save'):
    assert viewer.post(path, data={}).status_code == 403, f'a viewer reached {path}'
print('8. a viewer cannot save the project or produce particulars')


# ─── 9. Download returns a real PDF, not an error page ──────────────────────
for pages in (2, 4):
    r = cl.post(f'/projects/{PID}/particulars/download',
                data={'pages': str(pages), 'photo_ids': PHOTOS})
    body_bytes = r.get_data()
    assert r.status_code == 200, f'{pages}-page download returned {r.status_code}'
    assert r.headers['Content-Type'] == 'application/pdf', r.headers['Content-Type']
    assert body_bytes[:5] == b'%PDF-', f'not a PDF: {body_bytes[:40]!r}'
    assert len(body_bytes) > 5000, f'suspiciously small: {len(body_bytes)} bytes'
    assert b'<html' not in body_bytes[:2000].lower(), 'an HTML error page was served'
    doc = pymupdf.open(stream=body_bytes, filetype='pdf')
    assert doc.page_count == pages, f'{pages}-page file has {doc.page_count} pages'
    for pg in doc:
        pg.get_text()
    disp = r.headers.get('Content-Disposition', '')
    assert 'attachment' in disp, f'not sent as a download: {disp}'
    assert f'{pages} Page' in disp, disp
    assert 'Particulars - ' in disp and '.pdf' in disp
print('9. both formats download as real PDFs with the right page count and name')


# ─── 10. The filename is safe, and the record is not changed ────────────────
with A.app.app_context():
    p = A.Property.query.get(PROPID)
    was = p.address
    p.address = 'Unit 3/4, "The Yard", Kings Rd: SW6\\SW10'
    db.session.commit()
r = cl.post(f'/projects/{PID}/particulars/download',
            data={'pages': '2', 'photo_ids': PHOTOS})
disp = r.headers['Content-Disposition']
name = re.search(r'filename="?([^";]+)', disp).group(1)
for bad in ('/', '\\', ':', '"', '?', '*', '<', '>', '|'):
    assert bad not in name, f'{bad!r} is in the filename: {name}'
assert name.endswith('.pdf')
with A.app.app_context():
    assert A.Property.query.get(PROPID).address.startswith('Unit 3/4'), \
        'sanitising the filename changed the property record'
    A.Property.query.get(PROPID).address = was
    db.session.commit()
print(f'10. the filename is sanitised without touching the record')


# ─── 11. Saving to the brochure really stores it ────────────────────────────
with A.app.app_context():
    assert not listing_now().brochure_filename, 'a brochure already exists'
r = cl.post(f'/projects/{PID}/particulars/save',
            data={'pages': '2', 'photo_ids': PHOTOS}, follow_redirects=True)
assert r.status_code == 200
with A.app.app_context():
    l = listing_now()
    assert l.brochure_filename and l.brochure_filename.endswith('.pdf'), l.brochure_filename
    assert l.brochure_size > 5000 and l.brochure_size == len(l.brochure_data)
    assert l.brochure_data[:5] == b'%PDF-', 'what was stored is not a PDF'
    assert pymupdf.open(stream=l.brochure_data, filetype='pdf').page_count == 2
    first_name = l.brochure_filename
assert 'saved to the brochure' in r.get_data(as_text=True).lower()
print('11. Save to Brochure stores a real PDF against the right instruction')


# ─── 12. The saved brochure can be viewed and downloaded ────────────────────
body = page()
assert first_name in body, 'the saved filename is not shown on the project'
r = cl.get(f'/listings/{LID}/brochure/download')
assert r.status_code == 200, f'the saved brochure cannot be fetched ({r.status_code})'
assert r.get_data()[:5] == b'%PDF-', 'the saved brochure does not come back as a PDF'
assert pymupdf.open(stream=r.get_data(), filetype='pdf').page_count == 2
# View, Download, Replace and Remove must all be reachable.
for endpoint in ('listing_brochure_download', 'listing_brochure_upload',
                 'listing_brochure_delete'):
    assert any(r.endpoint == endpoint for r in A.app.url_map.iter_rules()), \
        f'{endpoint} is missing, so the brochure box cannot offer that action'
assert f'/listings/{LID}/brochure/download' in body, \
    'the project page offers no way to download the saved brochure'
print('12. the saved brochure can be fetched back, and is still a valid PDF')


# ─── 13. An existing brochure is never replaced without being asked ─────────
r = cl.post(f'/projects/{PID}/particulars/save',
            data={'pages': '4', 'photo_ids': PHOTOS}, follow_redirects=True)
assert 'Choose whether to replace' in r.get_data(as_text=True)
with A.app.app_context():
    assert listing_now().brochure_filename == first_name, \
        'the brochure was replaced without being asked'
    assert pymupdf.open(stream=listing_now().brochure_data,
                        filetype='pdf').page_count == 2
print('13. saving over an existing brochure is refused until a choice is made')


# ─── 14. Keep Both keeps both, and says which is current ────────────────────
with A.app.app_context():
    before = A.ProjectDocument.query.filter_by(project_id=PID).count()
r = cl.post(f'/projects/{PID}/particulars/save',
            data={'pages': '4', 'photo_ids': PHOTOS, 'existing': 'keep'},
            follow_redirects=True)
with A.app.app_context():
    l = listing_now()
    assert l.brochure_filename == first_name, 'Keep Both replaced the brochure'
    docs = A.ProjectDocument.query.filter_by(project_id=PID).all()
    assert len(docs) == before + 1, 'the new version was not kept anywhere'
    kept = docs[-1]
    assert kept.file_data[:5] == b'%PDF-' and kept.file_mime == 'application/pdf'
    assert pymupdf.open(stream=kept.file_data, filetype='pdf').page_count == 4
    assert first_name in (kept.notes or ''), \
        'nothing says which version is the current brochure'
text = r.get_data(as_text=True)
assert 'still the brochure' in text and 'Key Documents' in text
print('14. Keep Both keeps both, and names the one that is still current')


# ─── 15. Replace does replace, and is audited ───────────────────────────────
r = cl.post(f'/projects/{PID}/particulars/save',
            data={'pages': '4', 'photo_ids': PHOTOS, 'existing': 'replace'},
            follow_redirects=True)
with A.app.app_context():
    l = listing_now()
    assert pymupdf.open(stream=l.brochure_data, filetype='pdf').page_count == 4, \
        'Replace did not put the new document in place'
    assert l.brochure_size == len(l.brochure_data)
    entry = (A.AuditLog.query.filter(A.AuditLog.detail.like('%brochure%'))
             .order_by(A.AuditLog.id.desc()).first())
    assert entry and 'replacing' in (entry.detail or ''), entry.detail if entry else None
print('15. Replace swaps the brochure and records what it replaced')


# ─── 16. One click makes one brochure ───────────────────────────────────────
with A.app.app_context():
    before = A.ProjectDocument.query.filter_by(project_id=PID).count()
    size_before = listing_now().brochure_size
cl.post(f'/projects/{PID}/particulars/save',
        data={'pages': '2', 'photo_ids': PHOTOS, 'existing': 'replace'},
        follow_redirects=True)
with A.app.app_context():
    assert A.ProjectDocument.query.filter_by(project_id=PID).count() == before, \
        'replacing the brochure also filed a duplicate document'
    assert A.Listing.query.filter_by(project_id=PID).count() == 1
print('16. saving once produces one brochure and no duplicates')


# ─── 17. A bad document is never offered ────────────────────────────────────
try:
    A.validate_particulars(b'', 2)
    raise SystemExit('empty bytes were accepted')
except A.ParticularsError as e:
    assert 'empty' in e.message.lower()
try:
    A.validate_particulars(b'<html>Internal Server Error</html>', 2)
    raise SystemExit('an HTML error page was accepted as a PDF')
except A.ParticularsError as e:
    assert 'not a PDF' in e.message
r = cl.post(f'/projects/{PID}/particulars/download',
            data={'pages': '2', 'photo_ids': PHOTOS})
good = r.get_data()
try:
    A.validate_particulars(good, 4)
    raise SystemExit('a two-page file passed as four pages')
except A.ParticularsError as e:
    assert 'came out with 2 page' in e.message, e.message
assert A.validate_particulars(good, 2) == len(good)
print('17. empty files, HTML error pages and wrong page counts are all refused')


# ─── 18. A failure keeps the work and says what went wrong ──────────────────
import particulars as pp
real = pp.build
pp.build = lambda *a, **kw: b'<html>Something broke</html>'
try:
    r = cl.post(f'/projects/{PID}/particulars/download',
                data={'pages': '2', 'photo_ids': PHOTOS}, follow_redirects=True)
    text = r.get_data(as_text=True)
    assert 'download failed' in text.lower(), text[:300]
    assert 'not a PDF' in text
    assert 'try again' in text.lower(), 'the user is not told they can retry'
    with A.app.app_context():
        still = listing_now()
        assert still.brochure_data and still.brochure_data[:5] == b'%PDF-', \
            'a failed download destroyed the saved brochure'
    r = cl.post(f'/projects/{PID}/particulars/save',
                data={'pages': '2', 'photo_ids': PHOTOS, 'existing': 'replace'},
                follow_redirects=True)
    assert 'Nothing was saved to the brochure' in r.get_data(as_text=True)
    with A.app.app_context():
        assert pymupdf.open(stream=listing_now().brochure_data,
                            filetype='pdf').page_count == 2, \
            'a failed save overwrote the good brochure'
finally:
    pp.build = real
print('18. a failure names itself, keeps the existing brochure, and invites a retry')


# ─── 19. After a failure everything still works ─────────────────────────────
r = cl.post(f'/projects/{PID}/particulars/download',
            data={'pages': '2', 'photo_ids': PHOTOS})
assert r.status_code == 200 and r.get_data()[:5] == b'%PDF-'
print('19. the next attempt after a failure succeeds without regenerating anything')


# ─── 20. The buttons the screen offers ──────────────────────────────────────
body = particulars_page()
for label in ('Preview PDF', 'Download PDF', 'Save to Brochure',
              'Create Another Version', 'Close'):
    assert label in body, f'the {label!r} button is missing'
assert 'Preparing Download' in body, 'the download button has no busy label'
assert 'Saving' in body, 'the save button has no busy label'
assert 'data-pt-confirm="brochure-choice"' in body, 'save does not ask before replacing'
assert 'Replace Existing Brochure' in body and 'Keep Both Versions' in body
assert 'data-pt-cancel' in body, 'the dialog cannot be cancelled'
print('20. every required button is present, with its busy label and confirmation')


# ─── 21. The guard cannot cancel the submission it guards ───────────────────
js = open(f'{ROOT}/static/js/particulars.js').read()
assert 'guardOnce' not in js, 'the old submit-cancelling guard is still there'
guard = js[js.index('function guardActions'):]
guard = guard[:guard.index('\n  }\n')]
click = guard[guard.index("addEventListener('click'"):guard.index("form.addEventListener('submit'")]
assert 'disabled = true' not in click, \
    'the click handler disables the button again, which cancels the submission'
submit = guard[guard.index("form.addEventListener('submit'"):]
assert 'setTimeout' in submit and 'disabled = true' in submit, \
    'the button is no longer disabled once the submission is under way'
assert 'pageshow' in guard, 'a button could stay stuck after a download'
print('21. the guard disables after submitting, never instead of submitting')


# ─── 22. Everything asked of the particulars before is still true ───────────
r = cl.post(f'/projects/{PID}/particulars/download',
            data={'pages': '4', 'photo_ids': PHOTOS, 'no_floorplan_ok': '1'})
doc = pymupdf.open(stream=r.get_data(), filetype='pdf')
whole = re.sub(r'\s+', ' ', ' '.join(p.get_text() for p in doc))
assert 'Key Terms' in whole and 'Edited one' in whole, 'the key terms were lost'
assert 'EDITED' in whole and '|' in whole, 'the strapline or its separators were lost'
assert 'FOR SALE' in whole, 'the instruction wording was lost'
assert 'Business Rates' in whole and 'Service Charge' in whole
assert 'Benjamin Cowan' in whole, 'the fee earner details were lost'
assert '£' not in re.sub(r'\s+', ' ', doc[0].get_text()), 'a price is back on the cover'
assert 'FLOORPLAN' in whole.upper()
two = pymupdf.open(stream=cl.post(f'/projects/{PID}/particulars/download',
                                  data={'pages': '2', 'photo_ids': PHOTOS}).get_data(),
                   filetype='pdf')
assert re.sub(r'\s+', ' ', two[0].get_text()) == re.sub(r'\s+', ' ', doc[0].get_text())
print('22. every earlier particulars requirement still holds')

print('\nSAVE, DOWNLOAD AND BROCHURE: ALL CHECKS PASSED')
