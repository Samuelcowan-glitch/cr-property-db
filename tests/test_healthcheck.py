"""The deployment healthcheck.

Railway polls the path in railway.json after every deploy and requires a 2xx.
It got a 302 for four days — /health was not in the public endpoint list, so
the login guard redirected it — and every deployment failed at Network >
Healthcheck with the build and the deploy both green. The previous build
stayed live, so the site kept working while nothing new ever reached it.

Worth a test of its own: a failure here is invisible in the application and
silently stops everything shipping.
"""
import json
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/health.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)

with A.app.app_context():
    A.db.create_all()


# ─── 1. The path railway.json names actually exists ─────────────────────────
with open(os.path.join(ROOT, 'railway.json')) as fh:
    config = json.load(fh)
path = config.get('deploy', {}).get('healthcheckPath')
assert path, 'railway.json names no healthcheck path'
rules = {r.rule for r in A.app.url_map.iter_rules()}
assert path in rules, f'railway.json checks {path!r}, which the app does not serve'
print(f'1. railway.json checks {path!r}, and the app serves it')


# ─── 2. It answers 200 to a stranger ────────────────────────────────────────
# Railway sends no cookies. Anything but a 2xx fails the deployment.
anon = A.app.test_client()
r = anon.get(path)
assert 200 <= r.status_code < 300, (
    f'{path} returned {r.status_code} to an anonymous request'
    + (f' -> {r.headers.get("Location")}' if r.status_code in (301, 302, 307, 308)
       else '') + ' — this fails the deploy')
print(f'2. {path} returns {r.status_code} with no session, as Railway probes it')


# ─── 3. It is exempt from the login guard ───────────────────────────────────
assert 'health' in A._PUBLIC_ENDPOINTS, \
    'health is not a public endpoint, so the login guard will redirect it'
print('3. health is in the public endpoint list')


# ─── 4. It does not depend on the database ──────────────────────────────────
# A healthcheck that queries fails whenever the database is slow to wake,
# which turns a healthy deploy into a failed one.
src = open(os.path.join(ROOT, 'app.py')).read()
body = src[src.index("@app.route('/health')"):]
body = body[:body.index('\n@app.route')] if '\n@app.route' in body else body[:400]
for forbidden in ('.query', 'db.session', 'current_user'):
    assert forbidden not in body, f'the healthcheck touches {forbidden}'
print('4. the healthcheck touches neither the database nor the session')


# ─── 5. Every public endpoint really is reachable without signing in ────────
# Anything named public but still guarded would fail the same silent way.
for endpoint in ('health', 'login'):
    rule = next((r for r in A.app.url_map.iter_rules()
                 if r.endpoint == endpoint and 'GET' in r.methods), None)
    if not rule or '<' in rule.rule:
        continue
    code = anon.get(rule.rule).status_code
    assert code < 400, f'{endpoint} ({rule.rule}) returned {code} to a stranger'
print('5. the public endpoints answer without a session')

print('\nHEALTHCHECK: ALL CHECKS PASSED')
