f = r'C:\Users\SamuelC\property-db\app.py'
with open(f, 'r', encoding='utf-8') as fh:
    txt = fh.read()

photo_routes = '''
# ── Listing Photo upload/delete ───────────────────────────────────────────────

@app.route('/listings/<int:id>/photos/upload', methods=['POST'])
def listing_photo_upload(id):
    listing = Listing.query.get_or_404(id)
    files = request.files.getlist('photos')
    count = 0
    for f in files:
        if f and f.filename:
            data = f.read()
            ph = ListingPhoto(
                listing_id=id,
                file_data=data,
                filename=f.filename,
                file_mime=f.content_type or 'image/jpeg',
                file_size=len(data),
                sort_order=len(listing.photos),
            )
            db.session.add(ph)
            count += 1
    db.session.commit()
    flash(f'{count} photo(s) uploaded.', 'success')
    return redirect(url_for('listing_edit', id=id) + '#media')


@app.route('/listing-photos/<int:id>/delete', methods=['POST'])
def listing_photo_delete(id):
    ph = ListingPhoto.query.get_or_404(id)
    listing_id = ph.listing_id
    db.session.delete(ph)
    db.session.commit()
    return redirect(url_for('listing_edit', id=listing_id) + '#media')


@app.route('/listing-photos/<int:id>/image')
def listing_photo_image(id):
    from flask import send_file
    import io
    ph = ListingPhoto.query.get_or_404(id)
    return send_file(io.BytesIO(ph.file_data), mimetype=ph.file_mime or 'image/jpeg')


@app.route('/listings/<int:id>/brochure/upload', methods=['POST'])
def listing_brochure_upload(id):
    listing = Listing.query.get_or_404(id)
    f = request.files.get('brochure')
    if f and f.filename:
        listing.brochure_data     = f.read()
        listing.brochure_filename = f.filename
        listing.brochure_size     = len(listing.brochure_data)
        db.session.commit()
        flash('Brochure uploaded.', 'success')
    return redirect(url_for('listing_edit', id=id) + '#media')


@app.route('/listings/<int:id>/brochure/delete', methods=['POST'])
def listing_brochure_delete(id):
    listing = Listing.query.get_or_404(id)
    listing.brochure_data = listing.brochure_filename = listing.brochure_size = None
    db.session.commit()
    flash('Brochure removed.', 'info')
    return redirect(url_for('listing_edit', id=id) + '#media')


@app.route('/listings/<int:id>/brochure/download')
def listing_brochure_download(id):
    from flask import send_file
    import io
    listing = Listing.query.get_or_404(id)
    if not listing.brochure_data:
        flash('No brochure uploaded.', 'warning')
        return redirect(url_for('listing_edit', id=id))
    return send_file(io.BytesIO(listing.brochure_data),
                     mimetype='application/pdf', as_attachment=True,
                     download_name=listing.brochure_filename or 'brochure.pdf')


@app.route('/listings/<int:id>/floorplan/upload', methods=['POST'])
def listing_floorplan_upload(id):
    listing = Listing.query.get_or_404(id)
    f = request.files.get('floor_plan')
    if f and f.filename:
        listing.floor_plan_data     = f.read()
        listing.floor_plan_filename = f.filename
        listing.floor_plan_size     = len(listing.floor_plan_data)
        db.session.commit()
        flash('Floor plan uploaded.', 'success')
    return redirect(url_for('listing_edit', id=id) + '#media')


@app.route('/listings/<int:id>/floorplan/delete', methods=['POST'])
def listing_floorplan_delete(id):
    listing = Listing.query.get_or_404(id)
    listing.floor_plan_data = listing.floor_plan_filename = listing.floor_plan_size = None
    db.session.commit()
    return redirect(url_for('listing_edit', id=id) + '#media')

'''

if 'def listing_photo_upload' not in txt:
    marker = '# ── Website → DB: inbound enquiry webhook'
    txt = txt.replace(marker, photo_routes + marker, 1)
    print('Photo routes added.')
else:
    print('Photo routes already exist.')

with open(f, 'w', encoding='utf-8') as fh:
    fh.write(txt)
print('Done.')
