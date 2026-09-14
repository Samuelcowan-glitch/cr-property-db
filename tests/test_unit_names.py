"""The website title is the address, and says it once.

A listing carried a unit name that was pasted in front of the property address
to make the title. The guard only skipped it where the address STARTED with it,
so an address that mentioned the unit anywhere else got it twice:

    "10 New Kings Road, Unit 3"  +  "Unit 3"
        ->  "Unit 3, 10 New Kings Road, Unit 3"

The field is gone. What it has to not do is take a real unit number off the
website along with the repetition: an address of "10 New Kings Road" with a
unit name of "Unit 3" says something the address does not, and that has to end
up in the address rather than being dropped.
"""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/units.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db


def build():
    """Listings as they stand, with every shape of unit name."""
    with A.app.app_context():
        db.drop_all()
        db.create_all()
        A._sync_model_columns()
        db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                              password_hash=generate_password_hash('pw')))
        db.session.commit()
        made = {}

        def listing(key, address, unit, extra_listing=False):
            p = A.Property(address=address, postcode='SW6 1AA',
                           property_type='Office', size=1200)
            db.session.add(p); db.session.commit()
            pj = A.Project(name=key, property_id=p.id, fee_earner_id=1,
                           instruction_type=A.INSTRUCTION_TO_LET)
            db.session.add(pj); db.session.commit()
            l = A.Listing(project_id=pj.id, property_id=p.id, unit_name=unit,
                          listing_price=30000, listing_price_unit='pa',
                          listing_status='available', website_listed=True)
            db.session.add(l); db.session.commit()
            second = None
            if extra_listing:
                l2 = A.Listing(project_id=pj.id, property_id=p.id,
                               unit_name='Second Floor', listing_price=28000,
                               listing_price_unit='pa', listing_status='available',
                               website_listed=True)
                db.session.add(l2); db.session.commit()
                second = l2.id
            made[key] = {'prop': p.id, 'listing': l.id, 'second': second,
                         'project': pj.id}

        # The repetition: the address carries the unit, but not at the start.
        listing('repeat', '10 New Kings Road, Unit 3', 'Unit 3')
        # Already at the start: never repeated, and nothing to keep.
        listing('front', 'Unit 7, 8 Farm Lane', 'Unit 7')
        # A genuine unit number the address does not carry. This must survive.
        listing('genuine', '42 Peterborough Road', 'Unit 12')
        # A whole building. No unit, nothing to do.
        listing('whole', '1 Stanley Bridge Studios', None)
        # Two units on one property: folding either onto the shared address
        # would put one unit's name on both.
        listing('shared', '3 Munster Road', 'First Floor', extra_listing=True)
        return made


IDS = build()
cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)


def title(key):
    with A.app.app_context():
        return A.Listing.query.get(IDS[key]['listing']).display_title


def address(key):
    with A.app.app_context():
        return A.Property.query.get(IDS[key]['prop']).address


# ─── 1. The field is off both forms ─────────────────────────────────────────
r = cl.get(f"/listings/{IDS['repeat']['listing']}/edit", follow_redirects=True)
assert r.status_code == 200, r.status_code
form = r.get_data(as_text=True)
assert 'name="unit_name"' not in form, 'the unit name is still on the listing form'
assert 'Unit Name' not in form
# The page really is the listing form, so the check above is not passing on an
# error page that happens to lack the field.
assert 'name="listing_price"' in form or 'name="strapline"' in form, \
    'that was not the listing form'
project = cl.get(f"/projects/{IDS['repeat']['project']}").get_data(as_text=True)
assert 'name="unit_name"' not in project, 'the unit name is still on the instruction page'
print('1. the unit name is off the listing form and the instruction page')


# ─── 2. The address is no longer repeated ───────────────────────────────────
assert title('repeat') == '10 New Kings Road, Unit 3', title('repeat')
assert title('repeat').lower().count('unit 3') == 1, \
    f'the unit is still said twice: {title("repeat")}'
print('2. an address that mentions its own unit says it once')


# ─── 3. A unit at the front was never the problem, and is untouched ─────────
assert title('front') == 'Unit 7, 8 Farm Lane', title('front')
assert title('front').lower().count('unit 7') == 1
print('3. an address that starts with its unit is unchanged')


# ─── 4. A whole building has no unit to say ─────────────────────────────────
assert title('whole') == '1 Stanley Bridge Studios', title('whole')
print('4. a whole building is just its address')


# ─── 5. A genuine unit number is not lost ───────────────────────────────────
# Before the migration the title carried it only because the unit name was
# pasted on. Now it must be in the address itself.
A._migrate_fold_unit_names()
assert address('genuine') == 'Unit 12, 42 Peterborough Road', address('genuine')
assert title('genuine') == 'Unit 12, 42 Peterborough Road', title('genuine')
assert title('genuine').lower().count('unit 12') == 1
with A.app.app_context():
    kept = A.Listing.query.get(IDS['genuine']['listing'])
    assert kept.unit_name == 'Unit 12', 'what was typed was deleted rather than kept'
print('5. a unit number the address did not carry is folded into it, and kept')


# ─── 6. An address that already said it is not changed ──────────────────────
assert address('repeat') == '10 New Kings Road, Unit 3', address('repeat')
assert address('front') == 'Unit 7, 8 Farm Lane', address('front')
print('6. an address that already carried the unit is left exactly as it was')


# ─── 7. Two units on one property are left for a person ─────────────────────
# Folding either would put one unit's name on an address both share.
assert address('shared') == '3 Munster Road', \
    f'one unit name was folded onto an address two listings share: {address("shared")}'
with A.app.app_context():
    first = A.Listing.query.get(IDS['shared']['listing'])
    second = A.Listing.query.get(IDS['shared']['second'])
    assert first.unit_name == 'First Floor', 'the unit name was dropped'
    assert second.unit_name == 'Second Floor'
print('7. a property with two listings is left alone, with both unit names kept')


# ─── 8. Running it again changes nothing ────────────────────────────────────
before = {k: address(k) for k in IDS}
A._migrate_fold_unit_names()
after = {k: address(k) for k in IDS}
assert before == after, f'a second run changed an address: {before} -> {after}'
assert address('genuine').lower().count('unit 12') == 1, \
    'running it twice folded the unit in twice'
print('8. running the fold again changes nothing, and folds nothing twice')


# ─── 9. The website feed says the address once ──────────────────────────────
feed = cl.get('/api/listings')
assert feed.status_code == 200, feed.status_code
rows = feed.get_json()
rows = rows.get('listings', rows) if isinstance(rows, dict) else rows
titles = [r.get('title') or r.get('address') or '' for r in rows]
assert titles, 'the website feed returned nothing to check'
for shown in titles:
    words = shown.lower()
    for unit in ('unit 3', 'unit 7', 'unit 12', 'first floor'):
        assert words.count(unit) <= 1, f'the feed repeats {unit!r}: {shown}'
print(f'9. no title in the website feed repeats its unit ({len(titles)} checked)')


# ─── 10. Nothing was deleted ────────────────────────────────────────────────
with A.app.app_context():
    assert 'unit_name' in {c.name for c in A.Listing.__table__.columns}, \
        'the column was dropped, taking what was typed in it'
    assert A.Listing.query.count() == 6, A.Listing.query.count()
    assert A.Property.query.count() == 5
    assert A.Listing.query.filter(A.Listing.unit_name.isnot(None)).count() == 5
print('10. every listing, property and typed unit name is still there')


# ─── 11. Every page still loads ─────────────────────────────────────────────
for url in ('/', '/properties', '/projects', f"/projects/{IDS['repeat']['project']}",
            f"/listings/{IDS['repeat']['listing']}/edit", '/api/listings'):
    assert cl.get(url, follow_redirects=True).status_code == 200, url
print('11. every page that shows a listing still loads')

print('\nUNIT NAMES: ALL CHECKS PASSED')
