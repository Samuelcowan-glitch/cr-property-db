"""The boot migration, and the upgrade path a live database actually takes.

This exists because three deploys silently failed: a BOOLEAN column was given
an integer default, which Postgres refuses. The app crashed on boot, Railway
kept serving the previous version, and nothing said so.
"""
import os, re, sys, tempfile

tmp = tempfile.mkdtemp()
DB = f'{tmp}/live.db'
os.environ['DATABASE_URL'] = f'sqlite:///{DB}'
os.environ['EMAIL_SYNC_MINUTES'] = '0'
ROOT = "/Users/samueljcowan/Documents/Documents - Samuel’s MacBook Air/GitHub/cr-property-db"
sys.path.insert(0, ROOT)
SRC = open(f'{ROOT}/app.py').read()

# ─── 1. No boolean column is given an integer default ───────────────────────
# Postgres: "column is of type boolean but default expression is of type
# integer". SQLite accepts it, which is why this got through the tests.
bad = re.findall(r"\('(\w+)',\s*'BOOLEAN DEFAULT [^TF][^']*'\)", SRC)
assert not bad, f'boolean columns with a non-boolean default: {bad}'
assert 'BOOLEAN DEFAULT 0' not in SRC and 'BOOLEAN DEFAULT 1' not in SRC
print('1. every boolean default is TRUE or FALSE, which Postgres accepts')


# ─── 2. Every DDL type is one both databases understand ─────────────────────
KNOWN = ('TEXT', 'INTEGER', 'BIGINT', 'REAL', 'BLOB', 'BYTEA', 'DATE',
         'TIMESTAMP', 'BOOLEAN', 'VARCHAR', 'FLOAT', 'DOUBLE', 'NUMERIC')
pairs = re.findall(r"\('([a-z][a-z0-9_]*)',\s*'([A-Z][A-Z0-9 ()',_]*)'\)", SRC)
# Four characters is the shortest real type (TEXT); anything shorter is a
# default value somewhere else in the file, not a column definition.
odd = {ddl for _n, ddl in pairs if len(ddl) >= 4 and not ddl.startswith(KNOWN)}
assert not odd, f'column types Postgres may not accept: {odd}'
assert len(pairs) > 50, f'only found {len(pairs)} column definitions to check'
print('2. every column type is one both SQLite and Postgres understand')


# ─── 3. One bad column cannot take the boot down ────────────────────────────
assert 'def _add_columns(' in SRC, 'columns are still added in one batch'
block = SRC.split('def _add_columns(')[1].split('\ndef ')[0]
assert 'with db.engine.begin() as conn' in block, 'the statements share a transaction'
assert 'except Exception' in block, 'a failed column would still crash the boot'
assert 'MIGRATION INCOMPLETE' in block, 'a failure would pass unnoticed'
for gone in ["for col_name, col_def in org_cols:",
             "if col_name not in user_existing:",
             "if col_name not in cont_existing:",
             "if col_name not in org_existing:"]:
    assert gone not in SRC, f'an unguarded batch remains: {gone}'
assert SRC.count("_add_columns(") >= 6, 'the guarded helper is barely used'
print('3. columns are added one at a time, and a failure is logged not fatal')


# ─── 4. The whole boot runs on an empty database ────────────────────────────
from app import (app, db, _migrate_project_columns, _migrate_listing_columns,
                 _migrate_listings_table_columns, _migrate_email_columns,
                 _migrate_enquiry_columns, _migrate_document_columns,
                 _migrate_crm_columns, _migrate_security_columns,
                 _migrate_diary_tables, _ensure_default_user, _add_columns,
                 User, Organisation, Project, Transaction, Contact)


def boot():
    """Exactly what serve.py does at startup."""
    with app.app_context():
        db.create_all()
        _migrate_project_columns()
        _migrate_listing_columns()
        _migrate_listings_table_columns()
        _migrate_document_columns()
        _migrate_enquiry_columns()
        _migrate_email_columns()
        _migrate_crm_columns()
        _migrate_security_columns()
        _migrate_diary_tables()
        _ensure_default_user()


boot()
print('4. the full boot sequence runs on an empty database')


# ─── 5. And again on a database that already holds records ──────────────────
# The upgrade path a live database takes, which is where this went wrong.
with app.app_context():
    from datetime import date
    org = Organisation(name='Marsden Estates Ltd', status='Active')
    db.session.add(org)
    db.session.commit()
boot()
boot()          # a redeploy must be safe too
with app.app_context():
    assert Organisation.query.filter_by(name='Marsden Estates Ltd').count() == 1, \
        'booting twice duplicated a record'
    kept = Organisation.query.filter_by(name='Marsden Estates Ltd').one()
    assert kept.status == 'Active', 'the boot changed a record'
print('5. booting over an existing database, twice, changes nothing')


# ─── 6. The columns the last three deploys needed are all there ─────────────
with app.app_context():
    from sqlalchemy import inspect
    insp = inspect(db.engine)
    expected = {
        'organisations': ['trading_name', 'legal_name', 'company_number',
                          'marketing_consent', 'fee_earner_id', 'aml_status'],
        'users': ['full_name', 'active', 'can_earn_fees'],
        'transactions': ['fee_earner_id', 'agreement_type',
                         'expected_completion_date'],
        'projects': ['fee_earner_id'], 'contacts': ['fee_earner_id'],
        'enquiries': ['fee_earner_id'],
    }
    for table, columns in expected.items():
        have = {c['name'] for c in insp.get_columns(table)}
        missing = [c for c in columns if c not in have]
        assert not missing, f'{table} is missing {missing}'
    for table in ('organisation_types', 'organisation_roles',
                  'organisation_requirements', 'commission_targets',
                  'transaction_payments', 'transaction_documents'):
        assert table in insp.get_table_names(), f'{table} was never created'
print('6. every column and table the last three deploys needed exists')


# ─── 7. A column that cannot be added is reported, not fatal ────────────────
with app.app_context():
    failed = _add_columns('organisations', [('nonsense_col', 'NOT A REAL TYPE')])
    assert failed == ['nonsense_col'], failed
    # And the boot still completes afterwards.
    good = _add_columns('organisations', [('a_good_col', 'TEXT')])
    assert good == [], good
print('7. a column that fails is reported and the rest still go in')


# ─── 8. The health check Railway calls ──────────────────────────────────────
cl = app.test_client()
r = cl.get('/health')
assert r.status_code in (200, 302), r.status_code
print(f'8. /health answers ({r.status_code}) — see the note below')

print('\nMIGRATION: ALL CHECKS PASSED')
