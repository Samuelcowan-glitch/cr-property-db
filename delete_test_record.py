"""
One-time cleanup: permanently remove the test record "1 Test Street" from the
public website / database, including its project-managed Listing and Project.

Matches by ADDRESS (not by id) so it is robust to id formatting. The public API
labels rows like cr-db-1 / cr-lst-1, but the real primary keys are plain
integers - matching on address avoids that mismatch.

Safety:
  * Idempotent - if nothing matches, it reports and changes nothing.
  * Atomic     - single commit at the end; if anything raises, nothing changes.
  * Verbose    - prints exactly what it will remove before committing.

Run it in the Railway console:
    /opt/venv/bin/python3 delete_test_record.py
"""
from app import app, db, Property, Project, Listing

TEST_ADDRESS = "1 test street"  # compared case-insensitively, trimmed


def run():
    with app.app_context():
        props = [p for p in Property.query.all()
                 if (p.address or "").strip().lower() == TEST_ADDRESS]

        # Catch any orphan listings whose own address matches but whose
        # property is missing/unlinked.
        prop_ids = {p.id for p in props}
        orphan_listings = [
            l for l in Listing.query.all()
            if (getattr(l, "address", "") or "").strip().lower() == TEST_ADDRESS
            and l.property_id not in prop_ids
        ]

        if not props and not orphan_listings:
            print(f'No record matching address "{TEST_ADDRESS}" found - nothing to do.')
            return

        del_listings = del_projects = del_props = 0

        for p in props:
            print(f"Property to delete: id={p.id} address={p.address!r}")
            for l in Listing.query.filter_by(property_id=p.id).all():
                print(f"  - listing id={l.id}")
                db.session.delete(l); del_listings += 1
            for pr in Project.query.filter_by(property_id=p.id).all():
                print(f"  - project id={pr.id} name={pr.name!r}")
                db.session.delete(pr); del_projects += 1
            db.session.delete(p); del_props += 1

        for l in orphan_listings:
            print(f"Orphan listing to delete: id={l.id}")
            db.session.delete(l); del_listings += 1

        db.session.commit()

        print(f"\nDeleted: {del_props} property, {del_projects} project, "
              f"{del_listings} listing.")
        print(f"Website listings now: {Listing.query.filter_by(website_listed=True).count()}")
        print("Done. Test record removed.")


if __name__ == '__main__':
    run()
