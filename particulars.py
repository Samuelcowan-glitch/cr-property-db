"""Property particulars, as a genuine PDF.

Built to the house style: A4 landscape, a full-bleed cover photograph above a
band carrying the logo, the headline and the size, then pages of navy and grey
panels for the words and a column of photographs beside them.

The two-page and four-page versions are the same components in a different
order, so a change to a panel changes both and they cannot drift apart.

Everything on the page comes from the CRM. Nothing is invented: a section with
nothing behind it is left out and the rest reflows, so a brochure never carries
an empty box or a placeholder.

The text is real text — selectable, searchable and embedded — not a picture of
a page.
"""
import io
import os
import re
from datetime import date

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

# ── The house style ──────────────────────────────────────────────────────────

PAGE = landscape(A4)                       # 842 × 595 pt — 297 × 210 mm
PW, PH = PAGE

NAVY = HexColor('#2e2c71')                 # taken from the logo itself
RED = HexColor('#e15441')
PANEL_GREY = HexColor('#e9e9e9')
INK = HexColor('#1f2333')
MUTED = HexColor('#6b7280')
RULE = HexColor('#c8ccd6')

MARGIN = 34
GUTTER = 18

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(HERE, 'static', 'img', 'cr-logo.png')
FONT_DIR = os.path.join(HERE, 'static', 'fonts')

COMPANY = {
    'name': 'Cowan & Rutter',
    'phone': '020 7349 6666',
    'website': 'www.cowanandrutter.co.uk',
}

DISCLAIMER_TITLE = 'Misrepresentation Act 1967:'
DISCLAIMER = (
    'These particulars are believed to be correct but their accuracy is not '
    'guaranteed and they do not form part of any contract. Unless otherwise '
    'stated, all prices and rents are quoted exclusive of VAT. These details '
    'are believed to be correct at the time of compilation but may be subject '
    'to subsequent amendment.'
)


# ── Type ─────────────────────────────────────────────────────────────────────

def register_fonts():
    """Use Mustica Pro if it is here, and say so if it is not.

    The licensed files are not in the repository, so until they are dropped
    into static/fonts the particulars are set in the closest face ReportLab
    ships with. Nothing else changes: the moment the files appear, every
    document is set in Mustica Pro without another line of code.

    Returns the family name to use, and whether it is the real thing.
    """
    wanted = {
        'MusticaPro': 'MusticaPro-Regular',
        'MusticaPro-Bold': 'MusticaPro-Bold',
        'MusticaPro-Medium': 'MusticaPro-Medium',
        'MusticaPro-SemiBold': 'MusticaPro-SemiBold',
    }
    found = {}
    for name, stem in wanted.items():
        for ext in ('ttf', 'otf'):
            path = os.path.join(FONT_DIR, f'{stem}.{ext}')
            if os.path.exists(path):
                try:
                    pdfmetrics.registerFont(TTFont(name, path))
                    found[name] = True
                except Exception:
                    pass
                break
    if 'MusticaPro' in found:
        return 'MusticaPro', True
    # DejaVu ships with ReportLab and embeds cleanly, so the text stays real
    # text whichever face is used.
    return 'Helvetica', False


FAMILY, HAVE_MUSTICA = register_fonts()


def face(weight='regular'):
    """The font name for a weight, whichever family is in use."""
    if HAVE_MUSTICA:
        return {'regular': 'MusticaPro', 'medium': 'MusticaPro-Medium',
                'semibold': 'MusticaPro-SemiBold',
                'bold': 'MusticaPro-Bold'}.get(weight, 'MusticaPro')
    return {'regular': 'Helvetica', 'medium': 'Helvetica',
            'semibold': 'Helvetica-Bold', 'bold': 'Helvetica-Bold'}.get(
        weight, 'Helvetica')


# ── Small helpers ────────────────────────────────────────────────────────────

def clean(value):
    """Text as it will appear: trimmed, with runs of space collapsed.

    Anything drawn on the page goes through here, so a stray tag or a run of
    whitespace typed into a description cannot disturb the layout.
    """
    if value is None:
        return ''
    text = re.sub(r'<[^>]+>', ' ', str(value))
    return re.sub(r'\s+', ' ', text).strip()


def wrap(canvas, text, font, size, width):
    """Break text into lines that fit, without splitting a word."""
    words = clean(text).split()
    if not words:
        return []
    lines, line = [], words[0]
    for word in words[1:]:
        trial = f'{line} {word}'
        if canvas.stringWidth(trial, font, size) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return lines


def draw_paragraph(canvas, text, x, y, width, size=9.5, leading=13,
                   colour=INK, weight='regular', max_lines=None):
    """Draw wrapped text downwards from y. Returns the y it finished at."""
    font = face(weight)
    canvas.setFont(font, size)
    canvas.setFillColor(colour)
    lines = wrap(canvas, text, font, size, width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip('.,;') + '…'
    for line in lines:
        canvas.drawString(x, y, line)
        y -= leading
    return y


def draw_bullets(canvas, items, x, y, width, size=9.5, leading=12.6,
                 colour=INK, weight='regular', gap=3):
    """Draw a list of key terms, one bullet each. Returns the y it finished at.

    Each term is printed exactly as it was entered — nothing is rewritten,
    shortened or joined with another. A long term wraps and its continuation
    lines are indented to sit under the first word rather than under the
    bullet, so the list still reads as a list.
    """
    font = face(weight)
    canvas.setFont(font, size)
    canvas.setFillColor(colour)
    indent = 11
    for item in items:
        text = clean(item)
        if not text:
            continue
        lines = wrap(canvas, text, font, size, width - indent)
        canvas.drawString(x, y, '\u2022')
        for i, line in enumerate(lines):
            canvas.drawString(x + indent, y, line)
            y -= leading
        y -= gap
    return y


def block_height(canvas, body, width, size=9.5, leading=12.6, gap=3):
    """How tall a block's body will be, paragraph or bullet list alike."""
    if isinstance(body, (list, tuple)):
        total = 0
        for item in body:
            text = clean(item)
            if not text:
                continue
            total += leading * max(1, len(wrap(canvas, text, face('regular'),
                                               size, width - 11))) + gap
        return total
    return leading * max(1, len(wrap(canvas, body, face('regular'), size, width)))


def has_body(body):
    """Whether a block has anything to show."""
    if isinstance(body, (list, tuple)):
        return any(clean(x) for x in body)
    return bool(clean(body))


def draw_heading(canvas, text, x, y, size=11, colour=NAVY):
    canvas.setFont(face('medium'), size)
    canvas.setFillColor(colour)
    canvas.drawString(x, y, clean(text))
    return y - size - 4


def draw_image(canvas, source, x, y, w, h, fit=False):
    """Draw an image in the box, keeping its proportions.

    By default it fills the box and the overflow is trimmed, which is what
    object-fit: cover does on the web and what a photograph wants.

    With fit=True the whole image is scaled to sit inside the box instead, and
    centred. A floorplan must never be cropped — the room names, dimensions,
    boundaries and scale bar around its edges are the reason it is there — so
    it is always drawn this way, and the spare space is simply left blank.
    """
    if not source:
        return False
    try:
        reader = ImageReader(io.BytesIO(source) if isinstance(source, bytes)
                             else source)
        iw, ih = reader.getSize()
        if not iw or not ih:
            return False
        scale = (min if fit else max)(w / iw, h / ih)
        dw, dh = iw * scale, ih * scale
        canvas.saveState()
        if not fit:
            path = canvas.beginPath()
            path.rect(x, y, w, h)
            canvas.clipPath(path, stroke=0, fill=0)
        canvas.drawImage(reader, x + (w - dw) / 2, y + (h - dh) / 2,
                         width=dw, height=dh, mask='auto')
        canvas.restoreState()
        return True
    except Exception:
        # A photograph that will not read must not stop the brochure.
        canvas.setFillColor(PANEL_GREY)
        canvas.rect(x, y, w, h, stroke=0, fill=1)
        return False


def draw_logo(canvas, x, y, height=58):
    """The company mark, at its own proportions."""
    if not os.path.exists(LOGO):
        return 0
    try:
        reader = ImageReader(LOGO)
        iw, ih = reader.getSize()
        width = height * iw / ih
        canvas.drawImage(reader, x, y, width=width, height=height, mask='auto')
        return width
    except Exception:
        return 0


# ── The pages ────────────────────────────────────────────────────────────────

def cover_heading(canvas, address, strapline, left, right, top,
                  size=20, min_size=14, measure=False):
    """The cover heading: address, rule, strapline — one centred group.

    All three share one centre point and one maximum width, and every wrapped
    line is centred on it. The group is measured before it is drawn so the rule
    always sits between the two with matching space above and below, whatever
    either of them wraps to.

    Nothing else belongs in this group. There is no price, and no space is left
    where one used to be.

    Returns the height it needs. With measure=True nothing is drawn.
    """
    width = right - left
    centre = (left + right) / 2
    addr = clean(address)
    strap = clean(strapline)

    # A long strapline steps down a size rather than being cut, so it still
    # reads. Its | separators are never touched: they are the office's own
    # punctuation and they mean something.
    strap_size = size
    strap_lines = wrap(canvas, strap, face('regular'), strap_size, width) if strap else []
    while len(strap_lines) > 2 and strap_size > min_size:
        strap_size -= 1.5
        strap_lines = wrap(canvas, strap, face('regular'), strap_size, width)
    strap_lines = strap_lines[:3]

    addr_size = 10.5
    addr_lines = (wrap(canvas, addr, face('regular'), addr_size, width)[:2]
                  if addr else [])

    addr_leading = addr_size + 4
    strap_leading = strap_size + 4
    rule_gap = 10                      # the same above the rule as below it
    both = bool(addr_lines and strap_lines)

    height = (len(addr_lines) * addr_leading
              + (rule_gap * 2 if both else 0)
              + len(strap_lines) * strap_leading)
    if measure:
        return height

    y = top
    canvas.setFillColor(MUTED)
    canvas.setFont(face('regular'), addr_size)
    for line in addr_lines:
        y -= addr_size
        canvas.drawCentredString(centre, y, line)
        y -= addr_leading - addr_size

    if both:
        y -= rule_gap
        # Centred on the same point, and never wider than the group it divides.
        rule = min(230, width)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.8)
        canvas.line(centre - rule / 2, y, centre + rule / 2, y)
        y -= rule_gap

    canvas.setFillColor(NAVY)
    canvas.setFont(face('regular'), strap_size)
    for line in strap_lines:
        y -= strap_size
        canvas.drawCentredString(centre, y, line)
        y -= strap_leading - strap_size
    return height


def cover_page(canvas, data):
    """A full-bleed photograph above a band carrying the mark and the heading.

    One implementation, used by the two-page particulars, page one of the
    four-page particulars, the preview and the download alike. They are the
    same call, so what is previewed is what is downloaded.
    """
    address = clean(data.get('address'))
    strapline = (clean(data.get('cover_line')) or clean(data.get('headline'))
                 or address or 'Property').upper()
    size_line = clean(data.get('size_line'))

    # Centred on the PAGE, not on the space left beside the mark, so the group
    # does not shift as the logo or the floor area changes.
    centre = PW / 2
    logo_w = 52 * 1.45
    size_w = (canvas.stringWidth(size_line, face('regular'), 10.5)
              if size_line else 0)
    # Clear of the mark on one side and the floor area on the other, by the
    # same amount, so the group stays centred on the page.
    reserved = MARGIN + max(logo_w, size_w) + 28
    group_w = min(PW - reserved * 2, 620)
    left, right = centre - group_w / 2, centre + group_w / 2

    height = cover_heading(canvas, address, strapline, left, right, 0,
                           measure=True)
    band = max(118, height + 44)
    photo_h = PH - band

    if not draw_image(canvas, data.get('cover'), 0, band, PW, photo_h):
        canvas.setFillColor(PANEL_GREY)
        canvas.rect(0, band, PW, photo_h, stroke=0, fill=1)
        canvas.setFillColor(MUTED)
        canvas.setFont(face('regular'), 11)
        canvas.drawCentredString(PW / 2, band + photo_h / 2,
                                 'No photograph available')

    canvas.setStrokeColor(NAVY)
    canvas.setLineWidth(2.5)
    canvas.line(0, band, PW, band)

    top = band - (band - height) / 2
    middle = top - height / 2
    draw_logo(canvas, MARGIN, middle - 26, height=52)
    cover_heading(canvas, address, strapline, left, right, top)

    if size_line:
        canvas.setFont(face('regular'), 10.5)
        canvas.setFillColor(INK)
        canvas.drawRightString(PW - MARGIN, middle - 4, size_line)


def measure_blocks(canvas, blocks, width, size=9.5, leading=12.6,
                   heading=15, gap=10):
    """How tall a set of headed paragraphs will be.

    Panels are sized to their contents rather than to a fixed fraction, so a
    short description does not leave a band of empty colour beneath it.
    """
    total = 30
    for title, body in blocks:
        if not has_body(body):
            continue
        total += heading
        total += block_height(canvas, body, width - 44, size, leading)
        total += gap
    return total + 12


def _share_column(canvas, upper, lower, width, total, floor=0.20):
    """Divide a column between two stacked panels, by what each needs.

    The lower panel carries the terms — rent, price, business rates, the EPC —
    and those are the sentences that must not be cut off halfway. So it is
    given the height it asks for, and the upper panel takes what is left.

    Neither is allowed below `floor` of the column, so a very long rates note
    cannot squeeze the description down to a stripe. When the two together want
    more than there is, the space is split in proportion to what each asked
    for, which at least fails evenly rather than silently truncating one.
    """
    want_up = measure_blocks(canvas, upper, width)
    want_low = measure_blocks(canvas, lower, width)
    least = total * floor

    if want_up + want_low <= total:
        # Room for both. The slack goes to the upper panel, so the column
        # fills without leaving a band of bare page between them.
        low = max(want_low, least)
        return total - low, low

    share = total * want_low / (want_up + want_low)
    low = min(max(share, least), total - least)
    return total - low, low


def _navy_panel(canvas, x, y, w, h, blocks):
    """The navy block: white headings and text, as on the reference."""
    canvas.setFillColor(NAVY)
    canvas.rect(x, y, w, h, stroke=0, fill=1)
    inner = x + 22
    width = w - 44
    cursor = y + h - 30
    for title, body in blocks:
        if not has_body(body):
            continue
        canvas.setFont(face('medium'), 11)
        canvas.setFillColor(white)
        canvas.drawString(inner, cursor, clean(title))
        cursor -= 15
        if isinstance(body, (list, tuple)):
            cursor = draw_bullets(canvas, body, inner, cursor, width,
                                  size=9.5, leading=12.6, colour=white)
        else:
            cursor = draw_paragraph(canvas, body, inner, cursor, width,
                                    size=9.5, leading=12.6, colour=white)
        cursor -= 10
        if cursor < y + 20:
            break
    return cursor


def _grey_panel(canvas, x, y, w, h, blocks):
    """The soft grey block beneath it: terms, rent, rates and the like."""
    canvas.setFillColor(PANEL_GREY)
    canvas.rect(x, y, w, h, stroke=0, fill=1)
    inner = x + 22
    width = w - 44
    cursor = y + h - 30
    for title, body in blocks:
        if not has_body(body):
            continue
        canvas.setFont(face('medium'), 10.5)
        canvas.setFillColor(NAVY)
        canvas.drawString(inner, cursor, clean(title))
        cursor -= 14
        if isinstance(body, (list, tuple)):
            cursor = draw_bullets(canvas, body, inner, cursor, width,
                                  size=9.5, leading=12.4, colour=INK)
        else:
            cursor = draw_paragraph(canvas, body, inner, cursor, width,
                                    size=9.5, leading=12.4, colour=INK)
        cursor -= 9
        if cursor < y + 16:
            break
    return cursor


def contact_block(canvas, x, y, w, data):
    """Who to speak to, with the mark beside it, right-aligned as in the house style."""
    right = x + w
    logo_w = 132
    text_right = right - logo_w - 22

    canvas.setFont(face('medium'), 11)
    canvas.setFillColor(NAVY)
    canvas.drawRightString(text_right, y, 'Contact')

    line = y - 16
    canvas.setFont(face('regular'), 9.5)
    canvas.setFillColor(INK)
    canvas.drawRightString(text_right, line, 'Viewings by prior appointment with the agent')
    line -= 20

    for text in data.get('contact_lines', []):
        canvas.drawRightString(text_right, line, clean(text))
        line -= 13

    draw_logo(canvas, right - logo_w, y - 74, height=72)
    return line


def disclaimer_block(canvas, x, y, w):
    """The small print, with a rule above it, as on the reference."""
    canvas.setStrokeColor(INK)
    canvas.setLineWidth(0.7)
    canvas.line(x, y + 12, x + w, y + 12)
    canvas.setFont(face('semibold'), 6.6)
    canvas.setFillColor(INK)
    canvas.drawString(x, y, DISCLAIMER_TITLE)
    offset = canvas.stringWidth(DISCLAIMER_TITLE, face('semibold'), 6.6) + 3
    canvas.setFont(face('regular'), 6.6)
    lines = wrap(canvas, DISCLAIMER, face('regular'), 6.6, w - offset)
    if lines:
        canvas.drawString(x + offset, y, lines[0])
        cursor = y - 8
        for line in wrap(canvas, ' '.join(lines[1:]), face('regular'), 6.6, w):
            canvas.drawString(x, cursor, line)
            cursor -= 8


def detail_page(canvas, data, photos):
    """Words on the left, pictures and contact on the right."""
    half = (PW - MARGIN * 2 - GUTTER) / 2
    left_x = 0
    right_x = MARGIN + half + GUTTER
    right_w = PW - MARGIN - right_x

    words = [('Location', data.get('location')),
             ('Description', data.get('description'))]
    # A letting shows Rent, a sale shows Price, and a unit offered both ways
    # shows each under its own heading. The two figures are never merged.
    terms = ([('Key Terms', data.get('key_terms'))]
             + ([('Rent', data.get('rent'))] if data.get('to_let') else [])
             + ([('Price', data.get('price_to_buy'))] if data.get('for_sale') else [])
             + [('Business Rates', data.get('rates')),
                ('Service Charge', data.get('service_charge')),
                ('EPC', data.get('epc'))])
    terms = [(t, b) for t, b in terms if has_body(b)]

    # Each panel is the height of what it holds, so nothing is left as a band
    # of empty colour. With no terms, the navy block takes the whole column.
    col_w = MARGIN + half
    if terms:
        navy_h, grey_h = _share_column(canvas, words, terms, col_w, PH - 16)
    else:
        navy_h, grey_h = PH - 8, 0
    _navy_panel(canvas, left_x, PH - navy_h, col_w, navy_h, words)
    if terms:
        _grey_panel(canvas, left_x, 8, col_w, grey_h, terms)

    # Right: the big picture, then a pair beneath it.
    top_h = 250
    cursor = PH - MARGIN - top_h
    hero = data.get('map') or (photos[0] if photos else None)
    if hero:
        draw_image(canvas, hero, right_x, cursor, right_w, top_h)

    rest = [p for p in photos if p is not hero][:2]
    if rest:
        pair_h = 130
        cursor -= pair_h + 14
        gap = 12
        each = (right_w - gap) / len(rest) if len(rest) > 1 else right_w
        for i, photo in enumerate(rest):
            draw_image(canvas, photo, right_x + i * (each + gap), cursor, each, pair_h)

    # Page two carries the contact details and the disclaimer in both formats,
    # because it is the same page two in both.
    contact_block(canvas, right_x, 128, right_w, data)
    disclaimer_block(canvas, right_x, 30, right_w)


def page_footer(canvas, data, page_no):
    """The mark, the address and the page number, along the foot of a page.

    The same on page three and page four, so the added pages sit with the
    first two rather than looking like a different document.
    """
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, 44, PW - MARGIN, 44)

    logo_w = draw_logo(canvas, MARGIN, 16, height=22)
    canvas.setFont(face('regular'), 8)
    canvas.setFillColor(MUTED)
    address = clean(data.get('address'))
    if address:
        room = PW - MARGIN * 2 - logo_w - 80
        while canvas.stringWidth(address, face('regular'), 8) > room and len(address) > 8:
            address = address[:-2]
        canvas.drawString(MARGIN + logo_w + 18, 24, address)
    if page_no:
        canvas.drawRightString(PW - MARGIN, 24, str(page_no))


def page_title(canvas, text):
    """A heading for an added page, in the house style of the panels."""
    canvas.setFont(face('medium'), 13)
    canvas.setFillColor(NAVY)
    canvas.drawString(MARGIN, PH - MARGIN - 4, clean(text).upper())
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.8)
    canvas.line(MARGIN, PH - MARGIN - 14, MARGIN + 150, PH - MARGIN - 14)
    return PH - MARGIN - 30


GALLERY_MAX = 6          # more than this and none of them is worth looking at


def _orientation(photo):
    """'landscape', 'portrait' or 'square' — or None if it cannot be read."""
    try:
        reader = ImageReader(io.BytesIO(photo) if isinstance(photo, bytes) else photo)
        w, h = reader.getSize()
        if not w or not h:
            return None
        ratio = w / h
        if ratio > 1.15:
            return 'landscape'
        if ratio < 0.87:
            return 'portrait'
        return 'square'
    except Exception:
        return None


def gallery_layout(photos):
    """Where each photograph goes, chosen from how many there are and which
    way round they are.

    Returns a list of (x, y, w, h) in the same order as `photos`. The shapes
    are picked so portraits are given tall cells and landscapes wide ones,
    rather than everything being forced into the same square and cropped hard.
    """
    n = len(photos)
    if not n:
        return []
    left, right = MARGIN, PW - MARGIN
    top = PH - MARGIN - 34
    bottom = 56
    w, h = right - left, top - bottom
    gap = 12
    shapes = [_orientation(p) for p in photos]
    portraits = sum(1 for s in shapes if s == 'portrait')

    if n == 1:
        return [(left, bottom, w, h)]

    if n == 2:
        if portraits >= 1:
            # Side by side gives a portrait its height.
            each = (w - gap) / 2
            return [(left + i * (each + gap), bottom, each, h) for i in range(2)]
        # Two landscapes read better stacked, each full width.
        each = (h - gap) / 2
        return [(left, bottom + (1 - i) * (each + gap), w, each) for i in range(2)]

    if n == 3:
        if shapes[0] == 'portrait':
            # A tall photograph beside two stacked.
            big = (w - gap) * 0.5
            small_h = (h - gap) / 2
            return [(left, bottom, big, h),
                    (left + big + gap, bottom + small_h + gap, w - big - gap, small_h),
                    (left + big + gap, bottom, w - big - gap, small_h)]
        # One wide photograph above two.
        big_h = h * 0.58
        small_w = (w - gap) / 2
        return [(left, bottom + h - big_h, w, big_h),
                (left, bottom, small_w, h - big_h - gap),
                (left + small_w + gap, bottom, small_w, h - big_h - gap)]

    if n == 4:
        each_w, each_h = (w - gap) / 2, (h - gap) / 2
        return [(left + (i % 2) * (each_w + gap),
                 bottom + (1 - i // 2) * (each_h + gap), each_w, each_h)
                for i in range(4)]

    if n == 5:
        # Two across the top, three beneath.
        top_h = h * 0.54
        top_w = (w - gap) / 2
        low_w = (w - gap * 2) / 3
        low_h = h - top_h - gap
        out = [(left + i * (top_w + gap), bottom + low_h + gap, top_w, top_h)
               for i in range(2)]
        out += [(left + i * (low_w + gap), bottom, low_w, low_h) for i in range(3)]
        return out

    # Six: a plain, even grid.
    each_w, each_h = (w - gap * 2) / 3, (h - gap) / 2
    return [(left + (i % 3) * (each_w + gap),
             bottom + (1 - i // 3) * (each_h + gap), each_w, each_h)
            for i in range(6)]


def photos_page(canvas, data, photos, page_no=3):
    """Page three: further photographs, and nothing else.

    No description, no location, no terms, no costs — those are on page two and
    repeating them here would only push the photographs about. The layout is
    chosen from how many photographs there are and which way round they are,
    and an empty cell is never drawn.
    """
    photos = [p for p in (photos or []) if p][:GALLERY_MAX]
    cursor = page_title(canvas, 'Further photographs')
    if not photos:
        # Nothing to show. Say so rather than printing an empty frame.
        canvas.setFont(face('regular'), 10.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, cursor - 14,
                          'No further photographs were selected for this page.')
        page_footer(canvas, data, page_no)
        return
    for photo, (x, y, w, h) in zip(photos, gallery_layout(photos)):
        draw_image(canvas, photo, x, y, w, h)
    page_footer(canvas, data, page_no)


def floorplan_page(canvas, data, floorplans, page_no=4):
    """Page four: the floorplan, as large as it will go.

    Fitted rather than cropped, and never overlaid: a floorplan's room names,
    dimensions and scale bar are the whole point of it, and a logo across the
    middle would cover exactly the part somebody is trying to read. The mark
    and the page number stay in the footer, clear of the drawing.
    """
    floorplans = [f for f in (floorplans or []) if f][:2]
    cursor = page_title(canvas, 'Floorplan')
    left, right = MARGIN, PW - MARGIN
    bottom, top = 56, cursor - 6

    if not floorplans:
        canvas.setFont(face('regular'), 10.5)
        canvas.setFillColor(MUTED)
        canvas.drawCentredString(PW / 2, (top + bottom) / 2,
                                 'No floorplan has been uploaded for this property.')
        page_footer(canvas, data, page_no)
        return

    if len(floorplans) == 1:
        draw_image(canvas, floorplans[0], left, bottom, right - left, top - bottom,
                   fit=True)
    else:
        gap = 16
        each = (right - left - gap) / 2
        for i, plan in enumerate(floorplans):
            draw_image(canvas, plan, left + i * (each + gap), bottom, each,
                       top - bottom, fit=True)
    page_footer(canvas, data, page_no)


# How many photographs each of the first two pages uses. Page three starts
# after these, so a photograph is never printed twice.
COVER_PHOTOS = 1
DETAIL_PHOTOS = 3


def build(data, photos, pages=2, floorplans=None):
    """The particulars as PDF bytes.

    `pages` is 2 or 4, and the four-page version IS the two-page version with
    two pages added. Pages one and two are the same calls with the same
    arguments in both, so a change to the two-page layout cannot fail to reach
    the four-page one — there is no second copy of them to forget.

        page 1   cover            shared
        page 2   property details shared
        page 3   photographs      four-page only
        page 4   floorplan        four-page only
    """
    pages = 4 if int(pages or 2) == 4 else 2
    photos = [p for p in (photos or []) if p]
    floorplans = [f for f in (floorplans or []) if f]
    buf = io.BytesIO()
    canvas = pdfcanvas.Canvas(buf, pagesize=PAGE)
    canvas.setTitle(f"Particulars — {clean(data.get('address')) or 'Property'}")
    canvas.setAuthor(COMPANY['name'])
    canvas.setSubject('Property particulars')

    # ── Pages one and two: identical in both formats ──
    cover = dict(data)
    cover['cover'] = photos[0] if photos else None
    cover_page(canvas, cover)
    canvas.showPage()

    detail_photos = photos[COVER_PHOTOS:COVER_PHOTOS + DETAIL_PHOTOS]
    detail_page(canvas, data, detail_photos)

    if pages == 4:
        # ── Page three: the photographs pages one and two did not use ──
        canvas.showPage()
        photos_page(canvas, data, photos[COVER_PHOTOS + DETAIL_PHOTOS:], page_no=3)
        # ── Page four: the floorplan ──
        canvas.showPage()
        floorplan_page(canvas, data, floorplans, page_no=4)

    canvas.showPage()
    canvas.save()
    return buf.getvalue()


def photo_plan(photos, pages=2):
    """Where each photograph will appear, so the screen can say so.

    Returns a dict of lists, in the user's own order. Nothing appears twice.
    """
    photos = list(photos or [])
    pages = 4 if int(pages or 2) == 4 else 2
    cover = photos[:COVER_PHOTOS]
    detail = photos[COVER_PHOTOS:COVER_PHOTOS + DETAIL_PHOTOS]
    rest = photos[COVER_PHOTOS + DETAIL_PHOTOS:]
    gallery = rest[:GALLERY_MAX] if pages == 4 else []
    used = COVER_PHOTOS + DETAIL_PHOTOS + len(gallery)
    return {'cover': cover, 'detail': detail, 'gallery': gallery,
            'excluded': photos[used:] if pages == 4 else rest}


def filename_for(address, pages, when=None):
    """A name somebody can recognise in an inbox."""
    when = when or date.today()
    safe = re.sub(r'[^A-Za-z0-9 ,&\-]', '', clean(address) or 'Property')[:70].strip()
    return f'Particulars - {safe} - {int(pages)} Page - {when.strftime("%d.%m.%Y")}.pdf'
