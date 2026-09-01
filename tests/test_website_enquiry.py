"""Website enquiries: the whole path from the form to the register."""
import os
import re
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/web.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db

with A.app.app_context():
    db.create_all()

cl = A.app.test_client()


def post(payload, origin='https://cowanandrutter.com'):
    return cl.post('/api/enquiry', json=payload, headers={'Origin': origin})


def count():
    with A.app.app_context():
        return A.Enquiry.query.count()


# ─── 1. The field names match what the website actually sends ───────────────
site = None
for candidate in ('../cowan-rutter-website/js/main.js',
                  os.path.expanduser('~/Documents/Documents - Samuel’s MacBook Air/'
                                     'GitHub/cowan-rutter-website/js/main.js')):
    path = os.path.join(ROOT, candidate) if candidate.startswith('..') else candidate
    if os.path.exists(path):
        site = open(path).read()
        break

src = open(os.path.join(ROOT, 'app.py')).read()
handler = src[src.index('def api_enquiry():'):]
handler = handler[:handler.index('\n@app.route')]
for field in ('from_name', 'from_email', 'phone', 'message', 'property',
              'interest', 'company_website'):
    assert f"data.get('{field}')" in handler, f'the API never reads {field!r}'
if site:
    for field in ('from_name', 'from_email', 'phone', 'message'):
        assert f'{field}:' in site, f'the website no longer sends {field!r}'
print('1. the website and the API agree on every field name')


# ─── 2. A real enquiry arrives ──────────────────────────────────────────────
before = count()
r = post({'from_name': 'Jane Doe', 'from_email': 'jane@example.com',
          'phone': '07700 900321', 'interest': 'Arrange a viewing',
          'message': 'Is this unit still available?'})
assert r.status_code == 200, r.status_code
assert count() == before + 1, 'a genuine website enquiry did not arrive'
with A.app.app_context():
    e = A.Enquiry.query.order_by(A.Enquiry.id.desc()).first()
    assert (e.source or '').lower() == 'website', f'source is {e.source!r}'
    assert e.contact_id, 'no contact was attached'
    c = A.Contact.query.get(e.contact_id)
    assert c.first_name == 'Jane' and c.email == 'jane@example.com'
    assert c.phone == '07700 900321'
print('2. a website enquiry arrives with its contact, name, email and phone')


# ─── 3. Nothing from nobody is refused ──────────────────────────────────────
# An empty POST used to create a blank enquiry with no contact — useless, and
# a way for anyone to fill the register with noise.
for label, payload in (('an empty body', {}),
                       ('no name and no email', {'message': 'hello'}),
                       ('a name but nothing asked', {'from_name': 'A Person'})):
    before = count()
    r = post(payload)
    assert r.status_code == 400, f'{label} was accepted ({r.status_code})'
    assert count() == before, f'{label} still created a record'
print('3. an empty or contentless submission is refused and creates nothing')


# ─── 4. The honeypot still silently swallows bots ───────────────────────────
before = count()
r = post({'from_name': 'Bot', 'from_email': 'bot@example.com',
          'message': 'spam', 'company_website': 'http://spam.example'})
assert r.status_code == 200, 'the honeypot should look successful to a bot'
assert count() == before, 'a honeypot submission created a record'
print('4. a bot filling the honeypot is accepted silently and stored nowhere')


# ─── 5. CORS allows the real website ────────────────────────────────────────
origins = src[src.index('CORS(app'):]
origins = origins[:origins.index('}})') + 3]
assert 'cowanandrutter.com' in origins, \
    'the live website origin is not allowed to post'
r = cl.options('/api/enquiry', headers={
    'Origin': 'https://cowanandrutter.com',
    'Access-Control-Request-Method': 'POST'})
assert r.status_code in (200, 204), r.status_code
assert r.headers.get('Access-Control-Allow-Origin') == 'https://cowanandrutter.com', \
    f"preflight returned {r.headers.get('Access-Control-Allow-Origin')!r}"
print('5. the browser preflight from cowanandrutter.com is allowed')


# ─── 6. The CRM is told before, and independently of, EmailJS ───────────────
# The handler used to return early when EmailJS was missing, so anyone whose
# ad blocker stopped that script submitted the form and the enquiry never
# reached the CRM at all.
if site:
    code = re.sub(r'^\s*//.*$', '', site, flags=re.M)
    fetch_at = code.index("api/enquiry")
    guard = re.search(r"if \(typeof emailjs === 'undefined'\)", code)
    assert guard, 'the EmailJS guard vanished entirely'
    assert guard.start() > fetch_at, \
        'the EmailJS check still runs BEFORE the CRM is told — a blocked ' \
        'script means the enquiry is silently lost'
    print('6. the CRM is told first; a blocked EmailJS no longer loses the enquiry')
else:
    print('6. skipped — the website repository is not beside this one')

print('\nWEBSITE ENQUIRIES: ALL CHECKS PASSED')
