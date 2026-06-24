import os
from waitress import serve
from app import app, db, _migrate_project_columns, _migrate_listing_columns

with app.app_context():
    db.create_all()
    _migrate_project_columns()
    _migrate_listing_columns()

port = int(os.environ.get('PORT', 8080))

print("=" * 50)
print("  Cowan & Rutter Property Database")
print(f"  Open: http://localhost:{port}")
print("=" * 50)
serve(app, host='0.0.0.0', port=port, threads=4)
