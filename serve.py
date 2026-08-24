import os
from waitress import serve
from app import (app, db, _migrate_project_columns, _migrate_listing_columns,
                 _migrate_listings_table_columns, _migrate_email_columns,
                 _migrate_enquiry_columns, _migrate_document_columns,
                 _migrate_crm_columns, _ensure_default_user, _seed_project_listings,
                 Property, Contact, Enquiry, EnquiryNote)

with app.app_context():
    db.create_all()
    _migrate_project_columns()
    _migrate_listing_columns()
    _migrate_listings_table_columns()
    _migrate_document_columns()
    _migrate_enquiry_columns()
    _migrate_email_columns()
    _migrate_crm_columns()
    _ensure_default_user()

    # First deploy only (empty DB): seed the 32 website properties, then give
    # each a Project + project-managed Listing so the public website is driven
    # by projects. Gated on an empty DB so it never recreates listings a user
    # later deletes, and never touches user-managed data on a persistent DB.
    if Property.query.count() == 0:
        try:
            import import_listings  # seeds the 32 website properties
            _seed_project_listings()
        except Exception as e:
            print(f"Seed warning: {e}")

# Poll the mailbox for portal leads (Zoopla etc.) and client emails. Silent
# no-op when the Microsoft 365 variables are not set.
try:
    from email_sync import start_background_sync
    start_background_sync(app, db, Contact, Enquiry, EnquiryNote)
except Exception as e:
    print(f'Email sync not started: {e}')

port = int(os.environ.get('PORT', 8080))
print("=" * 50)
print("  Cowan & Rutter Property Database v2")
print(f"  Open: http://localhost:{port}")
print("=" * 50)
serve(app, host='0.0.0.0', port=port, threads=4)
