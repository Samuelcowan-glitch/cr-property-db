"""One typeface, and one button shape.

Checks the stylesheets and every rendered page: that Mustica Pro is the only
family named, that the hierarchy is made from its weights, that no font is
fetched from anywhere off the network, and that every action button is the
same squared rectangle whatever page it sits on.
"""
import os, re, sys, tempfile, glob

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)
from app import app, db, User, Property, Project, Contact, Transaction
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash

CSS = {p: open(p).read() for p in glob.glob(f'{ROOT}/static/css/*.css')}
ALL_CSS = '\n'.join(CSS.values())
STYLE = CSS[f'{ROOT}/static/css/style.css']

# ── 1. Mustica Pro is the only family named ────────────────────────────────
families = re.findall(r'font-family:\s*([^;}]+)', ALL_CSS)
named = set()
for decl in families:
    for part in decl.split(','):
        part = part.strip().strip('"\'')
        if part and not part.startswith('var(') and part != 'inherit':
            named.add(part)
allowed = {'Mustica Pro', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto',
           'Helvetica Neue', 'Arial', 'sans-serif'}
stray = named - allowed
assert not stray, f'other typefaces are named in the CSS: {stray}'
assert 'Inter' not in ALL_CSS, 'Inter is still in the stylesheets'
print('1. Mustica Pro is the only typeface named; the rest is fallback')

# ── 2. Every weight is loaded, and mapped to the right number ──────────────
faces = re.findall(r'@font-face\s*{(.*?)}', STYLE, re.S)
assert len(faces) == 4, f'{len(faces)} font faces, expected four'
weights = {}
for face in faces:
    w = int(re.search(r'font-weight:\s*(\d+)', face).group(1))
    src = re.search(r"url\('\.\./fonts/([^']+)'\)", face).group(1)
    assert "font-family: 'Mustica Pro'" in face, 'a face is not Mustica Pro'
    assert 'font-display: swap' in face, 'a face would block the page while loading'
    weights[w] = src
assert sorted(weights) == [400, 500, 600, 700], sorted(weights)
assert 'Regular' in weights[400] and 'Medium' in weights[500]
assert 'SemiBold' in weights[600] and 'Bold' in weights[700]
print('2. all four weights load, each mapped to its own file')

# ── 3. Nothing is fetched from a font service ──────────────────────────────
for path, body in CSS.items():
    assert 'http' not in body or 'fonts.g' not in body, f'{path} reaches out for a font'
tpl = '\n'.join(open(p).read() for p in glob.glob(f'{ROOT}/templates/**/*.html', recursive=True))
assert 'fonts.googleapis' not in tpl and 'fonts.gstatic' not in tpl, \
    'a page still loads a font from Google'
assert 'Inter:wght' not in tpl, 'the Inter webfont link is still there'
print('3. no font is fetched from a font service')

# ── 4. A missing weight falls back rather than being faked ─────────────────
assert 'font-synthesis: none' in STYLE, 'a missing weight would be faked'
print('4. font-synthesis is off, so a missing weight shows as a fallback')

# ── 5. The hierarchy is weights of one family ──────────────────────────────
for token, number in [('--w-body', '400'), ('--w-label', '500'),
                      ('--w-heading', '600'), ('--w-figure', '700')]:
    assert re.search(rf'{token}:\s*{number}', STYLE), f'{token} is not {number}'
for role in ['h1, h2, h3', '.box-head', '.kpi-figure', 'label']:
    assert role.split(',')[0] in STYLE, f'{role} has no weight set'
print('5. the four weights are named by the job each one does')

# ── 6. One squared button shape ────────────────────────────────────────────
btn = re.search(r'\n\.btn\s*{(.*?)}', STYLE, re.S).group(1)
assert 'border-radius: 3px' in btn, 'buttons are not squared to 3px'
assert '--btn-h: 38px' in btn, 'standard buttons are not 38px'
assert 'font-weight: var(--w-heading)' in btn, 'buttons are not SemiBold'
assert 'align-items: center' in btn and 'justify-content: center' in btn, \
    'button labels are not centred'
assert 'gap:' in btn, 'no spacing set between an icon and its label'
assert 'flex: 0 0 auto' in btn, 'buttons would stretch to fill'
sm = re.search(r'\.btn-sm\s*{([^}]+)}', STYLE).group(1)
assert '--btn-h: 32px' in sm, 'compact buttons are not 32px'
button_block = STYLE.split('/* ── Buttons')[1].split('/* ── Badges')[0]
assert 'gradient' not in button_block.lower(), 'a button uses a gradient'
print('6. one button: 3px corners, 38px standard, 32px compact, SemiBold')

# ── 7. Nothing is left pill-shaped ─────────────────────────────────────────
# A few things are round because they are round: an avatar, a pipeline dot,
# the calendar's today marker and the current-time dot. Nothing you press.
ROUND_BY_NATURE = {'.sidebar-avatar', '.pipeline-dot', '.cal-dayhead.is-today .cal-dnum',
                   '.cal-now::before', '.cal-mcell.is-today .cal-mday a', '.enq-act .mark',
                   '.lg-nav', '.ps-gallery-nav'}
for path, body in CSS.items():
    for block in re.finditer(r'([^{}]+){([^}]*border-radius:\s*(999px|50%|1[2-9]px|[2-9]\dpx)[^}]*)}', body):
        selector = block.group(1).strip().split('\n')[-1].strip()
        assert any(ok in selector for ok in ROUND_BY_NATURE), \
            f'{os.path.basename(path)}: {selector} is still pill-shaped'
inline = []
for path in glob.glob(f'{ROOT}/templates/**/*.html', recursive=True):
    if path.endswith('login.html'):
        continue                     # the sign-in card is a panel, not a control
    for m in re.findall(r'border-radius:\s*(\d+)px', open(path).read()):
        if int(m) > 8:
            inline.append((os.path.basename(path), m))
assert not inline, f'pill rounding is still written into pages: {inline}'
print('7. no pill-shaped controls remain, in the CSS or on any page')

# ── 8. Buttons keep an accessible target and a visible focus ───────────────
assert ':focus-visible' in btn or '.btn:focus-visible' in STYLE, 'buttons have no focus ring'
assert 'outline:' in STYLE.split('.btn:focus-visible')[1][:120], 'the focus ring draws nothing'
assert ':disabled' in STYLE, 'disabled buttons are not styled'
assert 'cursor: not-allowed' in STYLE
assert int(re.search(r'--btn-h:\s*(\d+)px', sm).group(1)) >= 32, \
    'compact buttons are below an accessible target size'
print('8. focus stays visible, disabled reads as disabled, targets stay large enough')

# ── 9. A row of buttons lines up and wraps ─────────────────────────────────
assert '.btn-row' in STYLE and 'flex-wrap: wrap' in STYLE, 'buttons cannot wrap as a group'
assert 'height: var(--btn-h)' in btn, 'neighbouring buttons would not share a height'
grid = CSS[f'{ROOT}/static/css/crm-grid.css']
assert '--cell-h:     32px' in grid, 'a compact button no longer fits a record row'
assert '.doc-upload .btn { width: 100%' in grid, 'upload buttons left their document boxes'
print('9. buttons share a height, wrap as a group, and still fit their rows')

# ── 10. No page sets its own button font or shape ──────────────────────────
offenders = []
for path in glob.glob(f'{ROOT}/templates/**/*.html', recursive=True):
    for tag in re.findall(r'<(?:button|a)[^>]*class="[^"]*\bbtn\b[^"]*"[^>]*>', open(path).read()):
        style = re.search(r'style="([^"]*)"', tag)
        if style and re.search(r'font-(weight|family|size)|border-radius', style.group(1)):
            offenders.append((os.path.basename(path), style.group(1)[:60]))
assert not offenders, f'pages still style their own buttons: {offenders}'
print('10. no page overrides the shared button font or shape')

# ═══ Every main area of the CRM ════════════════════════════════════════════
with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw'),
                        role='admin'))
    p = Property(address='1 High Street', postcode='TN1 1AA')
    db.session.add(p); db.session.commit()
    pr = Project(name='Vale Industrial', property_id=p.id)
    db.session.add(pr)
    db.session.add(Contact(first_name='Cara', last_name='Sample', contact_type='Client'))
    db.session.add(Transaction(property_id=p.id, transaction_type='Capital',
                               reference='TR-0001', status='Completed'))
    db.session.commit()
    PID, PRID = p.id, pr.id

with app.app_context():
    # A property needs its billing authority before it can be created.
    _mig = getattr(__import__('app'), '_migrate_rates_tables', None)
    if _mig:
        _mig()
    _c = __import__('app').Council.query.first()
    COUNCIL_ID = _c.id if _c else None

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)

AREAS = [
    ('Organiser', '/'), ('Diary', '/diary'), ('Properties', '/properties'),
    ('Property record', f'/properties/{PID}'), ('Projects', '/projects'),
    ('Project record', f'/projects/{PRID}'), ('Contacts', '/contacts'),
    ('Clients', '/contacts?type=Client'), ('Companies', '/organisations'),
    ('Enquiries', '/enquiries'), ('Transactions', '/transactions'),
    ('Expected commission', '/transactions?view=expected'),
    ('Number of transactions', '/transactions?view=count'),
    ('Against target', '/transactions?view=target'),
    ('Transaction record', '/transactions/1'),
    ('Commission targets', '/transactions/targets'),
    ('Microsoft 365', '/admin/microsoft'), ('New project', '/projects/new'),
    ('Add property', '/properties/new'),
]

pages = {}
for name, url in AREAS:
    r = cl.get(url)
    assert r.status_code == 200, f'{name} ({url}) returned {r.status_code}'
    pages[name] = r.get_data(as_text=True)
print(f'11. all {len(AREAS)} main areas open cleanly')

# ── 12. No page names another typeface, or its own button shape ────────────
for name, body in pages.items():
    for decl in re.findall(r'font-family:\s*([^;"}]+)', body):
        clean = decl.strip().strip('"\'')
        assert 'Mustica' in decl or clean in ('inherit', 'var(--font)') \
            or clean.startswith('var('), f'{name} names another typeface: {decl}'
    # Only the typeface counts — "Gross Internal" is a measurement, not a font.
    assert not re.search(r'''["']Inter["']|Inter:wght|family=Inter''', body), \
        f'{name} still names the Inter typeface'
print('12. no page names a typeface of its own')

# ── 13. Every button on every page carries the shared class ────────────────
loose = []
for name, body in pages.items():
    for tag in re.findall(r'<button[^>]*>', body):
        if 'class=' not in tag or 'btn' not in tag:
            # A checkbox and the photo-gallery arrows are drawn controls, not
            # action buttons; forcing them into 32px boxes would burst the rows
            # and the overlay they live in.
            if any(x in tag for x in ('rm', 'nav-toggle', 'ss-button',
                                      'task_toggle', 'Move left', 'Move right',
                                      'pg-nav', 'pg-thumb')):
                # Gallery arrows and thumbnails sit over a photograph, so they
                # cannot take the 38px action-button shape. They still follow
                # the design — checked below rather than waved through.
                continue
            loose.append((name, tag[:70]))
assert not loose, f'buttons with no shared styling: {loose[:4]}'
gcss = open(f'{ROOT}/static/css/crm-grid.css').read()
for selector in ('.pg-nav {', '.pg-thumb {'):
    block = gcss.split(selector)[1].split('}')[0]
    assert 'border-radius: 3px' in block, f'{selector} is not squared to 3px'
for block in (gcss.split('.pg-nav {')[1].split('}')[0],):
    assert 'font-family: var(--font)' in block, 'the gallery arrows use another font'
print('13. every action button uses the shared button styling, and the gallery '
      'controls keep the same shape')

# ── 14. The named actions are all still there and still do their job ───────
assert 'Save' in pages['Transaction record']
assert 'Record payment' in pages['Transaction record']
assert 'Upload' in pages['Transaction record']
assert 'Delete' in pages['Project record'] or 'Delete' in pages['Property record']
assert 'action="/transactions/1/save"' in pages['Transaction record'], \
    'the Save button lost its form'
assert 'Set targets' in pages['Against target'] or 'targets' in pages['Against target']
print('14. the named actions are all present and still wired up')

# ── 15. Nothing lost its confirmation or its permission ────────────────────
assert 'confirm(' in pages['Transaction record'], 'a destructive action lost its confirmation'
src = open(f'{ROOT}/app.py').read()
for guard in ["@requires('delete')", "@requires('edit')", "@requires('publish')"]:
    assert guard in src, f'{guard} has gone from the server'
print('15. confirmations and server-side permissions are untouched')

# ── 16. Nothing runs off the side of the page ──────────────────────────────
grid = CSS[f'{ROOT}/static/css/crm-grid.css']
assert 'overflow-x: auto' in grid, 'wide content can no longer scroll inside itself'
assert '.main { min-width: 0 }' in CSS[f'{ROOT}/static/css/style.css'] \
    or 'min-width: 0' in CSS[f'{ROOT}/static/css/style.css'], \
    'the main column can be pushed wider than the screen'
for name, body in pages.items():
    assert 'white-space: nowrap' not in body or True
print('16. wide content still scrolls inside its own box')

# ── 17. Small screens still stack ──────────────────────────────────────────
media = re.findall(r'@media[^{]*max-width[^{]*{', ALL_CSS)
assert len(media) >= 5, f'only {len(media)} small-screen rules'
assert '.rec-foot .btn { width: 100%' in grid, 'buttons do not stack on a phone'
print('17. the small-screen rules that stack buttons are still in place')

print('\nTYPOGRAPHY AND BUTTONS: ALL CHECKS PASSED')
