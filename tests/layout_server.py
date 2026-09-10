"""Run the CRM on a local port with enough data for every page to be worth
looking at. Used by the layout harness, which drives a real browser against it
— alignment is a question about pixels, and only a browser knows those.

    python tests/layout_server.py 8099
"""
import os
import sys
import tempfile

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/layout.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
from werkzeug.security import generate_password_hash

db = A.db

with A.app.app_context():
    db.create_all()
    A._migrate_rates_tables()
    A._migrate_progression_columns()
    A._migrate_contact_roles()
    for fn in ('_migrate_security_columns', '_migrate_diary_tables',
               '_migrate_crm_columns', '_migrate_enquiry_columns',
               '_migrate_listing_columns', '_migrate_project_columns',
               '_migrate_document_columns', '_migrate_email_columns',
               '_migrate_listings_table_columns'):
        getattr(A, fn, lambda: None)()

    db.session.add(A.User(username='admin', role='admin',
                          full_name='Benjamin Cowan',
                          email='bc@cowanandrutter.co.uk',
                          password_hash=generate_password_hash('layout-pw')))
    db.session.commit()
    council = A.Council.query.first()

    org = A.Organisation(name='Hurlingham Holdings Limited',
                         address='12 Bank Street, London SW6 4LT')
    db.session.add(org)
    db.session.commit()

    people = []
    for first, last, kind in (('Phillipa', 'Smith', 'Landlord'),
                              ('Terence', 'Vole', 'Tenant'),
                              ('Sara', 'Okelo', 'Buyer'),
                              ('Margaret', 'Hale', 'Seller')):
        c = A.Contact(first_name=first, last_name=last, contact_type=kind,
                      email=f'{first.lower()}@example.co.uk',
                      phone='020 7731 0000', mobile='07700 900100',
                      organisation_id=org.id, status='Prospect',
                      tags='Key account, Kings Road')
        db.session.add(c)
        people.append(c)
    db.session.commit()
    for c in people:
        db.session.add(A.ContactRole(contact_id=c.id, role=c.contact_type))
    db.session.commit()

    props, projs = [], []
    for n, (addr, kind) in enumerate((
            ('42 Peterborough Road, London SW6 3BN', 'Office'),
            ('10 New Kings Road, London SW6 4LT', 'Retail'),
            ('8 Farm Lane, London SW6 1QJ', 'Industrial'))):
        p = A.Property(address=addr, postcode=addr.split()[-1],
                       property_type=kind, size=1636 + n * 400,
                       council_id=council.id if council else None,
                       client_contact_id=people[0].id)
        db.session.add(p)
        db.session.commit()
        props.append(p)
        pr = A.Project(name=addr.split(',')[0], property_id=p.id,
                       fee_earner_id=1, instruction_type=A.INSTRUCTION_TO_LET,
                       client_contact_id=people[0].id)
        db.session.add(pr)
        db.session.commit()
        projs.append(pr)
        db.session.add(A.Listing(
            project_id=pr.id, property_id=p.id, set_as_to_let=True,
            listing_price=57260, listing_price_unit='pa', epc_band='C',
            service_charge=4.5, strapline=f'{kind.upper()} | FULHAM',
            blurb='A well proportioned unit arranged over two floors.',
            location_description='Off the Kings Road, close to Parsons Green.',
            key_terms='New FRI lease\nAvailable now'))
        db.session.commit()

    for n, who in enumerate(people[:3]):
        db.session.add(A.Enquiry(
            subject=f'Particulars request from {who.first_name} {who.last_name}',
            enquiry_type=A.INQUIRY_TYPES[n % len(A.INQUIRY_TYPES)],
            contact_id=who.id, status='New',
            notes='Please could you send the particulars and confirm the rates.',
            source=['Website', 'Zoopla', 'Email'][n],
            project_id=projs[n % len(projs)].id,
            property_id=props[n % len(props)].id))
    db.session.commit()

    # A day's appointments, so the dashboard's diary has something in it.
    from datetime import datetime as _dt, timedelta as _td
    base = A.to_london(_dt.utcnow()).replace(minute=0, second=0, microsecond=0)
    for hour, title, kind, place in ((10, 'Viewing', 'viewing', '1 Stanley Bridge Studios'),
                                     (12, 'Meeting — client', 'meeting', 'Office'),
                                     (15, 'Inspection', 'appointment', '10 New Kings Road')):
        start = A.from_london(base.replace(hour=hour))
        db.session.add(A.DiaryEvent(title=title, event_type=kind, location=place,
                                    owner='Benjamin Cowan',
                                    start_at=start, end_at=start + _td(minutes=60)))
    db.session.commit()

print(f'layout server on {PORT}', flush=True)
A.app.config.update(TESTING=False)
A.app.run(host='127.0.0.1', port=PORT, debug=False, threaded=True,
          use_reloader=False)
