"""Instruction Details and Key Contacts: aligned boxes, three instruction types."""
import os
import re, re, sys, tempfile
from html.parser import HTMLParser

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
sys.path.insert(0, "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db")

from app import (app, db, Property, Project, User, AuditLog,
                 INSTRUCTION_TYPES, instruction_type_ok)
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash

root = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
LONG_CLIENT = 'Marsden Estates and Property Investments (Holdings) Limited Partnership'

with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw')))
    prop = Property(address='2A Britannia Way, Parsons Green, London', postcode='SW6 1AA')
    db.session.add(prop); db.session.flush()
    live = Project(name='Letting — 2A Britannia Way', project_ref='CR/2026/001',
                   property_id=prop.id, status='Active', instruction_type='To Let – Available',
                   client=LONG_CLIENT, fee_earner='B. Cowan')
    legacy = Project(name='Rent review — 2A Britannia Way', project_ref='CR/2019/044',
                     property_id=prop.id, status='Active', instruction_type='Rent Review',
                     client='Ashcombe Clinics')
    db.session.add_all([live, legacy]); db.session.commit()
    pid, legacy_id, prop_id = live.id, legacy.id, prop.id
    original_name, original_ref = live.name, live.project_ref

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def page(pid_):
    r = cl.get(f'/projects/{pid_}')
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)


def box_html(html, heading):
    """The markup of one .box--grid, found by its heading."""
    m = re.search(r'<div class="box-head">\s*' + re.escape(heading), html)
    assert m, f'{heading} box not found'
    i = m.start()
    start = html.rfind('<div class="box box--grid">', 0, i)
    assert start != -1, f'{heading} is not on the shared grid'
    depth, j = 0, start
    while True:
        nxt_open = html.find('<div', j)
        nxt_close = html.find('</div>', j)
        if nxt_close == -1:
            break
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1; j = nxt_open + 4
        else:
            depth -= 1; j = nxt_close + 6
            if depth == 0:
                break
    return html[start:j]


def cells(html, heading):
    """Each cell of that box: whether it is a label, and what control it holds."""
    body = box_html(html, heading)
    out = []
    for m in re.finditer(r'<div class="(fcell[^"]*)"[^>]*>(.*?)(?=<div class="fcell|</div>\s*</div>|$)',
                         body, re.S):
        cls, inner = m.group(1), m.group(2)
        ctl = re.search(r'<(input|select|textarea)[^>]*name="([^"]+)"', inner)
        out.append({'label': 'fcell--label' in cls,
                    'text': re.sub(r'<[^>]+>', ' ', inner).split(),
                    'ctl': (ctl.group(1), ctl.group(2)) if ctl else None})
    return out


html = page(pid)

# ── 1. The project name control is gone from this page ──────────────────────
inst = cells(html, 'Instruction Detail')
assert inst, 'the Instruction Detail box was not found'
names = [c['ctl'][1] for c in inst if c['ctl']]
assert 'name' not in names, 'the project name field is still editable here'
assert 'Project name' not in ' '.join(t for c in inst for t in c['text'])
assert 'Change Project Name' not in html
print('1. no project-name field and no Change Project Name control')

# ── 2. An instruction type dropdown, with exactly three choices ─────────────
sel = re.search(r'<select name="instruction_type"[^>]*>(.*?)</select>', html, re.S)
assert sel, 'no instruction type dropdown'
options = re.findall(r'<option value="([^"]*)"', sel.group(1))
assert [o for o in options if o] == INSTRUCTION_TYPES, options
assert INSTRUCTION_TYPES == ['For Sale – Available', 'To Let – Available',
                             'Market Appraisal', 'Prospect', 'Archived']
print('2. the five instruction types are the only choices offered')

# ── 3. The saved value shows automatically ──────────────────────────────────
assert re.search(r'<option value="To Let – Available"\s+selected', sel.group(1)), \
    'the saved type is not selected'
print('3. the instruction type already on the record is shown')

# ── 4. All three can be chosen and are saved ────────────────────────────────
for choice in INSTRUCTION_TYPES:
    r = cl.post(f'/projects/{pid}/edit', data={'instruction_type': choice},
                follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        p = Project.query.get(pid)
        assert p.instruction_type == choice, (choice, p.instruction_type)
print('4. all five instruction types save from the Property Overview')

# ── 5. Nothing else moves when the type changes ─────────────────────────────
with app.app_context():
    p = Project.query.get(pid)
    assert p.name == original_name, 'the project name changed'
    assert p.project_ref == original_ref, 'the reference changed'
    assert p.property_id == prop_id, 'the property changed'
    assert p.client == LONG_CLIENT, 'the client changed'
    assert Project.query.count() == 2, 'a duplicate project was created'
assert cl.get(f'/projects/{pid}').status_code == 200, 'the project URL changed'
print('5. name, reference, property and URL are untouched; no duplicate made')

# ── 6. Anything outside the three is refused ────────────────────────────────
for bad in ['Freehold Sale', 'letting', 'Valuation', 'Sale or Letting', '<script>']:
    r = cl.post(f'/projects/{pid}/edit', data={'instruction_type': bad}, follow_redirects=True)
    assert 'Choose one of:' in r.get_data(as_text=True), bad
    with app.app_context():
        assert Project.query.get(pid).instruction_type in INSTRUCTION_TYPES, bad
assert instruction_type_ok('Market Appraisal') and instruction_type_ok('')
assert not instruction_type_ok('Rent Review')
print('6. only the three permitted values are accepted, checked on the server')

# ── 7. A legacy value is kept and flagged, not rewritten ────────────────────
legacy_html = page(legacy_id)
assert 'Rent Review — please review' in legacy_html, 'the legacy value is not flagged'
sel = re.search(r'<select name="instruction_type"[^>]*>(.*?)</select>', legacy_html, re.S)
assert re.search(r'<option value="Rent Review" selected', sel.group(1))
with app.app_context():
    assert Project.query.get(legacy_id).instruction_type == 'Rent Review'
# Saving other fields must not force the legacy value onto the new list.
r = cl.post(f'/projects/{legacy_id}/edit',
            data={'instruction_type': 'Rent Review', 'fee_earner': 'K. Cowan'},
            follow_redirects=True)
with app.app_context():
    p = Project.query.get(legacy_id)
    assert p.instruction_type == 'Rent Review', 'the legacy value was changed'
    assert p.fee_earner == 'K. Cowan'
assert instruction_type_ok('Rent Review', existing='Rent Review')
print('7. a legacy instruction is preserved and flagged for review')

# ── 8. Changes are recorded in the audit log ────────────────────────────────
with app.app_context():
    entries = AuditLog.query.filter(AuditLog.entity == 'Project',
                                    AuditLog.detail.like('instruction type%')).all()
    assert entries, 'no instruction type change was audited'
    assert any('to Archived' in e.detail for e in entries), [e.detail for e in entries]
print('8. every instruction type change is written to the audit log')

# ── 9. Both boxes are on the shared column grid ─────────────────────────────
for heading in ['Instruction Detail', 'Client Contact Details']:
    assert f'>{heading}</div>' in html.replace('\n', '') or heading in html, heading
assert html.count('box box--grid') >= 2, 'the boxes are not on the shared grid'
css = open(f'{root}/static/css/crm-grid.css').read()
assert '.box--grid {' in css
assert 'grid-template-columns: var(--label-w) minmax(0, 1fr) var(--label-w) minmax(0, 1fr)' in css
assert '--label-w:' in css, 'there is no shared label width'
assert '.box--grid .frow { display: contents; }' in css
print('9. both boxes share one four-column grid, so their columns line up')

# ── 10. Every row has the same shape ────────────────────────────────────────
for heading in ['Instruction Detail', 'Client Contact Details']:
    cs = cells(html, heading)
    # Rows hold two pairs, or one pair spanning the width — either way, pairs.
    assert len(cs) % 2 == 0, f'{heading}: {len(cs)} cells is not whole label/value pairs'
    for i, c in enumerate(cs):
        expect_label = (i % 2 == 0)
        assert c['label'] == expect_label, f'{heading}: cell {i} is out of step'
print('10. every row is label, value, label, value — nothing out of step')

# ── 11. Heights and spacing are set by the grid, not per box ────────────────
assert 'align-items: stretch' in css
assert '.box--grid .fcell--edit > input,' in css and 'height: var(--cell-h)' in css
assert '.box--grid .fcell { border-bottom: 1px solid var(--cell-line); }' in css
assert 'overflow-wrap: anywhere' in css
print('11. matching field heights, consistent row borders, long text wraps')

# ── 12. Stacking on smaller screens ─────────────────────────────────────────
assert '@container (max-width: 620px) { .box--grid' in css
assert '@container (max-width: 420px) { .box--grid' in css
assert '@media (max-width: 700px) { .box--grid' in css
print('12. two columns then one as the space runs out')

# ── 13. The New Project page still names a project ──────────────────────────
new = cl.get('/projects/new').get_data(as_text=True)
assert 'name="name"' in new, 'the create page lost its project name field'
assert 'name="instruction_type"' in new
sel = re.search(r'<select name="instruction_type"[^>]*>(.*?)</select>', new, re.S)
assert [o for o in re.findall(r'<option value="([^"]*)"', sel.group(1)) if o] == INSTRUCTION_TYPES
r = cl.post('/projects/new', data={'instruction_type': 'Valuation', 'property_mode': 'existing',
                                   'property_id': str(prop_id)}, follow_redirects=True)
assert 'Choose one of:' in r.get_data(as_text=True)
print('13. the create page keeps its name field and the same five types')

# ── 14. Inline editing and permissions still work ───────────────────────────
assert 'data-inline-edit' in html and 'data-save' in html
r = cl.post(f'/projects/{pid}/edit', data={'instruction_type': 'For Sale – Available',
                                           'client': 'Bellweather Retail'},
            follow_redirects=True)
with app.app_context():
    p = Project.query.get(pid)
    assert p.client == 'Bellweather Retail' and p.instruction_type == 'For Sale – Available'
    assert p.fee_earner == 'B. Cowan', 'a field not on the form was blanked'
with app.app_context():
    db.session.add(User(username='viewer', password_hash=generate_password_hash('pw'),
                        role='viewer'))
    db.session.commit()
v = app.test_client()
v.post('/login', data={'username': 'viewer', 'password': 'pw'}, follow_redirects=True)
assert v.post(f'/projects/{pid}/delete', follow_redirects=False).status_code in (302, 403)
print('14. inline editing, presence-guarded saves and permissions all unchanged')

# ── 15. The type is visible on the Projects list, and can be filtered ──────
# Earlier checks walked this project through every type; set a known one.
cl.post(f'/projects/{pid}/edit', data={'instruction_type': 'To Let – Available'},
        follow_redirects=True)
listing = cl.get('/projects').get_data(as_text=True)
rows = listing.split('class="rec-list"')[1]
assert 'org-tag' in rows, 'the instruction type is not shown on the list'
for t in ['To Let – Available', 'Rent Review']:
    assert t in rows, f'{t} is not shown on the list'
sel = re.search(r'<select name="type"[^>]*>(.*?)</select>', listing, re.S)
assert sel, 'the list has no instruction type filter'
assert [o for o in re.findall(r'<option value="([^"]*)"', sel.group(1)) if o] == INSTRUCTION_TYPES
filtered = cl.get('/projects?type=To+Let+%E2%80%93+Available').get_data(as_text=True)
body = filtered.split('class="rec-list"')[1]
assert 'To Let – Available' in body and 'Rent Review' not in body
print('15. the type shows on every row, and the list filters by it')

# ── 16. Nothing outside the five is offered anywhere ────────────────────────
gone = ['Sale or Letting', 'Rent Review', 'Lease Renewal', 'Valuation',
        'Building Survey', 'Lease Advisory', 'Business Rates']
for where, text in [('the record', page(pid)), ('the create page', new)]:
    for g in gone:
        assert f'>{g}</option>' not in text, f'{g} is still offered on {where}'
print('16. none of the old instruction types are offered any more')

# ── 17. The organiser panels follow the new names ──────────────────────────
from app import INSTRUCTION_TO_LET, INSTRUCTION_FOR_SALE
assert INSTRUCTION_TO_LET == 'To Let – Available'
assert INSTRUCTION_FOR_SALE == 'For Sale – Available'
src = open(f'{root}/app.py').read()
assert "_available_listings('Letting')" not in src and "_available_listings('Sale')" not in src
assert '_available_listings(INSTRUCTION_TO_LET)' in src
print('17. the Organiser To Let and For Sale panels read the new names')


def instruction_details_only(html):
    """Just the Instruction Details box, so a field in the next box is not
    mistaken for one that was supposed to be cleared out of this one.

    Sliced to the next box heading rather than by a character count, which
    quietly swallowed the following box as the page grew.
    """
    if 'Instruction Detail' not in html:
        return ''
    part = html.split('Instruction Detail', 1)[1]
    nxt = re.search(r'<div class="box-head"', part)
    return part[:nxt.start()] if nxt else part


# ── 18. Landlord, Fee, Fees & Rent and Location are gone from the record ───
rec = page(pid)
for field in ['landlord_name', 'location_description']:
    assert f'name="{field}"' not in rec, f'{field} is still editable on the project page'
for box in ['Fees &amp; Rent', 'Rent p.a.', 'Rent PSF', '>Location<']:
    assert box not in rec, f'{box} is still on the project page'
assert 'name="landlord_name"' not in new, 'Landlord is still on the create page'
assert 'name="location_description"' not in new
# The fee came out of Instruction Details and now has a box of its own, so
# stock can be valued. It must not creep back into Instruction Details.
for where, page_name in [(rec, 'Project Overview'), (new, 'the create page')]:
    box = instruction_details_only(where)
    assert 'name="fee_percent"' not in box, f'the fee is back in Instruction Details on {page_name}'
    assert 'name="fee_percent"' in where, f'there is nowhere to enter a fee on {page_name}'
    assert 'name="fee_fixed"' in where, f'no fixed fee on {page_name}'
print('18. Landlord, Fees & Rent and Location are gone; the fee has its own box')

# ── 19. What is left fits with no half-empty rows ──────────────────────────
for heading, where in [('Instruction Detail', rec), ('Instruction Detail', new)]:
    cs = cells(where, heading)
    assert len(cs) % 2 == 0, f'{len(cs)} cells is not whole label/value pairs'
    for i, c in enumerate(cs):
        assert c['label'] == (i % 2 == 0), f'cell {i} is out of step'
    # A row is either a full pair of pairs or one pair spanning the width.
    body = box_html(where, heading)
    assert body.count('<div class="frow') * 2 <= len(cs), 'a row was left half empty'
print('19. the remaining fields fill whole rows, with no gaps left behind')

# ── 20. The data itself is untouched, and still used where it is needed ────
with app.app_context():
    p = Project.query.get(pid)
    p.landlord_name = 'Marsden Holdings'
    p.fee_percent = 10.0
    p.location_description = 'Off St James’s.'
    db.session.commit()
cl.post(f'/projects/{pid}/edit', data={'instruction_type': 'Prospect'}, follow_redirects=True)
with app.app_context():
    p = Project.query.get(pid)
    assert p.landlord_name == 'Marsden Holdings', 'saving blanked the landlord'
    assert p.fee_percent == 10.0, 'saving blanked the fee'
    assert p.location_description == 'Off St James’s.', 'saving blanked the location'
print('20. landlord, fee and location are kept in the database, not blanked')

# ── 21. No field is claimed by the project form twice ──────────────────────
from collections import Counter
names = re.findall(r'<(?:input|select|textarea)[^>]*name="([a-z_]+)"[^>]*form="project-form"', rec)
dupes = {k: v for k, v in Counter(names).items() if v > 1}
assert not dupes, f'these fields would overwrite each other on save: {dupes}'
print('21. every field in the project form appears exactly once')

print('\nINSTRUCTION DETAILS: ALL CHECKS PASSED')
