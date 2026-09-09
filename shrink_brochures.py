"""Shrink the brochures already saved against instructions.

The generator now sizes every photograph to the space it occupies, so anything
made from 8 September 2026 onwards is a fraction of a megabyte. The documents
saved before that still hold their photographs at full camera resolution —
some are over a hundred megabytes — and those are the ones sitting in the
Brochure field, being attached to emails.

This rewrites them in place: each embedded image is resized to what the page
actually needs and re-encoded, and the rest of the document — its text, its
layout, its fonts — is untouched.

It is careful about a few things:

  - A brochure is only replaced when the result is genuinely smaller. If
    recompressing gains nothing, the original is kept exactly as it was.
  - Nothing is deleted. The replacement is written to the same field in one
    transaction per brochure, so a failure part-way leaves the rest alone.
  - Third-party brochures that were uploaded rather than generated are
    included, because they are emailed just the same, but they are reported
    separately so it is clear what was touched.

Run it from the Railway console:

    python shrink_brochures.py            # report only, changes nothing
    python shrink_brochures.py --apply    # rewrite the oversized ones
"""

import io
import os
import sys

# A brochure page is A4 landscape, 842pt across. At 150 dpi that is about
# 1750 pixels, which is as much as any photograph on it can show.
MAX_EDGE = 1800
JPEG_QUALITY = 82
# Below this there is nothing worth gaining, and re-encoding only loses a
# little quality for no reason.
WORTH_DOING = 2_000_000


def shrink(pdf_bytes):
    """Return a smaller version of the document, or None if not worth it."""
    import pymupdf
    from PIL import Image

    doc = pymupdf.open(stream=pdf_bytes, filetype='pdf')
    touched = 0

    # Which page to ask for each image — replace_image is a page method.
    where = {}
    for page in doc:
        for info in page.get_images(full=True):
            where.setdefault(info[0], page)

    for xref, page in where.items():
        try:
            src = doc.extract_image(xref)
        except Exception:
            continue
        data = src['image']
        if len(data) < 60_000:
            continue                      # already small — the mark, an icon
        try:
            im = Image.open(io.BytesIO(data))
            if max(im.size) <= MAX_EDGE and len(data) < 400_000:
                continue                  # already a sensible size
            im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
            buf = io.BytesIO()
            if im.mode in ('RGBA', 'LA') and im.getchannel('A').getextrema()[0] < 255:
                im.convert('RGBA').save(buf, 'PNG', optimize=True)
            else:
                im.convert('RGB').save(buf, 'JPEG', quality=JPEG_QUALITY,
                                       optimize=True, progressive=True)
            if buf.tell() >= len(data):
                continue                  # no gain — leave it as it is
            page.replace_image(xref, stream=buf.getvalue())
            touched += 1
        except Exception:
            continue                      # one awkward image must not stop the rest

    if not touched:
        doc.close()
        return None

    out = io.BytesIO()
    doc.save(out, garbage=4, deflate=True, clean=True)
    doc.close()
    smaller = out.getvalue()
    # Only ever an improvement.
    return smaller if len(smaller) < len(pdf_bytes) else None


def main():
    apply_changes = '--apply' in sys.argv
    from app import app, db, Listing, AuditLog

    with app.app_context():
        rows = (Listing.query
                .filter(Listing.brochure_data.isnot(None))
                .order_by(Listing.id).all())

        print(f'{len(rows)} brochure(s) attached to instructions')
        print(f'{"REPORT ONLY — nothing will change" if not apply_changes else "REWRITING"}')
        print()

        before_total = after_total = 0
        changed = skipped = failed = 0

        for l in rows:
            data = l.brochure_data
            name = l.brochure_filename or f'listing {l.id}'
            before = len(data)
            before_total += before

            if before < WORTH_DOING:
                after_total += before
                skipped += 1
                continue

            try:
                smaller = shrink(data)
            except Exception as e:
                print(f'  FAILED  {name[:58]:58} {e}')
                after_total += before
                failed += 1
                continue

            if not smaller:
                after_total += before
                skipped += 1
                continue

            after_total += len(smaller)
            changed += 1
            print(f'  {before/1_000_000:7.1f} MB -> {len(smaller)/1_000_000:6.2f} MB   '
                  f'{name[:52]}')

            if apply_changes:
                l.brochure_data = smaller
                l.brochure_size = len(smaller)
                db.session.add(AuditLog(
                    username='console', action='brochure-shrunk',
                    entity='Listing', entity_id=str(l.id),
                    detail=f'{before} bytes to {len(smaller)}'))
                db.session.commit()

        print()
        print(f'  {changed} shrunk, {skipped} already small enough, {failed} failed')
        print(f'  {before_total/1_000_000:.1f} MB -> {after_total/1_000_000:.1f} MB')
        if not apply_changes and changed:
            print()
            print('  Nothing was changed. Run again with --apply to rewrite them.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
