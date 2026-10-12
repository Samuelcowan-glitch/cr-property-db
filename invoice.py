"""The Cowan & Rutter invoice.

The layout is not a new design: every measurement here was read off the firm's
own invoice (Invoice.40052C_KC, 591-593 Kings Road) so a generated invoice
lands on the page exactly where a typed one does. The navy letterhead is the
artwork lifted whole out of that file, drawn full-bleed across the top, so the
logo, the agency line, the phone numbers and the address are the originals
rather than a redrawing of them.

Fixed for every invoice, and not reachable from the CRM: the letterhead, the
company name, the registration number, the VAT number and the bank details.
What changes is the reference, the date, who it is addressed to, what it is
for and the money.

Type: the original is set in Century Gothic, with the footer in Candara. Those
are licensed Microsoft faces and are not in this repository -- drop the files
into static/fonts (see the README there) and every invoice picks them up. Until
then the closest face ReportLab ships with is used, which keeps the layout and
the text identical and changes only the shapes of the letters.
"""
import io
import os
import re
from datetime import date

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, 'static', 'fonts')
LETTERHEAD = os.path.join(HERE, 'static', 'img', 'invoice-letterhead.jpg')

PAGE_W, PAGE_H = A4                      # 595 x 842 pt, as the original
INK = HexColor('#000000')

# Where the letterhead sits. The original bleeds a point off the left edge and
# runs the full width, so it is drawn the same way rather than inset.
HEAD_X, HEAD_W, HEAD_H = -1.0, 596.88, 160.95

LEFT = 71.0                              # the left margin every line starts on
MONEY_X = 395.2                          # where the figures line up
PAYABLE_X = 251.1                        # the amount payable sits further left

# Baselines, measured from the top of the page as the original has them. They
# are converted to ReportLab's bottom-up y when drawn.
Y_TITLE = 198.4                          # INVOICE
Y_REF = 210.6                            # CL / 40052C/ KC
Y_DATE = 234.4
Y_TO = 282.1                             # first line of the address block
Y_RE = 389.2                             # Re: <property>
Y_BODY = 425.7                           # the description
Y_FEE = 473.2
Y_NET = 520.9
Y_VAT = 556.7
Y_PAYABLE = 616.5
Y_FOOT = 764.2                           # first footer line

LINE_GAP = 12.0                          # the address block's line spacing
BODY_GAP = 11.7                          # the description's line spacing
FOOT_GAP = 12.15

SIZE = 10.0
SMALL = 6.5                              # the ordinal in "4th" and "1st"

# Never variable. These are the firm's own details and the CRM has no way to
# change them.
FOOTER_LINES = [
    'Cowan & Rutter Property Services Limited',
    'Company Registration No. 07851737',
    'VAT No 191 5161 13',
    'Bank Details: 20632643    Sort Code: 20-47-34',
]


# ── Type ─────────────────────────────────────────────────────────────────────

def register_fonts():
    """Use the real faces if they are here, and say so if they are not.

    Century Gothic and Candara are licensed Microsoft fonts, so the files are
    not in this repository. Drop them into static/fonts and every invoice is
    set in them from the next one onwards, with no other change.
    """
    wanted = {
        'CenturyGothic': ('Century Gothic', 'CenturyGothic', 'GOTHIC'),
        'CenturyGothic-Bold': ('Century Gothic Bold', 'CenturyGothic-Bold',
                               'GOTHICB'),
        'Candara-Bold': ('Candara Bold', 'Candara-Bold', 'Candarab'),
    }
    found = {}
    for name, stems in wanted.items():
        for stem in stems:
            for ext in ('ttf', 'otf', 'TTF'):
                path = os.path.join(FONT_DIR, f'{stem}.{ext}')
                if os.path.exists(path):
                    try:
                        pdfmetrics.registerFont(TTFont(name, path))
                        found[name] = True
                    except Exception:
                        pass
                    break
            if name in found:
                break
    return found


_FOUND = register_fonts()
HAVE_GOTHIC = 'CenturyGothic' in _FOUND and 'CenturyGothic-Bold' in _FOUND
HAVE_CANDARA = 'Candara-Bold' in _FOUND


def body_face(bold=False):
    """The face the invoice body is set in."""
    if HAVE_GOTHIC:
        return 'CenturyGothic-Bold' if bold else 'CenturyGothic'
    return 'Helvetica-Bold' if bold else 'Helvetica'


def footer_face():
    """The face the footer is set in."""
    return 'Candara-Bold' if HAVE_CANDARA else 'Helvetica-Bold'


# ── Small helpers ────────────────────────────────────────────────────────────

def clean(value):
    """Text fit to draw: never None, no stray whitespace."""
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def money(amount):
    """A figure the way the invoice writes it: £5,175.00."""
    try:
        return f'£{float(amount):,.2f}'
    except (TypeError, ValueError):
        return ''


def invoice_date(value=None):
    """The date the way the invoice writes it: 4th December 2025.

    The ordinal is drawn raised and small, as the original has it, so this
    returns the number and the suffix separately.
    """
    when = value or date.today()
    day = when.day
    if 11 <= day % 100 <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    return str(day), suffix, when.strftime('%B %Y')


def _wrap(text, font, size, width):
    """Break a paragraph to the measured width, as the original wraps it."""
    words = clean(text).split(' ')
    lines, line = [], ''
    for word in words:
        trial = f'{line} {word}'.strip()
        if line and pdfmetrics.stringWidth(trial, font, size) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def _at(y):
    """A baseline measured from the top, in ReportLab's bottom-up space."""
    return PAGE_H - y


def _ordinal_run(c, x, y, number, suffix, rest, bold=False):
    """Draw "4th December 2025" with the ordinal raised, as the original does.

    Returns where the run ends, so a caller can carry on from it.
    """
    face = body_face(bold)
    c.setFont(face, SIZE)
    c.drawString(x, _at(y), number)
    x += pdfmetrics.stringWidth(number, face, SIZE)
    c.setFont(face, SMALL)
    c.drawString(x, _at(y) + 2.6, suffix)
    x += pdfmetrics.stringWidth(suffix, face, SMALL)
    if rest:
        c.setFont(face, SIZE)
        c.drawString(x, _at(y), rest)
        x += pdfmetrics.stringWidth(rest, face, SIZE)
    return x


def _underline(c, x, y, text, font, size):
    """The rule under "Re:" and "Amount payable", as the original has it."""
    width = pdfmetrics.stringWidth(text, font, size)
    c.setLineWidth(0.7)
    c.line(x, _at(y) - 1.6, x + width, _at(y) - 1.6)


# ── The invoice ──────────────────────────────────────────────────────────────

def build_invoice(data):
    """Render one invoice and hand back the PDF bytes.

    `data` carries only what changes from invoice to invoice:
        reference       e.g. 'CL / 40052C/ KC'
        issued_on       a date; today if missing
        client_name     who it is addressed to
        client_address  the rest of the address, one line per line
        property_line   what follows "Re:"
        description     the paragraph explaining the fee
        fee_label       e.g. 'Our fee calculated at 7.5% of the annual rental'
        net             the fee before VAT
        vat_rate        20 unless told otherwise
        vat             the VAT; worked out from net and vat_rate if missing
        total           the amount payable; net + vat if missing
    """
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Invoice {clean(data.get('reference')) or ''}".strip())
    c.setAuthor('Cowan & Rutter Property Services Limited')
    c.setFillColor(INK)

    # The letterhead, whole and unaltered.
    if os.path.exists(LETTERHEAD):
        c.drawImage(ImageReader(LETTERHEAD), HEAD_X, PAGE_H - HEAD_H,
                    width=HEAD_W, height=HEAD_H, mask='auto')

    reg, bold = body_face(False), body_face(True)

    # INVOICE, centred over the page as the original has it.
    c.setFont(bold, SIZE)
    c.drawCentredString(PAGE_W / 2, _at(Y_TITLE), 'INVOICE')

    reference = clean(data.get('reference'))
    if reference:
        c.setFont(reg, SIZE)
        c.drawCentredString(PAGE_W / 2, _at(Y_REF), reference)

    # The date, with its raised ordinal.
    number, suffix, rest = invoice_date(data.get('issued_on'))
    _ordinal_run(c, LEFT, Y_DATE, number, suffix, f' {rest}')

    # Who it is addressed to.
    c.setFont(reg, SIZE)
    y = Y_TO
    address = [clean(data.get('client_name'))]
    raw = data.get('client_address') or ''
    if isinstance(raw, str):
        raw = raw.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    address += [clean(part) for part in raw]
    for line in [a for a in address if a]:
        c.drawString(LEFT, _at(y), line)
        y += LINE_GAP

    # Re: the property, bold and underlined.
    prop = clean(data.get('property_line'))
    if prop:
        text = f'Re: {prop}'
        c.setFont(bold, SIZE)
        c.drawString(LEFT, _at(Y_RE), text)
        _underline(c, LEFT, Y_RE, text, bold, SIZE)

    # What the fee is for. The original runs to two lines; a longer one is
    # allowed to push the money down the page rather than being written over
    # the fee line.
    c.setFont(reg, SIZE)
    y = Y_BODY
    for line in _wrap(data.get('description'), reg, SIZE, 455):
        c.drawString(LEFT, _at(y), line)
        y += BODY_GAP
    # Two lines is what the template leaves room for; anything beyond that
    # shifts the figures down by the same amount, keeping the gaps between them.
    overflow = max(0.0, y - (Y_BODY + 2 * BODY_GAP))

    # The money. Each label on the left, each figure on the column.
    net = data.get('net')
    try:
        vat_rate = float(data.get('vat_rate') if data.get('vat_rate') not in
                         (None, '') else 20)
    except (TypeError, ValueError):
        vat_rate = 20.0
    vat = data.get('vat')
    if vat in (None, '') and net not in (None, ''):
        vat = round(float(net) * vat_rate / 100.0, 2)
    total = data.get('total')
    if total in (None, '') and net not in (None, ''):
        total = round(float(net) + float(vat or 0), 2)

    fee_label = clean(data.get('fee_label')) or 'Our fee'
    c.setFont(reg, SIZE)
    c.drawString(LEFT, _at(Y_FEE + overflow), fee_label)
    c.drawString(MONEY_X, _at(Y_FEE + overflow), money(net))

    c.drawString(LEFT, _at(Y_NET + overflow), 'Total before VAT')
    c.drawString(MONEY_X, _at(Y_NET + overflow), money(net))

    rate_text = f'{vat_rate:g}'
    c.drawString(LEFT, _at(Y_VAT + overflow), f'VAT @ {rate_text}%')
    c.drawString(MONEY_X, _at(Y_VAT + overflow), money(vat))

    c.setFont(bold, SIZE)
    c.drawString(LEFT, _at(Y_PAYABLE + overflow), 'Amount payable')
    _underline(c, LEFT, Y_PAYABLE + overflow, 'Amount payable', bold, SIZE)
    payable = money(total)
    c.drawString(PAYABLE_X, _at(Y_PAYABLE + overflow), payable)
    _underline(c, PAYABLE_X, Y_PAYABLE + overflow, payable, bold, SIZE)

    # The firm's own details, centred, identical on every invoice.
    foot = footer_face()
    c.setFont(foot, SIZE)
    y = Y_FOOT
    for line in FOOTER_LINES:
        c.drawCentredString(PAGE_W / 2, _at(y), line)
        y += FOOT_GAP

    c.showPage()
    c.save()
    return buf.getvalue()


def invoice_filename(data):
    """A filename that says what the invoice is, the way the firm names them."""
    bits = [clean(data.get('reference')).replace('/', '').replace(' ', '.'),
            clean(data.get('property_line'))[:60]]
    stem = '.'.join(b for b in bits if b) or 'Invoice'
    stem = re.sub(r'[^A-Za-z0-9._-]+', '.', stem).strip('.')
    return f'Invoice.{stem}.pdf'[:150]
