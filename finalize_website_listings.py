"""
One-time finalize step for the public website data.

Does two things in a SINGLE atomic commit, so the live site never blanks out:
  1. Deletes the test record  "1 Test Street"  (Property id = cr-db-1) and
     anything linked to it (its Projects / Listings, if any).
  2. Converts every remaining website-listed legacy Property into a
     Project + project-managed Listing, so the website is driven by projects.

Why atomic: /api/listings switches from "all Properties" to "only Listings"
the instant ONE website_listed Listing exists. Creating them all in one commit
means the switch-over has no empty-state window.

Safety:
  * Idempotent - skips any property that already has a Listing, and the test
                 record delete is a no-op once it's gone. Safe to re-run.
  * Atomic     - one commit at the end; if anything raises, nothing changes.

Run it in the Railway console:
    /opt/venv/bin/python3 finalize_website_listings.py
"""
from app import app, db, Property, Project, Listing

TEST_PROPERTY_ID = "cr-db-1"  # "1 Test Street" - should not be on the public site


def run():
    with app.app_context():
        # --- 1. Remove the test record and anything linked to it ---
        deleted_listings = deleted_projects = deleted_props = 0
        test = Property.query.filter_by(id=TEST_PROPERTY_ID).first()
        if test is not None:
            for l in Listing.query.filter_by(property_id=TEST_PROPERTY_ID).all():
                db.session.delete(l); deleted_listings += 1
            for pr in Project.query.filter_by(property_id=TEST_PROPERTY_ID).all():
                db.session.delete(pr); deleted_projects += 1
            db.session.delete(test); deleted_props += 1
            print(f"Test record {TEST_PROPERTY_ID} queued for deletion "
                  f"(+{deleted_projects} project, +{deleted_listings} listing).")
        else:
            print(f"Test record {TEST_PROPERTY_ID} not found - already gone.")

        # --- 2. Migrate remaining website-listed properties ---
        props = [p for p in Property.query.filter_by(website_listed=True).all()
                 if p.id != TEST_PROPERTY_ID]
        print(f"Website-listed properties to migrate: {len(props)}")
        print(f"Website listings before: {Listing.query.filter_by(website_listed=True).count()}")

        created_projects = created_listings = skipped = 0
        for p in props:
            if Listing.query.filter_by(property_id=p.id).first():
                skipped += 1
                continue

            project = Project.query.filter_by(property_id=p.id).first()
            if project is None:
                project = Project(
                    property_id=p.id,
                    name=p.address or f"Property {p.id}",
                    status='Active',
                )
                db.session.add(project)
                db.session.flush()
                created_projects += 1

            db.session.add(Listing(
                project_id=project.id,
                property_id=p.id,
                unit_name=None,
                website_listed=True,
                listing_status=p.listing_status or 'available',
                featured=bool(p.featured),
                website_category=p.website_category,
                use_class=p.use_class,
                residential_use=p.residential_use,
                area=p.area,
                listing_price=p.listing_price,
                listing_price_unit=p.listing_price_unit,
                price_display=p.price_display,
                size=p.size or p.listing_size,
                measurement_type=p.measurement_type,
                beds=p.beds,
                baths=p.baths,
                photo_id=p.photo_id,
                blurb=p.blurb or p.description,
                lat=p.lat,
                lng=p.lng,
                created_at=p.created_at,
            ))
            created_listings += 1

        db.session.commit()

        print(f"Test properties deleted: {deleted_props}")
        print(f"Projects created:        {created_projects}")
        print(f"Listings created:        {created_listings}")
        print(f"Skipped (already had a listing): {skipped}")
        print(f"Website listings now: {Listing.query.filter_by(website_listed=True).count()}")
        print("Done. The website now serves project-managed listings.")


if __name__ == '__main__':
    run()
