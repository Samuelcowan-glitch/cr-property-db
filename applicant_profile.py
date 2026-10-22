"""What a property tells us about the person enquiring about it.

Pure logic, no Flask and no database — the same shape as business_rates.py.
app.py owns the models and the routes; this module answers one question:
"somebody has asked about THIS property, so what are they probably looking
for?"

Somebody who rings about a shop at £1,500 a month is looking for a shop, and
is looking at about £1,500 a month. Before this, every requirement field was
left empty and the office typed them in again from the property the enquiry
was already linked to — and because the matcher reads those fields, an
applicant with none of them set matched nothing at all.

Everything here is a STARTING POINT, not a decision:

  - It is only ever used to fill a blank. A figure somebody has typed is
    never overwritten, on an enquiry or on an applicant.
  - Every value is returned with a plain reason, so the record can say where
    it came from and anyone can see it was derived rather than asked for.
  - A property that does not say what it costs produces no budget at all,
    rather than a guess. Likewise its type.

The office is expected to correct these. An applicant who wants a shop at
£1,500 but would go to £2,500 for the right one is a conversation, not a
calculation, and the figures stay editable so that conversation can be
recorded.
"""

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

# ── The budget range ─────────────────────────────────────────────────────────
# How far either side of the asking rent somebody is taken to be looking.
#
# Wider above than below on purpose. Someone enquiring at £1,500 will stretch
# for the right unit more readily than they will drop, and the cost of the two
# errors is not the same: too wide a range shows a property that needs ruling
# out, too narrow a one hides a property nobody ever sees.
BUDGET_BELOW = Decimal('0.80')          # 20% under the asking figure
BUDGET_ABOVE = Decimal('1.25')          # 25% over

# Rounded to something a person would actually say. A range of
# "£1,200 – £1,875" reads as arithmetic; "£1,200 – £1,900" reads as a budget.
# Monthly rents are discussed in hundreds, annual rents and prices in
# thousands.
BUDGET_STEP = {'pcm': Decimal('100'), 'pa': Decimal('1000'),
               'sale': Decimal('1000')}
DEFAULT_STEP = Decimal('100')

# The minimum is rounded DOWN and the maximum UP, so rounding only ever widens
# the range. Rounding a maximum down would exclude the very property the
# enquiry was about, which is the one thing this must never do.

# A unit that means "ask us". There is no figure to work from, so no budget is
# produced for one.
NO_FIGURE_UNITS = {'poa', ''}


def budget_range(amount, unit):
    """The budget range implied by an asking rent or price.

    Returns a dict with min, max, unit and a reason, or None where there is
    nothing to work from. The unit is carried through unchanged: a budget
    derived from a monthly rent is a monthly budget, and saying so is what
    stops it being compared against an annual one later.

    >>> r = budget_range(1500, 'pcm')
    >>> r['min'], r['max']
    (1200.0, 1900.0)
    """
    unit = (unit or '').strip().lower()
    if unit in NO_FIGURE_UNITS:
        return None
    try:
        asking = Decimal(str(amount))
    except Exception:
        return None
    if asking <= 0:
        return None

    step = BUDGET_STEP.get(unit, DEFAULT_STEP)
    low = ((asking * BUDGET_BELOW) / step).to_integral_value(ROUND_FLOOR) * step
    high = ((asking * BUDGET_ABOVE) / step).to_integral_value(ROUND_CEILING) * step

    # A very small figure can floor to nothing. No minimum is more honest than
    # a minimum of zero, which would read as "any property at all".
    if low <= 0:
        low = None

    return {
        'min': float(low) if low is not None else None,
        'max': float(high),
        'unit': unit,
        'asking': float(asking),
        'reason': ('worked out from the advertised figure of '
                   f'{_money(asking)} {unit}, from 20% below to 25% above'),
    }


def _money(value):
    """A figure the way the office writes it, for the reason line only."""
    whole = Decimal(value).quantize(Decimal('1'))
    return f'£{whole:,}'


# ── What kind of property they are looking for ───────────────────────────────
# The property's own type IS the requirement: somebody enquiring about an
# industrial unit is looking for an industrial unit. The map exists only to
# settle spelling, so a type typed differently on an old record still lands on
# one of the five the CRM offers.
#
# Keys are compared lowercased with the punctuation taken out, so
# "Creative / Art Studio", "creative/art studio" and "Creative Art Studio" all
# arrive at the same place.
TYPE_ALIASES = {
    'office': 'Office',
    'offices': 'Office',
    'office suite': 'Office',
    'retail': 'Retail',
    'shop': 'Retail',
    'retail unit': 'Retail',
    'industrial': 'Industrial',
    'warehouse': 'Industrial',
    'industrial unit': 'Industrial',
    'light industrial': 'Light Industrial',
    'workshop': 'Light Industrial',
    'creative art studio': 'Creative / Art Studio',
    'creative studio': 'Creative / Art Studio',
    'art studio': 'Creative / Art Studio',
    'studio': 'Creative / Art Studio',
}


def _flatten(text):
    """Lowercased, punctuation out, one space between words."""
    cleaned = ''.join(ch if ch.isalnum() else ' ' for ch in (text or '').lower())
    return ' '.join(cleaned.split())


def requirement_type(property_type):
    """The property type to record as a requirement, or None.

    Returns None rather than guessing where the property's type is not one the
    CRM knows. "Industrial" and "Light Industrial" are kept apart, because the
    matcher compares them whole and a workshop is not a warehouse.
    """
    key = _flatten(property_type)
    if not key:
        return None
    return TYPE_ALIASES.get(key)


# ── Lease or purchase ────────────────────────────────────────────────────────
def requirement_tenure(unit):
    """Whether they are looking to lease or to buy, from the basis of the price.

    A property priced for sale is being bought; one priced per month or per
    annum is being leased. Anything else is left unsaid.
    """
    unit = (unit or '').strip().lower()
    if unit == 'sale':
        return 'purchase'
    if unit in ('pcm', 'pa'):
        return 'lease'
    return None


# ── The whole profile ────────────────────────────────────────────────────────
# Which fields may be derived at all. Everything else on an applicant's
# requirements — the size range, when they need to be in, what they intend to
# do there — is theirs to tell us and is never inferred from one property.
#
# Deliberately NOT derived: the size. Somebody enquiring about a 1,200 sq ft
# shop has not said they want between 960 and 1,500 sq ft, and a size range
# invented from one unit would rule out the right property at the wrong size,
# which is exactly the mistake the budget range is careful to avoid.
DERIVED_FIELDS = ('req_category', 'req_property_type', 'req_use_class',
                  'req_area', 'req_budget_min', 'req_budget_max',
                  'req_budget_unit', 'req_tenure')


def profile_from_property(property_type=None, use_class=None, category=None,
                          area=None, price=None, price_unit=None):
    """Everything one property implies about the person asking about it.

    Takes plain values rather than a model so this module stays free of the
    database. Returns (fields, reasons): fields is the subset of
    DERIVED_FIELDS that could be worked out, and reasons says in words where
    each one came from, for the record to show.

    A field that cannot be worked out is absent from the dict rather than
    present and empty, so a caller filling blanks never writes a blank over
    something already there.
    """
    fields, reasons = {}, {}

    kind = requirement_type(property_type)
    if kind:
        fields['req_property_type'] = kind
        reasons['req_property_type'] = f'the property is a {kind.lower()} one'

    use = (use_class or '').strip()
    if use:
        fields['req_use_class'] = use
        reasons['req_use_class'] = f'the property’s use class is {use}'

    cat = (category or '').strip()
    if cat:
        fields['req_category'] = cat
        reasons['req_category'] = f'the property is a {cat} one'

    where = (area or '').strip()
    if where:
        fields['req_area'] = where
        reasons['req_area'] = f'the property is in {where}'

    money = budget_range(price, price_unit)
    if money:
        if money['min'] is not None:
            fields['req_budget_min'] = money['min']
        fields['req_budget_max'] = money['max']
        fields['req_budget_unit'] = money['unit']
        reasons['req_budget'] = money['reason']

    tenure = requirement_tenure(price_unit)
    if tenure:
        fields['req_tenure'] = tenure
        reasons['req_tenure'] = (
            'the property is priced for sale' if tenure == 'purchase'
            else 'the property is priced as a rent')

    return fields, reasons


def fill_blanks(record, fields):
    """Write the derived fields onto a record, without overwriting anything.

    Returns the names of the fields actually written. A value already on the
    record — typed by somebody, or derived from an earlier enquiry — is left
    exactly where it is: this only ever answers a question nobody has answered
    yet.
    """
    written = []
    for name, value in fields.items():
        if value in (None, ''):
            continue
        # Only a field the record actually has. An enquiry records the tenure
        # and an applicant does not, and setting an attribute a model has no
        # column for would look as though it had been saved and quietly not
        # be there on the next read.
        if not hasattr(record, name):
            continue
        current = getattr(record, name, None)
        if current in (None, '') or (isinstance(current, str) and not current.strip()):
            setattr(record, name, value)
            written.append(name)
    return written
