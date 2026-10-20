# -*- coding: utf-8 -*-
"""Companies: the logo tile, the business category, and the icon set.

The point of most of these checks is the thing that must NOT happen: a company
must never be drawn with a property's photograph. The two have separate id
sequences, so serving one from the other would put a stranger's building
against a company's name — which is why there is a second macro at all rather
than a reuse of templates/_property_row.html.
"""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/companies.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db

failures = []


def check(label, condition):
    if not condition:
        failures.append(label)
    print('  %-66s %s' % (label, 'PASS' if condition else 'FAIL'))


# A real 1x1 PNG, so Pillow has something it can genuinely open.
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
       b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00'
       b'\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')

with A.app.app_context():
    db.create_all()
    A._sync_model_columns()
    A._migrate_rates_tables()
    db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                          password_hash=generate_password_hash('pw')))
    db.session.commit()
    council = A.Council.query.first()

    # One company in each of the three states the tile has to cover.
    with_logo = A.Organisation(name='Beta Holdings Ltd', business_category='Retail')
    with_cat = A.Organisation(name='Alpha Catering', business_category='Food and beverage')
    bare = A.Organisation(name='Gamma Trading Company')
    db.session.add_all([with_logo, with_cat, bare])
    db.session.commit()
    A._store_org_logo(with_logo, PNG, 'image/png', 'upload', origin='beta.png')
    db.session.commit()
    IDS = {'logo': with_logo.id, 'cat': with_cat.id, 'bare': bare.id}

    # A property carrying the same low id a company has, so that if the two
    # ever were served from one another it would show here rather than 404 by
    # luck.
    prop = A.Property(address='10 New Kings Road, London SW6 4LT',
                      postcode='SW6 4LT', property_type='Retail',
                      council_id=council.id)
    db.session.add(prop)
    db.session.commit()

cl = A.app.test_client()
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)

print('\nThe companies list')
r = cl.get('/organisations')
html = r.get_data(as_text=True)
check('the list loads', r.status_code == 200)
check('the stylesheet is linked', 'companies.css' in html)
check('a company with a logo shows its own logo URL',
      f'/organisations/{IDS["logo"]}/logo' in html)
check('a company with only a category gets the icon state', 'co-tile is-icon' in html)
check('a company with neither gets initials', 'co-tile is-initials' in html)
check('initials are the first letters of two words, not three', '>GT<' in html)
check('the category is written out under the name as well as drawn',
      'co-name__cat' in html and 'Food and beverage' in html)
check('NO property photograph is served on the companies list',
      'listing_photo' not in html and '/photo/' not in html)
check('the table still has its five columns and no sixth',
      html.count('<th>') == 6)      # five headings plus the actions column

print('\nThe company record')
for key, cid in IDS.items():
    rr = cl.get(f'/organisations/{cid}')
    body = rr.get_data(as_text=True)
    check(f'{key}: the record loads', rr.status_code == 200)
    check(f'{key}: the logo box is on the page', 'Company logo' in body)
    check(f'{key}: the header carries the mark', 'rec-header-id' in body)
    check(f'{key}: no property photograph on the record', 'listing_photo' not in body)

print('\nServing the image')
check('a company with a logo serves it',
      cl.get(f'/organisations/{IDS["logo"]}/logo').status_code == 200)
check('a company without one is a 404, not somebody else’s picture',
      cl.get(f'/organisations/{IDS["cat"]}/logo').status_code == 404)

print('\nThe logo itself')
with A.app.app_context():
    o = A.Organisation.query.get(IDS['logo'])
    check('stored as PNG whatever came in', o.logo_mime == 'image/png')
    check('the source is recorded', o.logo_source == 'upload')
    check('where it came from is recorded', o.logo_origin == 'beta.png')
    check('and when, so a lookup is not repeated', o.logo_tried_on is not None)
    check('a stored logo has no failure note', o.logo_note is None)

    # A file that is not an image must be refused, and say so.
    junk = A.Organisation(name='Junk Ltd')
    db.session.add(junk); db.session.commit()
    ok = A._store_org_logo(junk, b'this is not an image', 'image/png', 'upload')
    db.session.commit()
    check('a file that is not an image is refused', ok is False)
    check('nothing is stored when it is refused', junk.logo_data is None)
    check('and the reason is kept', bool(junk.logo_note))

    # A failed website lookup records why, so it is not retried every view.
    none_found = A.Organisation(name='Nowhere Ltd')
    db.session.add(none_found); db.session.commit()
    A._store_org_logo(none_found, None, None, None, note='no usable website address')
    db.session.commit()
    check('a failed lookup records its reason',
          none_found.logo_note == 'no usable website address')
    check('and the date it was tried', none_found.logo_tried_on is not None)

print('\nThe category list and the icon set')
icons = A.app.jinja_env.get_template('crm/_category_icons.html')
unmatched = [c for c in A.BUSINESS_CATEGORIES
             if 'cat-icon--unknown' in icons.module.category_icon(c)]
check('every business category has an icon of its own', not unmatched)
check('a category nobody has drawn falls back rather than breaking',
      'cat-icon--unknown' in icons.module.category_icon('Llama grooming'))
check('no category at all also falls back',
      'cat-icon--unknown' in icons.module.category_icon(None))
check('the categories are offered to every form that edits a company',
      A.app.jinja_env.globals.get('BUSINESS_CATEGORIES') is A.BUSINESS_CATEGORIES)
check('business category is a field the save route writes',
      any(f[0] == 'business_category' for f in A.ORGANISATION_FIELDS))

print('\nLooking on a company\u2019s own website: where we are allowed to look')
import org_logo

# The website box is free text and this code fetches it from inside Railway's
# network, so the guard matters more than the feature does. Nothing below
# touches the network: localhost and the link-local address resolve locally,
# and the reserved .invalid TLD cannot resolve at all.
check('a bare domain is accepted and made into a URL',
      org_logo.normalise_website('example.com') == 'https://example.com/')
check('a path is reduced to the site root, and www is left alone',
      org_logo.normalise_website('www.example.com/about/us') == 'https://www.example.com/')
check('a trailing dot and capitals are tidied',
      org_logo.normalise_website('EXAMPLE.com.') == 'https://example.com/')
check('an existing scheme is kept',
      org_logo.normalise_website('http://example.com') == 'http://example.com/')
check('an empty website box gives nothing, not a guess',
      org_logo.normalise_website('') is None and org_logo.normalise_website(None) is None)
check('a hostname with no dot is refused \u2014 it is on our own network',
      org_logo.normalise_website('intranet') is None)
check('a .local name is refused', org_logo.normalise_website('printer.local') is None)
check('a non-http scheme is refused',
      org_logo.normalise_website('file:///etc/passwd') is None
      and org_logo.normalise_website('ftp://example.com') is None)

check('loopback is not a public host', org_logo.is_public_host('localhost') is False)
check('a name that does not resolve is not a public host',
      org_logo.is_public_host('nothing-here.invalid') is False)

# The whole way through, not just the helper: a private address must come back
# as a refusal with a reason, and must never raise.
for target in ('http://127.0.0.1/', 'http://localhost/', 'http://169.254.169.254/',
               'http://10.0.0.1/', 'http://[::1]/'):
    out = org_logo.fetch_logo(target)
    check('fetch_logo refuses %s' % target, out['ok'] is False and bool(out['reason']))
check('fetch_logo on an empty website is refused with a reason',
      org_logo.fetch_logo('')['ok'] is False)
check('a refusal carries no image bytes',
      org_logo.fetch_logo('http://127.0.0.1/')['data'] is None)

print('\nSaving a category')
r = cl.post(f'/organisations/{IDS["bare"]}/save',
            data={'name': 'Gamma Trading Company',
                  'business_category': 'Logistics and distribution'},
            follow_redirects=True)
with A.app.app_context():
    saved = A.Organisation.query.get(IDS['bare']).business_category
check('a category chosen on the form is saved', saved == 'Logistics and distribution')
r = cl.get('/organisations?q=Logistics')
check('and the list can be searched by it', 'Gamma Trading Company' in r.get_data(as_text=True))

print()
if failures:
    print('FAILED: %d of these checks' % len(failures))
    for f in failures:
        print('   -', f)
    sys.exit(1)
print('COMPANIES: ALL CHECKS PASSED')
