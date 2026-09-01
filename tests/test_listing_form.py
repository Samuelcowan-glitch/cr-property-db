"""Website Listing built from the same components as the rest of the project form."""
import os, re, sys, tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
sys.path.insert(0, "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db")

from app import app, db, Property, Project, Listing, User, INSTRUCTION_TO_LET
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash

root = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"

with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw')))
    prop = Property(address='2A Britannia Way', postcode='SW6 1AA')
    db.session.add(prop); db.session.flush()
    pj = Project(name='Letting — 2A Britannia Way', property_id=prop.id, status='Active',
                 instruction_type=INSTRUCTION_TO_LET)
    db.session.add(pj); db.session.flush()
    lst = Listing(project_id=pj.id, property_id=prop.id, listing_status='available')
    db.session.add(lst); db.session.commit()
    pj_id, lid = pj.id, lst.id

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def page():
    r = cl.get(f'/projects/{pj_id}')
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)


def listing_form(html):
    import re
    at = html.index('class="listing-edit-form"')
    start = html.rindex('<div', 0, at)
    depth, end = 0, None
    for m in re.finditer(r'<div\b|</div>', html[start:]):
        depth += 1 if m.group(0).startswith('<div') else -1
        if depth == 0:
            end = start + m.end()
            break
    assert end, 'the listing section is not closed'
    return html[start:end]


html = page()
form = listing_form(html)

# ── 1. Built from the shared components, not its own markup ────────────────
assert 'box box--grid' in form, 'the listing sections are not on the shared grid'
assert form.count('class="frow') >= 25, 'the fields are not laid out as rows'
assert 'fcell fcell--label' in form and 'fcell fcell--edit' in form
print('1. the listing uses the same boxes, rows and cells as the rest of the form')

# ── 2. No hand-rolled field styling left ───────────────────────────────────
for stale in ['border:1px solid #dde0e4', 'text-transform:uppercase;letter-spacing:.4px',
              'font-size:10.5px;color:#5f6368', 'grid-template-columns:repeat(auto-fit',
              'background:#0e1f44;color:#fff;padding:8px 14px']:
    assert stale not in form, f'inconsistent styling remains: {stale}'
# Field labels are cells now; the only labels left are tick-boxes, and those
# carry a class rather than inline styling.
assert not re.search(r'<label style="font-size:1[03]', form), 'a hand-styled label remains'
assert 'class="pub-check"' in form and 'class="na-check"' in form
print('2. no hand-rolled borders, fonts, labels or section bars remain')

# ── 3. Section headings match the rest of the page ─────────────────────────
heads = re.findall(r'<div class="box-head">([^<]+)</div>', form)
assert len(heads) >= 6, f'only {len(heads)} section headings'
for h in ['Listing Details', 'Space & Measurement', 'Pricing', 'Marketing']:
    assert any(h in x for x in heads), f'{h} is not a box heading'
# The same heading element the Instruction Detail box uses.
assert '<div class="box-head">Instruction Detail</div>' in html
print('3. every section is a box heading in the same style as the rest')

# ── 4. Every field survived the rebuild ────────────────────────────────────
# _csrf is added to every form by the server, so it is not part of the
# template's own field list.
names = sorted(set(re.findall(r'<(?:input|select|textarea)[^>]*name="([a-z_0-9]+)"', form))
               - {'_csrf'})
EXPECTED = {
    'area', 'baths', 'beds', 'blurb', 'epc_band', 'featured', 'initial_yield',
    'inside_1954_act', 'investment_vacant', 'key_terms', 'lease_length_months',
    'lease_length_years', 'lease_type', 'lease_years_remaining',
    'listing_price', 'listing_price_unit', 'listing_status',
    'location_description', 'max_size', 'measurement_type', 'min_size',
    'parking_ratio', 'parking_spaces', 'photo_id', 'price_display',
    'rateable_value', 'rateable_value_na', 'rent_from', 'rent_inclusive',
    'rent_qualifier', 'rent_to', 'repair_insuring', 'service_charge',
    'service_charge_na', 'size', 'strapline', 'tenure', 'transaction',
    'unit_name', 'use_class', 'website_category', 'website_listed',
    'zoopla_listed',
}
lost = EXPECTED - set(names)
gained = set(names) - EXPECTED
assert not lost, f'fields were lost: {sorted(lost)}'
assert not gained, f'unexpected fields appeared: {sorted(gained)}'
print(f'4. all {len(names)} listing fields are still on the form')

# ── 5. Two fields to a row, so the form is compact ─────────────────────────
rows = re.findall(r'<div class="frow(?: [^"]*)?">(.*?)(?=<div class="frow|<div class="box|\Z)',
                  form, re.S)
paired = [r for r in rows if len(re.findall(r'<div class="fcell', r)) == 2]
assert len(paired) >= 20, f'only {len(paired)} rows hold a label and a value'
_css = open(f'{root}/static/css/crm-grid.css').read()
assert 'grid-template-columns: var(--label-w) minmax(0, 1fr) var(--label-w) minmax(0, 1fr)' \
    in _css, 'the grid is not two pairs across'
assert '--label-w:' in _css, 'the label width is not a shared token'
print('5. fields sit two to a row on the shared four-column grid')

# ── 6. Labels are short enough to stay on one line ─────────────────────────
labels = re.findall(r'<div class="fcell fcell--label[^"]*">([^<]*)</div>', form)
long = [l.strip() for l in labels if len(l.strip()) > 24]
assert not long, f'these labels will wrap in a 118px column: {long}'
print('6. no label is too long for its column')

# ── 7. The category toggles still have something to toggle ─────────────────
for cls in ['lst-com', 'lst-res', 'lst-sale', 'lst-let-com']:
    assert re.search(r'class="(?:frow|box box--grid lst-box)[^"]*\b' + cls + r'\b', form), \
        f'{cls} is no longer on any row or box, so the toggle does nothing'
# Hiding a row works because the row is display:contents until told otherwise.
assert '.box--grid .frow { display: contents; }' in open(f'{root}/static/css/crm-grid.css').read()
print('7. the commercial / residential / sale toggles still have rows to act on')

# ── 8. The listing still saves ─────────────────────────────────────────────
r = cl.post(f'/listings/{lid}/edit', data={
    'unit_name': 'Ground Floor', 'website_category': 'commercial', 'use_class': 'office',
    'listing_status': 'available', 'size': '1450', 'measurement_type': 'NIA',
    'listing_price': '32000', 'price_display': 'OIRO £32,000',
    'area': 'Parsons Green', 'blurb': 'A bright corner unit.',
    'key_terms': 'New FRI lease', 'location_description': 'Moments from the Green.',
    'epc_rating': 'B', 'parking_spaces': '4', 'website_listed': '1',
}, follow_redirects=True)
assert r.status_code == 200
with app.app_context():
    l = Listing.query.get(lid)
    assert l.unit_name == 'Ground Floor' and l.use_class == 'office'
    assert l.size == 1450 and l.measurement_type == 'NIA'
    assert l.listing_price == 32000 and l.price_display == 'OIRO £32,000'
    assert l.area == 'Parsons Green' and l.blurb == 'A bright corner unit.'
    assert l.key_terms == 'New FRI lease'
    assert l.location_description == 'Moments from the Green.'
    assert l.website_listed is True
print('8. every kind of field on the listing still saves')

# ── 9. The values come back on the page ───────────────────────────────────
form = listing_form(page())
for shown in ['Ground Floor', '1450', '32000', 'OIRO £32,000', 'Parsons Green',
              'A bright corner unit.', 'New FRI lease', 'Moments from the Green.']:
    assert shown in form, f'{shown} is not shown after saving'
print('9. saved values are displayed again when the page is reopened')

# ── 10. A save here does not disturb the project ──────────────────────────
with app.app_context():
    p = Project.query.get(pj_id)
    assert p.name == 'Letting — 2A Britannia Way'
    assert p.instruction_type == INSTRUCTION_TO_LET
    assert p.location_description is None, 'the listing wrote onto the project'
print('10. saving the listing leaves the project alone')

# ── 11. No field is claimed by two forms ─────────────────────────────────
from collections import Counter
whole = page()
proj_fields = re.findall(
    r'<(?:input|select|textarea)[^>]*name="([a-z_0-9]+)"[^>]*form="project-form"', whole)
dupes = {k: v for k, v in Counter(proj_fields).items() if v > 1}
assert not dupes, f'duplicate fields in the project form: {dupes}'
listing_names = re.findall(
    r'<(?:input|select|textarea)[^>]*name="([a-z_0-9]+)"', form)
missing = [n for n in set(listing_names) if n not in proj_fields]
assert not missing, f'these marketing fields are saved by nothing: {sorted(missing)}'
assert not {k: v for k, v in Counter(listing_names).items() if v > 1}, \
    'a marketing field appears twice and would overwrite itself'
print(f'11. all {len(set(listing_names))} marketing fields reach the one Save button')

print('\nWEBSITE LISTING: ALL CHECKS PASSED')
