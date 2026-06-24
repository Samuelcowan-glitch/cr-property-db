import os
from waitress import serve
from app import app, db, _migrate_project_columns, _migrate_listing_columns, _ensure_default_user, Property

with app.app_context():
    db.create_all()
    _migrate_project_columns()
    _migrate_listing_columns()
    _ensure_default_user()

    # Auto-seed properties if database is empty (first deploy)
    if Property.query.count() == 0:
        try:
            import import_listings
        except Exception as e:
            print(f"Seed warning: {e}")

port = int(os.environ.get('PORT', 8080))
print("=" * 50)
print("  Cowan & Rutter Property Database")
print(f"  Open: http://localhost:{port}")
print("=" * 50)
serve(app, host='0.0.0.0', port=port, threads=4)
