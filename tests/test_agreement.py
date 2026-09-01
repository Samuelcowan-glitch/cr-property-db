"""Licence or letting, and what happens to the solicitor details."""
import os, re, sys, tempfile
tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/test.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
sys.path.insert(0, "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db")
from app import app, db, Property, Transaction, User, AGREEMENT_TYPES
app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
from werkzeug.security import generate_password_hash

with app.app_context():
    db.create_all()
    db.session.add(User(username='admin', password_hash=generate_password_hash('pw'), role='admin'))
    p = Property(address='1 High Street', postcode='TN1 1AA'); db.session.add(p); db.session.commit()
    t = Transaction(property_id=p.id, transaction_type='Leasehold', reference='TR-0001',
                    status='Terms Agreed', client_solicitor='J Smith',
                    client_solicitor_firm='Smith & Co')
    db.session.add(t); db.session.commit()
    TID = t.id

cl = app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def page():
    r = cl.get(f'/transactions/{TID}')
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)


def sol_box(html):
    return re.search(r'<div class="box box--grid" id="solicitors-box"([^>]*)>', html).group(1)


# ── 1. The choice is offered, in Important dates ──────────────────────────
h = page()
assert AGREEMENT_TYPES == ['Letting', 'Licence'], AGREEMENT_TYPES
# The Important dates box runs from its heading to the next numbered box.
# Split on the heading itself; the same words appear in a comment above it.
after = h.split('>8. Important dates</div>')[1]
dates = after.split('<!-- ──')[0]
assert 'name="agreement_type"' in dates, 'the agreement choice is not in Important dates'
for opt in AGREEMENT_TYPES:
    assert f'>{opt}</option>' in h, f'{opt} is not offered'
print('1. Letting or Licence can be chosen, in Important dates')

# ── 2. A letting keeps its solicitors ─────────────────────────────────────
cl.post(f'/transactions/{TID}/save', data={'agreement_type': 'Letting'}, follow_redirects=True)
h = page()
assert 'hidden' not in sol_box(h), 'a letting is hiding its solicitors'
assert '7. Solicitors' in h and 'Smith &amp; Co' in h
print('2. a letting still shows the solicitor details')

# ── 3. A licence puts them away, on the server ────────────────────────────
cl.post(f'/transactions/{TID}/save', data={'agreement_type': 'Licence'}, follow_redirects=True)
h = page()
assert 'hidden' in sol_box(h), 'a licence is still showing the solicitor details'
print('3. choosing a licence hides the solicitors without relying on the browser')

# ── 4. Nothing is deleted; it comes back on a letting ─────────────────────
with app.app_context():
    t = Transaction.query.get(TID)
    assert t.client_solicitor == 'J Smith', 'the solicitor was wiped by choosing a licence'
    assert t.client_solicitor_firm == 'Smith & Co'
cl.post(f'/transactions/{TID}/save', data={'agreement_type': 'Letting'}, follow_redirects=True)
assert 'hidden' not in sol_box(page()), 'switching back did not bring the solicitors out'
print('4. the solicitor details are kept, and return if it becomes a letting again')

# ── 5. A made-up agreement type is refused on the server ──────────────────
cl.post(f'/transactions/{TID}/save', data={'agreement_type': 'Handshake'}, follow_redirects=True)
with app.app_context():
    assert Transaction.query.get(TID).agreement_type == 'Letting', \
        'an invented agreement type was saved'
print('5. an invented agreement type is refused on the server')

# ── 6. The browser follows the choice too ─────────────────────────────────
h = page()
assert "getElementById('agreement-type')" in h and 'solicitors-box' in h, \
    'the page does not react to the choice until it is saved'
print('6. the section follows the choice before saving as well')

print('\nAGREEMENT TYPE: ALL CHECKS PASSED')
