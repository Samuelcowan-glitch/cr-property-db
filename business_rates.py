"""Business rates: the arithmetic, kept away from the database and the web.

Everything here is a plain function over plain values, so the calculation can
be read, tested and argued with on its own. The CRM's models and routes are in
app.py; nothing in this file knows they exist.

Money is held as a whole number of pence, and multipliers as a whole number of
hundred-thousandths. Money is never a float: a float cannot hold £0.10 exactly,
so a long enough chain of additions drifts, and a rates figure that goes on a
brochure must not drift. Integers are also the only representation that behaves
identically on SQLite and on Postgres, which matters because this is developed
on one and run on the other.

A figure produced here is an ESTIMATE. Liability depends on the ratepayer's own
circumstances — how many properties they occupy, which reliefs they qualify
for, what transitional arrangements apply — none of which the CRM can know. The
caller is responsible for labelling it as an estimate and for never presenting
it as a council's own figure.
"""

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

# Multipliers are quoted to three decimal places (0.499, 0.555). Five gives
# room for a future rate quoted more finely without another migration.
MULTIPLIER_SCALE = 100_000

# What a multiplier can be. The names are the government's own.
MULTIPLIER_TYPES = [
    'Standard',
    'Small business',
    'Other',
]

# How a relief is expressed. Nothing here is ever applied on the CRM's own
# initiative — eligibility is the ratepayer's to establish, not ours to assume.
RELIEF_TYPES = [
    'Small Business Rate Relief',
    'Retail, Hospitality and Leisure Relief',
    'Charitable Rate Relief',
    'Rural Rate Relief',
    'Empty Property Relief',
    'Discretionary Relief',
    'Transitional Relief',
    'Other relief',
]

MONEY = Decimal('0.01')


# ── Money ───────────────────────────────────────────────────────────────────

def to_pence(value):
    """A typed figure as whole pence, or None.

    Accepts what a person actually types: '12,500', '£12,500.50', ' 12500 ',
    a Decimal, or an int. Refuses anything it cannot read rather than guessing
    at a number, because a misread rateable value would reach a brochure.
    """
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value * 100
    if isinstance(value, Decimal):
        return int((value * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    text = str(value).strip().replace(',', '').replace('£', '').replace(' ', '')
    if not text:
        return None
    try:
        return int((Decimal(text) * 100).quantize(Decimal('1'),
                                                  rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def from_pence(pence):
    """Pence back to pounds, exactly, as a Decimal with two places."""
    if pence is None:
        return None
    return (Decimal(int(pence)) / 100).quantize(MONEY, rounding=ROUND_HALF_UP)


def money(pence, blank='—'):
    """Pence as it should be written down: £12,500.50."""
    if pence is None:
        return blank
    amount = from_pence(pence)
    sign = '-' if amount < 0 else ''
    return f'{sign}£{abs(amount):,.2f}'


def to_multiplier(value):
    """A typed multiplier as whole hundred-thousandths, or None.

    Accepts 0.499 and 49.9p alike — a multiplier is quoted both ways, and
    somebody will type either.
    """
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        value = Decimal(value)
    text = str(value).strip().replace('£', '').replace(' ', '')
    pennies = text.endswith('p')
    if pennies:
        text = text[:-1]
    if not text:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if pennies:
        number = number / 100
    if number < 0:
        return None
    return int((number * MULTIPLIER_SCALE).quantize(Decimal('1'),
                                                    rounding=ROUND_HALF_UP))


def from_multiplier(scaled):
    """A scaled multiplier back to the figure the government publishes."""
    if scaled is None:
        return None
    return (Decimal(int(scaled)) / MULTIPLIER_SCALE).normalize()


def multiplier_str(scaled, blank='—'):
    """A multiplier as it is quoted: 0.499."""
    if scaled is None:
        return blank
    return f'{Decimal(int(scaled)) / MULTIPLIER_SCALE:.3f}'


def _apply(pence, scaled_rate):
    """A rate in the pound applied to a sum, rounded to the nearest penny.

    Half rounds up, which is the convention for money and is stated here so
    nobody has to infer it from the code.
    """
    exact = Decimal(int(pence)) * Decimal(int(scaled_rate)) / MULTIPLIER_SCALE
    return int(exact.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


# ── Tax years ───────────────────────────────────────────────────────────────

def tax_year_of(when):
    """The rating year a date falls in, written the way the government writes
    it: a year beginning 1 April, so 2025-03-31 is still 2024/25."""
    year = when.year if when.month >= 4 else when.year - 1
    return f'{year}/{str(year + 1)[-2:]}'


def tax_year_bounds(tax_year):
    """The first and last day of a tax year written '2025/26'."""
    from datetime import date
    start = int(str(tax_year).split('/')[0])
    return date(start, 4, 1), date(start + 1, 3, 31)


def tax_year_options(today, back=3, forward=1):
    """The years worth offering, newest first."""
    start = int(tax_year_of(today).split('/')[0])
    years = range(start + forward, start - back - 1, -1)
    return [f'{y}/{str(y + 1)[-2:]}' for y in years]


# ── Choosing a multiplier ───────────────────────────────────────────────────

def eligible_multipliers(rows, rateable_value_pence=None, property_type=None):
    """Those of `rows` a property could use, best match first.

    A row is a dict with tax_year, name, multiplier_type, value (scaled),
    category, rv_min / rv_max (pence, either may be None) and starts_on.
    Filtering is on what the CRM actually knows — the rateable value and the
    property's type — and never on anything about the occupier.
    """
    out = []
    for row in rows:
        low, high = row.get('rv_min'), row.get('rv_max')
        if rateable_value_pence is not None:
            if low is not None and rateable_value_pence < low:
                continue
            if high is not None and rateable_value_pence >= high:
                continue
        category = (row.get('category') or '').strip()
        if category and property_type and category.lower() != str(property_type).lower():
            continue
        out.append(row)
    # A row with a threshold is a better fit than a catch-all, and one tied to
    # a category better still.
    out.sort(key=lambda r: (bool(r.get('category')),
                            r.get('rv_min') is not None or r.get('rv_max') is not None),
             reverse=True)
    return out


def suggest_multiplier(rows, rateable_value_pence=None, property_type=None):
    """The multiplier the CRM would propose, with its reasoning, or None.

    A suggestion only. Small Business Rate Relief in particular depends on how
    many properties the ratepayer occupies, which is not in the CRM, so the
    small business MULTIPLIER may be proposed on the rateable value alone while
    the RELIEF never is.
    """
    fits = eligible_multipliers(rows, rateable_value_pence, property_type)
    if not fits:
        return None
    best = dict(fits[0])
    why = [f"{best['name']} for {best['tax_year']}"]
    if rateable_value_pence is not None and (best.get('rv_min') or best.get('rv_max')):
        low = money(best['rv_min']) if best.get('rv_min') else None
        high = money(best['rv_max']) if best.get('rv_max') else None
        if low and high:
            why.append(f'rateable values from {low} to under {high}')
        elif high:
            why.append(f'rateable values under {high}')
        elif low:
            why.append(f'rateable values of {low} and over')
    best['why'] = ', '.join(why)
    best['alternatives'] = fits[1:]
    return best


# ── The calculation ─────────────────────────────────────────────────────────

def calculate(rateable_value_pence, multiplier_scaled,
              relief_percent=None, relief_amount_pence=None,
              transitional_pence=None, supplement_pence=None,
              other_pence=None):
    """The estimated annual liability, and every step that produced it.

    Base liability      = rateable value × multiplier
    Estimated liability = base + supplements + transitional + other − reliefs

    A percentage relief is taken off the base liability, which is the usual
    order and is stated in the breakdown so the reader is not left to assume
    it. Transitional and other adjustments may be negative. The estimate is
    never allowed below zero — a property cannot have a negative rates bill —
    and the breakdown says so when that floor is reached.

    Returns None where there is nothing to calculate from.
    """
    if rateable_value_pence is None or multiplier_scaled is None:
        return None

    base = _apply(rateable_value_pence, multiplier_scaled)

    relief = 0
    relief_lines = []
    if relief_percent:
        pct = Decimal(str(relief_percent))
        taken = int((Decimal(base) * pct / 100).quantize(Decimal('1'),
                                                         rounding=ROUND_HALF_UP))
        relief += taken
        relief_lines.append((f'Relief at {pct.normalize()}% of the base liability',
                             -taken))
    if relief_amount_pence:
        relief += int(relief_amount_pence)
        relief_lines.append(('Relief as a fixed amount', -int(relief_amount_pence)))

    additions = []
    for label, value in (('Supplement', supplement_pence),
                         ('Transitional adjustment', transitional_pence),
                         ('Other adjustment', other_pence)):
        if value:
            additions.append((label, int(value)))

    total = base - relief + sum(v for _, v in additions)
    floored = total < 0
    if floored:
        total = 0

    return {
        'rateable_value': int(rateable_value_pence),
        'multiplier': int(multiplier_scaled),
        'base': base,
        'relief': relief,
        'relief_lines': relief_lines,
        'additions': additions,
        'adjustments': sum(v for _, v in additions),
        'total': total,
        'monthly': int((Decimal(total) / 12).quantize(Decimal('1'),
                                                      rounding=ROUND_HALF_UP)),
        'floored': floored,
        'lines': ([('Rateable value', int(rateable_value_pence))]
                  + [('Base liability at ' + multiplier_str(multiplier_scaled), base)]
                  + relief_lines + additions
                  + [('Estimated annual rates payable', total)]),
    }
