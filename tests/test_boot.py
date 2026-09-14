"""The CRM must start against a database that is behind the code.

This is the fault that took production down from 10 to 14 September, and the
second time the same shape of thing has happened.

A migration adds its columns and then queries a model. But a SELECT built from
a SQLAlchemy model names EVERY column the model has, not only the ones that
migration knew about. So a migration that runs early and touches Contact asks
the database for columns a later migration has not added yet. On a fresh SQLite
database create_all() makes them all up front and nothing shows; on a Postgres
database that already holds the table, create_all() adds nothing, the query
fails, boot dies and the healthcheck never passes.

So: every table is brought up to its model before any migration runs, and this
holds the whole boot sequence against a database with columns missing.
"""
import os
import sys
import tempfile

import sqlalchemy

tmp = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{tmp}/boot.db'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as A
from werkzeug.security import generate_password_hash

A.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
db = A.db

# The order serve.py runs them in. Read from serve.py rather than copied, so
# this cannot quietly drift from what production actually does.
SERVE = open(os.path.join(ROOT, 'serve.py')).read()
BOOT = [name for name in (
    '_sync_model_columns', '_migrate_project_columns', '_migrate_listing_columns',
    '_migrate_listings_table_columns', '_migrate_document_columns',
    '_migrate_enquiry_columns', '_migrate_email_columns', '_migrate_crm_columns',
    '_migrate_rates_tables', '_migrate_progression_columns',
    '_migrate_contact_roles', '_migrate_retire_client', '_migrate_transaction_kinds',
    '_migrate_security_columns', '_migrate_diary_tables', '_ensure_default_user',
) if f'{name}()' in SERVE]

# Columns added late enough that a production database will not have them.
BEHIND = {
    'contacts': ['tags', 'req_status', 'req_use'],
    'transactions': ['terms_agreed_date', 'solicitors_instructed_date'],
}


def build_database_that_is_behind():
    """A populated database missing the newest columns, as production was."""
    with A.app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(A.User(username='admin', role='admin', full_name='Benjamin Cowan',
                              password_hash=generate_password_hash('pw')))
        prop = A.Property(address='Worlds End Studios, London SW10 0RJ',
                          postcode='SW10 0RJ', property_type='Office', size=2400)
        db.session.add(prop)
        db.session.commit()
        contact = A.Contact(first_name='Margaret', last_name='Hale',
                            contact_type='Tenant', req_area='Fulham, SW6',
                            req_budget_max=60000, status='Prospect')
        db.session.add(contact)
        db.session.commit()
        project = A.Project(name='Worlds End Studios', property_id=prop.id,
                            instruction_type=A.INSTRUCTION_TO_LET,
                            client_contact_id=contact.id)
        db.session.add(project)
        db.session.commit()
        db.session.add(A.Transaction(property_id=prop.id, project_id=project.id,
                                     transaction_type='Leasehold',
                                     reference='TR0003', status='Completed',
                                     agreed_value=285000, fee_type='Percentage',
                                     fee_percent=1.0))
        db.session.commit()
        kept = {'contacts': A.Contact.query.count(),
                'properties': A.Property.query.count(),
                'projects': A.Project.query.count(),
                'transactions': A.Transaction.query.count()}

        # Now take the newest columns away, which is what production looked like.
        with db.engine.begin() as conn:
            for table, columns in BEHIND.items():
                for column in columns:
                    try:
                        conn.execute(sqlalchemy.text(
                            f'ALTER TABLE {table} DROP COLUMN {column}'))
                    except Exception:
                        pass                  # already absent is the same thing
        return kept


def columns_of(table):
    with A.app.app_context():
        return {c['name'] for c in sqlalchemy.inspect(db.engine).get_columns(table)}


# ─── 1. The database really is behind ───────────────────────────────────────
KEPT = build_database_that_is_behind()
missing = {t: [c for c in cols if c not in columns_of(t)] for t, cols in BEHIND.items()}
assert missing['contacts'], 'the test did not manage to take the columns away'
print(f'1. built a database missing {sum(len(v) for v in missing.values())} '
      'columns the code expects')


# ─── 2. Querying it fails before the fix would have run ─────────────────────
# This is the exact production exception: the SELECT names a column the table
# has not got, and it happens inside a migration, long before any page loads.
with A.app.app_context():
    try:
        A.Contact.query.first()
        raise AssertionError('the query should have failed on the missing column')
    except AssertionError:
        raise
    except Exception as e:
        assert 'req_status' in str(e) or 'column' in str(e).lower(), str(e)[:120]
print('2. querying a contact against it fails, exactly as production did')


# ─── 3. The whole boot sequence completes anyway ────────────────────────────
assert BOOT[0] == '_sync_model_columns', \
    'the schema is not brought up to the models before the migrations run'
assert len(BOOT) >= 15, f'only {len(BOOT)} boot steps found in serve.py'
with A.app.app_context():
    for name in BOOT:
        step = getattr(A, name)
        try:
            step()
        except Exception as e:
            raise AssertionError(f'{name} failed at boot: {type(e).__name__}: {e}') from e
print(f'3. all {len(BOOT)} boot steps complete against a database that was behind')


# ─── 4. The missing columns are there, and nothing was lost ─────────────────
for table, columns in BEHIND.items():
    have = columns_of(table)
    for column in columns:
        assert column in have, f'{table}.{column} was never added'
with A.app.app_context():
    assert A.Contact.query.count() == KEPT['contacts'], 'a contact was lost'
    assert A.Property.query.count() == KEPT['properties'], 'a property was lost'
    assert A.Project.query.count() == KEPT['projects'], 'a project was lost'
    assert A.Transaction.query.count() == KEPT['transactions'], 'a transaction was lost'
    margaret = A.Contact.query.filter_by(last_name='Hale').first()
    assert margaret is not None
    assert margaret.req_area == 'Fulham, SW6', 'a requirement was overwritten'
    assert margaret.req_budget_max == 60000, 'a budget was overwritten'
    assert margaret.status == 'Prospect'
    assert margaret.req_status is None, 'a value was invented for the new column'
print('4. the columns are added, and every record and value survives')


# ─── 5. The pages that were down now answer ─────────────────────────────────
cl = A.app.test_client()
health = cl.get('/health')
assert health.status_code == 200, f'the healthcheck returns {health.status_code}'
assert health.get_json().get('status') == 'ok', health.get_json()
# Railway's healthcheck is unauthenticated; it must not be redirected to login.
assert '/health' in A._PUBLIC_ENDPOINTS or 'health' in str(A._PUBLIC_ENDPOINTS), \
    'the healthcheck is behind the login and would redirect'
cl.post('/login', data={'username': 'admin', 'password': 'pw'}, follow_redirects=True)
for url in ('/', '/contacts', '/contacts/new', '/properties', '/projects',
            '/transactions', '/transactions/new', '/enquiries', '/diary'):
    assert cl.get(url).status_code == 200, f'{url} is {cl.get(url).status_code}'
print('5. the healthcheck answers 200 unauthenticated, and every page loads')


# ─── 6. Running the whole thing again changes nothing ───────────────────────
before = {t: columns_of(t) for t in BEHIND}
with A.app.app_context():
    for name in BOOT:
        getattr(A, name)()
after = {t: columns_of(t) for t in BEHIND}
assert before == after, 'a second boot altered the schema'
with A.app.app_context():
    assert A.Contact.query.count() == KEPT['contacts'], 'a second boot lost a record'
print('6. booting again adds nothing and changes nothing')


# ─── 7. It only ever adds ───────────────────────────────────────────────────
# A column the database has and the models no longer do must be left alone —
# it may be the only copy of something.
with A.app.app_context():
    with db.engine.begin() as conn:
        conn.execute(sqlalchemy.text(
            'ALTER TABLE contacts ADD COLUMN retired_field TEXT'))
        conn.execute(sqlalchemy.text(
            "UPDATE contacts SET retired_field = 'something somebody typed'"))
A._sync_model_columns()
assert 'retired_field' in columns_of('contacts'), \
    'a column the models no longer have was dropped'
with A.app.app_context():
    kept = db.session.execute(sqlalchemy.text(
        'SELECT retired_field FROM contacts LIMIT 1')).scalar()
assert kept == 'something somebody typed', 'the data in it was destroyed'
print('7. a column the models no longer have is left alone, data and all')


# ─── 8. Every model column is reachable ─────────────────────────────────────
# The one that failed in production was reachable in the model and absent from
# the table. After the sync there should be no such column anywhere.
with A.app.app_context():
    inspector = sqlalchemy.inspect(db.engine)
    tables = set(inspector.get_table_names())
    absent = []
    for mapper in db.Model.registry.mappers:
        table = mapper.local_table
        if table is None or table.name not in tables:
            continue
        have = {c['name'] for c in inspector.get_columns(table.name)}
        absent += [f'{table.name}.{c.name}' for c in table.columns if c.name not in have]
assert not absent, f'the models expect columns the database has not got: {absent}'
print(f'8. every column of every model exists in the database')

print('\nBOOT: ALL CHECKS PASSED')
