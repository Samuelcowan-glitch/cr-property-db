import sys; sys.path.insert(0, 'C:/Users/SamuelC/property-db')
from app import app, db
from sqlalchemy import text, inspect

new_cols = [
    ('min_size','REAL'), ('max_size','REAL'), ('measurement_std','TEXT'),
    ('total_size','REAL'), ('self_contained','BOOLEAN DEFAULT 0'),
    ('add_on_factor','REAL'), ('build_status','TEXT'),
    ('set_as_to_let','BOOLEAN DEFAULT 1'), ('lease_type','TEXT'),
    ('rent_qualifier','TEXT'), ('rent_inclusive','TEXT'),
    ('rent_from','REAL'), ('rent_to','REAL'), ('rent_comment','TEXT'),
    ('rent_on_application','BOOLEAN DEFAULT 0'),
    ('possession_now','BOOLEAN DEFAULT 0'), ('possession_quarter','TEXT'),
    ('possession_year','INTEGER'), ('possession_comment','TEXT'),
    ('lease_length_months','INTEGER'), ('lease_length_years','INTEGER'),
    ('lease_length_comment','TEXT'), ('inside_1954_act','TEXT'),
    ('repair_insuring','TEXT'), ('set_as_for_sale','BOOLEAN DEFAULT 0'),
    ('sale_price','REAL'), ('sale_price_display','TEXT'),
    ('service_charge','REAL'), ('service_charge_na','BOOLEAN DEFAULT 0'),
    ('service_charge_comment','TEXT'), ('rateable_value','REAL'),
    ('rateable_value_na','BOOLEAN DEFAULT 0'), ('rates_multiplier','REAL'),
    ('rates_payable','REAL'), ('epc_band','TEXT'), ('epc_band_potential','TEXT'),
    ('vat_comment','TEXT'), ('legal_fees','TEXT'),
    ('parking_ratio','TEXT'), ('parking_rent','REAL'),
    ('parking_rent_na','BOOLEAN DEFAULT 0'), ('parking_spaces','INTEGER'),
    ('summary_text','TEXT'), ('key_points','TEXT'), ('amenities','TEXT'),
    ('availability_reason','TEXT'),
    ('brochure_data','BLOB'), ('brochure_filename','TEXT'), ('brochure_size','INTEGER'),
    ('floor_plan_data','BLOB'), ('floor_plan_filename','TEXT'), ('floor_plan_size','INTEGER'),
]

with app.app_context():
    insp = inspect(db.engine)
    existing = {c['name'] for c in insp.get_columns('listings')}
    added = 0
    with db.engine.connect() as conn:
        for col_name, col_def in new_cols:
            if col_name not in existing:
                conn.execute(text(f'ALTER TABLE listings ADD COLUMN {col_name} {col_def}'))
                added += 1
        conn.commit()
    print(f'Added {added} columns. Total now: {len(existing) + added}')
