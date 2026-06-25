"""
READ-ONLY diagnostic. Changes nothing. Prints the current state of
Properties, Projects and Listings so we can see how they link up.

Run it in the Railway console:
    /opt/venv/bin/python3 inspect_state.py
"""
from app import app, db, Property, Project, Listing


def run():
    with app.app_context():
        props = Property.query.all()
        projects = Project.query.all()
        listings = Listing.query.all()

        print(f"Properties: {len(props)}")
        print(f"  website_listed=True: {Property.query.filter_by(website_listed=True).count()}")
        print(f"Projects:   {len(projects)}")
        print(f"Listings:   {len(listings)}")
        print(f"  website_listed=True: {Listing.query.filter_by(website_listed=True).count()}")

        print("\nProject id -> property_id:")
        for pr in projects:
            n = Listing.query.filter_by(project_id=pr.id).count()
            print(f"  project {pr.id}: property_id={pr.property_id} listings={n} name={pr.name!r}")

        print("\nListing id -> project_id / property_id (first 40):")
        for l in listings[:40]:
            print(f"  listing {l.id}: project_id={l.project_id} property_id={l.property_id} "
                  f"website_listed={l.website_listed}")

        print("\nDone (nothing changed).")


if __name__ == '__main__':
    run()
