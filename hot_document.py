"""The Cowan & Rutter Heads of Terms.

The layout is not a new design: every measurement was read off the firm's own
document (Heads of Terms - 1 Stanley Bridge Studios), so a generated set lands
on the page where a typed one does -- the cover, the parties page, then the
terms running one bold heading and its paragraph at a time.

The original is set in Liberation Serif, which is metric-compatible with Times
New Roman, and so is ReportLab's built-in Times. The type therefore matches
without shipping a font file.

Nothing here invents a term. A section the CRM has no answer for is left for
the user to write on the review page, and a section left empty is not printed.
"""
import io
import re

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as pdfcanvas

PAGE_W, PAGE_H = A4

SERIF, SERIF_BOLD = 'Times-Roman', 'Times-Bold'

LEFT = 82.0                     # the margin every term starts on
RIGHT = 82.0
TEXT_W = PAGE_W - LEFT - RIGHT

# The cover, centred, as the original has it.
COVER_TITLE_Y, COVER_TITLE_SIZE = 198.3, 22.0
COVER_RESPECT_Y, COVER_RESPECT_SIZE = 308.3, 18.0
COVER_ADDR_Y, COVER_ADDR_SIZE, COVER_ADDR_GAP = 394.6, 13.7, 18.0
COVER_FOOT_Y, COVER_FOOT_SIZE, COVER_FOOT_GAP = 633.2, 18.0, 23.0
COVER_FOOTER = ['SUBJECT TO CONTRACT', 'AND', 'WITHOUT PREJUDICE']

# The parties page.
PARTY_TOP, PARTY_SIZE = 170.9, 12.0
PARTY_LINE_GAP = 14.0           # within a block
PARTY_HEAD_GAP = 14.9           # heading to its first line
PARTY_BLOCK_GAP = 34.1          # last line of a block to the next heading

# The terms pages.
TERM_TOP, TERM_SIZE = 91.6, 13.7
TERM_LINE_GAP = 16.0
TERM_HEAD_GAP = 16.0            # heading to its first line
TERM_BLOCK_GAP = 28.0           # last line of a term to the next heading
TERM_BOTTOM = 760.0             # start a new page rather than run past this

# The order the original sets the terms out in. A section with nothing in it
# is skipped rather than printed empty.
TERM_ORDER = [
    ('demise', 'Demise'),
    ('lease', 'Lease'),
    ('completion', 'Indicative Target Completion / Lease Commencement'),
    ('rent', 'Rent'),
    ('term', 'Term'),
    ('rent_review', 'Rent review'),
    ('rent_deposit', 'Rent Deposit'),
    ('rent_free', 'Rent free'),
    ('insurance', 'Insurance'),
    ('repair', 'Repairing Obligations:'),
    ('alterations', 'Alterations:'),
    ('alienation', 'Alienation:'),
    ('service_charge', 'Service Charge, Business Rates and Utilities'),
    ('vat', 'VAT'),
    ('access', 'Access'),
    ('permitted_use', 'Permitted Use'),
    ('legal_costs', 'Legal Costs'),
    ('lt_act', 'Landlord & Tenant Act 1954'),
]

PARTY_ORDER = [
    ('lessor', 'Lessor'),
    ('lessee', 'Lessee'),
    ('lessor_solicitor', "Lessor's Solicitor"),
    ('lessee_solicitor', "Lessee's Solicitor"),
]

DEALT_WITH_HEADING = 'This matter is being dealt with by'


def clean(value):
    """Text fit to draw: never None, no stray whitespace."""
    return re.sub(r'[ \t]+', ' ', str(value or '')).strip()


def lines_of(value):
    """A block of text as a list of lines, blank ones dropped."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = str(value).replace('\r\n', '\n').replace('\r', '\n').split('\n')
    return [clean(p) for p in parts if clean(p)]


def _wrap(text, font, size, width):
    """Break a paragraph to the measured width, as the original wraps it."""
    out = []
    for para in lines_of(text):
        words, line = para.split(' '), ''
        for word in words:
            trial = f'{line} {word}'.strip()
            if line and pdfmetrics.stringWidth(trial, font, size) > width:
                out.append(line)
                line = word
            else:
                line = trial
        if line:
            out.append(line)
    return out


class _Doc:
    """A canvas that knows where it is on the page and when to turn over."""

    def __init__(self, buf):
        self.c = pdfcanvas.Canvas(buf, pagesize=A4)
        self.y = TERM_TOP

    def _at(self, y):
        return PAGE_H - y

    def new_page(self, start=TERM_TOP):
        self.c.showPage()
        self.y = start

    def room_for(self, line_count, size):
        """Whether a heading and this many lines still fit on the page."""
        needed = TERM_HEAD_GAP + line_count * TERM_LINE_GAP
        return self.y + needed <= TERM_BOTTOM

    def heading(self, text, size=TERM_SIZE):
        self.c.setFont(SERIF_BOLD, size)
        self.c.drawString(LEFT, self._at(self.y), text)

    def body(self, lines, gap=TERM_LINE_GAP, size=TERM_SIZE):
        self.c.setFont(SERIF, size)
        for line in lines:
            self.y += gap
            self.c.drawString(LEFT, self._at(self.y), line)

    def centred(self, text, y, font, size):
        self.c.setFont(font, size)
        self.c.drawCentredString(PAGE_W / 2, self._at(y), text)


def build_heads_of_terms(data):
    """Render the Heads of Terms and hand back the PDF bytes.

    `data` carries only what changes between deals:
        property_address  the lines on the cover
        lessor / lessee / lessor_solicitor / lessee_solicitor
                          each a block of lines, name first then address
        the keys in TERM_ORDER, each a paragraph
        dealt_with        the agent's lines, and the date
    """
    buf = io.BytesIO()
    doc = _Doc(buf)
    c = doc.c
    c.setTitle('Heads of Terms')
    c.setAuthor('Cowan & Rutter')

    # ── The cover ────────────────────────────────────────────────────────
    doc.centred('HEADS OF TERMS', COVER_TITLE_Y, SERIF_BOLD, COVER_TITLE_SIZE)
    doc.centred('In respect of', COVER_RESPECT_Y, SERIF_BOLD, COVER_RESPECT_SIZE)
    y = COVER_ADDR_Y
    for line in lines_of(data.get('property_address')):
        doc.centred(line, y, SERIF, COVER_ADDR_SIZE)
        y += COVER_ADDR_GAP
    y = COVER_FOOT_Y
    for line in COVER_FOOTER:
        doc.centred(line, y, SERIF_BOLD, COVER_FOOT_SIZE)
        y += COVER_FOOT_GAP

    # ── The parties ──────────────────────────────────────────────────────
    doc.new_page(PARTY_TOP)
    first = True
    for key, label in PARTY_ORDER:
        block = lines_of(data.get(key))
        if not block:
            continue
        if not first:
            doc.y += PARTY_BLOCK_GAP
        first = False
        if doc.y + PARTY_HEAD_GAP + len(block) * PARTY_LINE_GAP > TERM_BOTTOM:
            doc.new_page(PARTY_TOP)
        doc.heading(label, PARTY_SIZE)
        doc.y += PARTY_HEAD_GAP - PARTY_LINE_GAP      # the first line follows
        doc.body(block, PARTY_LINE_GAP, PARTY_SIZE)

    # ── The terms ────────────────────────────────────────────────────────
    doc.new_page(TERM_TOP)
    first = True
    for key, label in TERM_ORDER:
        body = _wrap(data.get(key), SERIF, TERM_SIZE, TEXT_W)
        if not body:
            continue
        if not first:
            doc.y += TERM_BLOCK_GAP
        first = False
        if not doc.room_for(len(body), TERM_SIZE):
            doc.new_page(TERM_TOP)
        doc.heading(label)
        doc.body(body)

    # Who is dealing with it, and the date, as the original closes.
    tail = lines_of(data.get('dealt_with'))
    if tail:
        doc.y += TERM_BLOCK_GAP
        if not doc.room_for(len(tail), TERM_SIZE):
            doc.new_page(TERM_TOP)
        doc.heading(DEALT_WITH_HEADING)
        doc.body(tail)

    c.showPage()
    c.save()
    return buf.getvalue()


def heads_of_terms_filename(data):
    """A filename that says which property the terms are for."""
    first = (lines_of(data.get('property_address')) or ['Property'])[0]
    stem = re.sub(r'[^A-Za-z0-9._-]+', '.', clean(first)).strip('.')
    return f'Heads.of.Terms.{stem}.pdf'[:150]
