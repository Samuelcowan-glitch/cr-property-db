"""Heads of terms, on one page, in the house style.

Subject to contract, and read entirely from records the CRM already holds: the
instruction gives the property and the landlord, the offer gives the rent, the
term, the break, the deposit and the rent-free period, and the transaction
behind it gives the solicitors. Nothing on this page is typed twice, which is
the only way a document like this can be trusted to agree with the file it
came from.

Portrait A4, because heads of terms are read and filed rather than looked at —
the brochure is the landscape one. The typeface, the navy, the rules and the
mark are the brochure's, from particulars.py, so the two read as the same
agency's paper.
"""
import io
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdfcanvas

from particulars import (AGENT, COMPANY, INK, MUTED, NAVY, PANEL_GREY, RULE,
                         clean, draw_logo, face, wrap)

PW, PH = A4                       # 595 × 842 pt — portrait
MARGIN = 48
LABEL_W = 150                     # the column the terms are named in


def _money(value, suffix=''):
    return f'£{value:,.0f}{suffix}' if value else None


def _when(value):
    return f'{value:%d %B %Y}' if value else None


def term_rows(offer):
    """The terms, in the order heads of terms are read in.

    Only what is known: a term with nothing against it is left off rather than
    printed as a dash, because a blank line on heads of terms reads as a point
    still to be agreed when in fact nobody ever entered it.
    """
    project = offer.project
    prop = project.property if project is not None else None
    letting = offer.is_letting
    rows = []

    def add(label, value):
        if value:
            rows.append((label, str(value)))

    add('Property', ', '.join(filter(None, [
        getattr(prop, 'address', None), getattr(prop, 'postcode', None)])))
    if getattr(prop, 'size', None):
        add('Floor area', prop.display_size)

    landlord = project.client_contact if project is not None else None
    if letting:
        add('Landlord', getattr(landlord, 'full_name', None) or getattr(project, 'landlord_name', None))
        add('Tenant', getattr(offer.contact, 'full_name', None))
        add('Rent', _money(offer.rent_pa, ' per annum exclusive'))
        add('Term', offer.display_term)
        add('Commencement', _when(offer.start_date))
        if offer.lease_end:
            add('Expiry', _when(offer.lease_end))
        add('Break clause', offer.break_clause)
        if offer.rent_free_months:
            months = float(offer.rent_free_months)
            whole = int(months)
            said = f'{whole} month' + ('' if whole == 1 else 's') \
                if months == whole else f'{months:g} months'
            add('Rent free', said)
        add('Deposit', _money(offer.deposit))
    else:
        add('Seller', getattr(landlord, 'full_name', None))
        add('Buyer', getattr(offer.contact, 'full_name', None))
        add('Price', _money(offer.amount))
        add('Completion', _when(offer.start_date))
        add('Deposit', _money(offer.deposit))

    # The solicitors are the transaction's, where the offer has become one.
    deal = offer.transaction
    if deal is not None:
        ours = ', '.join(filter(None, [deal.client_solicitor_firm,
                                       deal.client_solicitor]))
        theirs = ', '.join(filter(None, [deal.other_solicitor_firm,
                                         deal.other_solicitor]))
        add("Landlord's solicitor" if letting else "Seller's solicitor", ours)
        add("Tenant's solicitor" if letting else "Buyer's solicitor", theirs)

    add('Conditions', offer.conditions)
    return rows


def _header(c, offer):
    """The mark, the title and the rule under them."""
    top = PH - MARGIN
    draw_logo(c, MARGIN, top - 46, height=46)

    c.setFont(face('semibold'), 15)
    c.setFillColor(NAVY)
    c.drawRightString(PW - MARGIN, top - 14, 'HEADS OF TERMS')

    c.setFont(face('regular'), 9)
    c.setFillColor(MUTED)
    c.drawRightString(PW - MARGIN, top - 29, 'Subject to contract')
    c.drawRightString(PW - MARGIN, top - 41, f'{date.today():%d %B %Y}')

    y = top - 62
    c.setStrokeColor(NAVY)
    c.setLineWidth(2)
    c.line(MARGIN, y, PW - MARGIN, y)
    return y - 26


def _rows(c, rows, y):
    """The terms themselves: a name, a rule, and what was agreed."""
    width = PW - MARGIN * 2
    for label, value in rows:
        lines = wrap(c, value, face('regular'), 10, width - LABEL_W - 12)
        height = max(18, len(lines) * 13 + 6)

        c.setFillColor(PANEL_GREY)
        c.rect(MARGIN, y - height + 12, LABEL_W, height, stroke=0, fill=1)

        c.setFont(face('medium'), 9)
        c.setFillColor(NAVY)
        c.drawString(MARGIN + 8, y, clean(label).upper())

        c.setFont(face('regular'), 10)
        c.setFillColor(INK)
        line_y = y
        for line in lines:
            c.drawString(MARGIN + LABEL_W + 12, line_y, line)
            line_y -= 13

        y -= height
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(MARGIN, y + 10, PW - MARGIN, y + 10)
    return y


def _footer(c):
    """Who to speak to, and the line that says this is not a contract."""
    y = MARGIN + 54
    c.setStrokeColor(RULE)
    c.setLineWidth(0.6)
    c.line(MARGIN, y, PW - MARGIN, y)

    c.setFont(face('medium'), 9)
    c.setFillColor(NAVY)
    c.drawString(MARGIN, y - 14, AGENT['name'])
    c.setFont(face('regular'), 8.5)
    c.setFillColor(INK)
    c.drawString(MARGIN, y - 26, f"{AGENT['mobile']}  ·  {COMPANY['phone']}  ·  {AGENT['email']}")

    c.setFont(face('regular'), 7.5)
    c.setFillColor(MUTED)
    for n, line in enumerate(wrap(
            c,
            'These heads of terms are subject to contract and to the agreement '
            'of formal documentation. They are a summary of what has been '
            'discussed and do not constitute an offer or a binding agreement, '
            'in whole or in part.',
            face('regular'), 7.5, PW - MARGIN * 2)):
        c.drawString(MARGIN, y - 42 - n * 9, line)


def build(offer):
    """The document, as bytes, and what to call the file."""
    buffer = io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=A4)
    project = offer.project
    prop = project.property if project is not None else None
    c.setTitle(f'Heads of Terms — {getattr(prop, "address", "") or project.name}')
    c.setAuthor(COMPANY['name'])

    y = _header(c, offer)
    _rows(c, term_rows(offer), y)
    _footer(c)

    c.showPage()
    c.save()

    where = (getattr(prop, 'address', '') or getattr(project, 'name', 'Property'))
    stub = ''.join(ch if ch.isalnum() or ch in ' -' else '' for ch in where).strip()
    stub = '-'.join(stub.split())[:60] or 'Property'
    return buffer.getvalue(), f'Heads-of-Terms-{stub}-{date.today():%Y-%m-%d}.pdf'
