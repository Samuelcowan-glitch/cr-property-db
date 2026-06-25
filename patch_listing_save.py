f = r'C:\Users\SamuelC\property-db\app.py'
with open(f, 'r', encoding='utf-8') as fh:
    txt = fh.read()

# Helper function to add to the route - build key_points from form
def build_save_block():
    return '''
        # Build key_points from individual fields
        kp_parts = []
        for i in range(8):
            v = request.form.get(f'key_point_{i}', '').strip()
            if v:
                kp_parts.append(v)
        kp_str = '||'.join(kp_parts) if kp_parts else None

        extra = dict(
            website_category   = request.form.get('website_category','commercial'),
            listing_status     = request.form.get('listing_status','available'),
            use_class          = request.form.get('use_class') or None,
            residential_use    = request.form.get('residential_use') or None,
            area               = request.form.get('area') or None,
            min_size           = pf(request.form.get('min_size','')),
            max_size           = pf(request.form.get('max_size','')),
            size               = pf(request.form.get('size','')),
            measurement_std    = request.form.get('measurement_std') or None,
            measurement_type   = request.form.get('measurement_type') or None,
            size_unit          = request.form.get('size_unit','sq ft'),
            self_contained     = bool(request.form.get('self_contained')),
            build_status       = request.form.get('build_status') or None,
            availability_reason= request.form.get('availability_reason') or None,
            # Lease
            set_as_to_let      = bool(request.form.get('set_as_to_let')),
            lease_type         = request.form.get('lease_type') or None,
            rent_qualifier     = request.form.get('rent_qualifier') or None,
            rent_inclusive     = request.form.get('rent_inclusive') or None,
            rent_from          = pf(request.form.get('rent_from','')),
            rent_to            = pf(request.form.get('rent_to','')),
            listing_price      = pf(request.form.get('listing_price','')),
            listing_price_unit = request.form.get('listing_price_unit','pa'),
            price_display      = request.form.get('price_display') or None,
            rent_on_application= bool(request.form.get('rent_on_application')),
            possession_now     = bool(request.form.get('possession_now')),
            possession_quarter = request.form.get('possession_quarter') or None,
            possession_year    = int(request.form.get('possession_year')) if request.form.get('possession_year','').strip() else None,
            lease_length_months= int(request.form.get('lease_length_months')) if request.form.get('lease_length_months','').strip() else None,
            lease_length_years = int(request.form.get('lease_length_years')) if request.form.get('lease_length_years','').strip() else None,
            inside_1954_act    = request.form.get('inside_1954_act') or None,
            repair_insuring    = request.form.get('repair_insuring') or None,
            # Sale
            set_as_for_sale    = bool(request.form.get('set_as_for_sale')),
            sale_price         = pf(request.form.get('sale_price','')),
            sale_price_display = request.form.get('sale_price_display') or None,
            # Rates
            service_charge     = pf(request.form.get('service_charge','')),
            service_charge_na  = bool(request.form.get('service_charge_na')),
            rateable_value     = pf(request.form.get('rateable_value','')),
            rateable_value_na  = bool(request.form.get('rateable_value_na')),
            epc_band           = request.form.get('epc_band') or None,
            legal_fees         = request.form.get('legal_fees') or None,
            # Parking
            parking_ratio      = request.form.get('parking_ratio') or None,
            parking_rent       = pf(request.form.get('parking_rent','')),
            parking_rent_na    = bool(request.form.get('parking_rent_na')),
            parking_spaces     = int(request.form.get('parking_spaces')) if request.form.get('parking_spaces','').strip() else None,
            # Marketing
            summary_text       = request.form.get('summary_text') or None,
            key_points         = kp_str,
            blurb              = request.form.get('blurb') or None,
            # Residential
            beds               = int(request.form.get('beds')) if request.form.get('beds','').strip() else None,
            baths              = int(request.form.get('baths')) if request.form.get('baths','').strip() else None,
            # Publish
            website_listed     = bool(request.form.get('website_listed')),
            featured           = bool(request.form.get('featured')),
            unit_name          = request.form.get('unit_name') or None,
        )'''

# Find and patch listing_new route
old_new = '''        l = Listing(
            project_id=proj_id,
            property_id=project.property_id,
            unit_name=request.form.get('unit_name') or None,
            website_listed=bool(request.form.get('website_listed')),
            listing_status=request.form.get('listing_status','available'),
            featured=bool(request.form.get('featured')),
            website_category=request.form.get('website_category') or None,
            use_class=request.form.get('use_class') or None,
            residential_use=request.form.get('residential_use') or None,
            area=request.form.get('area') or None,
            listing_price=pf(request.form.get('listing_price','')),
            listing_price_unit=request.form.get('listing_price_unit','pa'),
            price_display=request.form.get('price_display') or None,
            size=pf(request.form.get('size','')),
            measurement_type=request.form.get('measurement_type') or None,
            beds=int(request.form.get('beds')) if request.form.get('beds','').strip() else None,
            baths=int(request.form.get('baths')) if request.form.get('baths','').strip() else None,
            photo_id=request.form.get('photo_id') or None,
            blurb=request.form.get('blurb') or None,
        )'''

new_new = build_save_block() + '''
        l = Listing(project_id=proj_id, property_id=project.property_id, **extra)'''

if old_new in txt:
    txt = txt.replace(old_new, new_new, 1)
    print('listing_new route patched.')
else:
    print('listing_new old block not found')

# Patch listing_edit route
old_edit_start = "    if request.method == 'POST':\n        def pf(v): return float(v.replace(',','')) if v and v.strip() else None\n        l.unit_name          = request.form.get('unit_name') or None"
new_edit_start = "    if request.method == 'POST':\n        def pf(v): return float(v.replace(',','')) if v and v.strip() else None" + build_save_block() + """
        for k, v in extra.items():
            setattr(l, k, v)
        if True:  # placeholder"""

if old_edit_start in txt:
    # Find end of old edit block
    old_edit_end_marker = "        l.photo_id           = request.form.get('photo_id') or None\n        l.blurb              = request.form.get('blurb') or None"
    new_edit_end = "        pass  # fields set above"
    if old_edit_end_marker in txt:
        txt = txt.replace(old_edit_start, new_edit_start, 1)
        txt = txt.replace(old_edit_end_marker, new_edit_end, 1)
        print('listing_edit route patched.')
    else:
        print('listing_edit end marker not found')
else:
    print('listing_edit start not found, trying alternative patch...')
    # Just add a note
    print('Skipping edit patch - will handle manually')

with open(f, 'w', encoding='utf-8') as fh:
    fh.write(txt)
print('Done.')
