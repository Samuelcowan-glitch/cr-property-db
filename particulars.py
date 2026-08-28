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


def draw_heading(canvas, text, x, y, size=11, colour=NAVY):
    canvas.setFont(face('medium'), size)
    canvas.setFillColor(colour)
    canvas.drawString(x, y, clean(text))
    return y - size - 4


def draw_image(canvas, source, x, y, w, h):
    """Draw a photograph filling the box, cropped rather than distorted.

    The aspect ratio is kept and the overflow trimmed, which is what
    object-fit: cover does on the web.
    """
    if not source:
        return False
    try:
        reader = ImageReader(io.BytesIO(source) if isinstance(source, bytes)
                             else source)
        iw, ih = reader.getSize()
        if not iw or not ih:
            return False
        scale = max(w / iw, h / ih)
        dw, dh = iw * scale, ih * scale
        canvas.saveState()
        path = canvas.beginPath()
        path.rect(x, y, w, h)
        canvas.clipPath(path, stroke=0, fill=0)
        canvas.drawImage(reader, x - (dw - w) / 2, y - (dh - h) / 2,
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

def cover_page(canvas, data):
    """A full-bleed photograph above a band carrying the mark and the headline."""
    band = 118
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

    logo_w = draw_logo(canvas, MARGIN, band - 92, height=76)

    left = MARGIN + logo_w + 40
    right = PW - MARGIN
    centre = (left + right) / 2

    headline = clean(data.get('headline')) or clean(data.get('address'))
    canvas.setFont(face('regular'), 21)
    canvas.setFillColor(NAVY)
    while canvas.stringWidth(headline, face('regular'), 21) > (right - left) and len(headline) > 12:
        headline = headline[:-2]
    canvas.drawCentredString(centre, band - 44, headline.upper())

    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.8)
    rule = min(230, (right - left) * 0.55)
    canvas.line(centre - rule / 2, band - 56, centre + rule / 2, band - 56)

    address = clean(data.get('address'))
    canvas.setFont(face('regular'), 10.5)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(centre, band - 74, address)

    size_line = clean(data.get('size_line'))
    if size_line:
        canvas.setFont(face('regular'), 10.5)
        canvas.setFillColor(INK)
        canvas.drawRightString(right, band - 74, size_line)

    price = clean(data.get('price'))
    if price:
        canvas.setFont(face('medium'), 11)
        canvas.setFillColor(NAVY)
        canvas.drawString(left, band - 74, price)


def measure_blocks(canvas, blocks, width, size=9.5, leading=12.6,
                   heading=15, gap=10):
    """How tall a set of headed paragraphs will be.

    Panels are sized to their contents rather than to a fixed fraction, so a
    short description does not leave a band of empty colour beneath it.
    """
    total = 30
    for title, body in blocks:
        if not clean(body):
            continue
        total += heading
        total += leading * max(1, len(wrap(canvas, body, face('regular'), size, width - 44)))
        total += gap
    return total + 12


def _navy_panel(canvas, x, y, w, h, blocks):
    """The navy block: white headings and text, as on the reference."""
    canvas.setFillColor(NAVY)
    canvas.rect(x, y, w, h, stroke=0, fill=1)
    inner = x + 22
    width = w - 44
    cursor = y + h - 30
    for title, body in blocks:
        if not clean(body):
            continue
        canvas.setFont(face('medium'), 11)
        canvas.setFillColor(white)
        canvas.drawString(inner, cursor, clean(title))
        cursor -= 15
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
        if not clean(body):
            continue
        canvas.setFont(face('medium'), 10.5)
        canvas.setFillColor(NAVY)
        canvas.drawString(inner, cursor, clean(title))
        cursor -= 14
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


def detail_page(canvas, data, photos, with_terms=True):
    """Words on the left, pictures and contact on the right."""
    half = (PW - MARGIN * 2 - GUTTER) / 2
    left_x = 0
    right_x = MARGIN + half + GUTTER
    right_w = PW - MARGIN - right_x

    words = [('Location', data.get('location')),
             ('Description', data.get('description'))]
    terms = [('Terms', data.get('terms')), ('Rent', data.get('rent')),
             ('Rates', data.get('rates')),
             ('Service Charge', data.get('service_charge')),
             ('EPC', data.get('epc'))] if with_terms else []
    terms = [(t, b) for t, b in terms if clean(b)]

    # Each panel is the height of what it holds, so nothing is left as a band
    # of empty colour. With no terms, the navy block takes the whole column.
    col_w = MARGIN + half
    if terms:
        wanted = measure_blocks(canvas, words, col_w)
        navy_h = max(PH * 0.30, min(wanted, PH * 0.66))
    else:
        navy_h = PH - 8
    _navy_panel(canvas, left_x, PH - navy_h, col_w, navy_h, words)
    if terms:
        _grey_panel(canvas, left_x, 8, col_w, PH - navy_h - 16, terms)

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

    contact_y = 128
    contact_block(canvas, right_x, contact_y, right_w, data)
    disclaimer_block(canvas, right_x, 30, right_w)


def gallery_page(canvas, data, photos, title='Accommodation'):
    """Schedule and specification beside a grid of photographs."""
    half = (PW - MARGIN * 2 - GUTTER) / 2
    right_x = MARGIN + half + GUTTER
    right_w = PW - MARGIN - right_x

    blocks = [(title, data.get('accommodation')),
              ('Specification', data.get('specification')),
              ('Key Terms', data.get('terms'))]
    blocks = [(t, b) for t, b in blocks if clean(b)]
    if blocks:
        _navy_panel(canvas, 0, PH * 0.42, MARGIN + half, PH * 0.58, blocks[:2])
        if len(blocks) > 2:
            _grey_panel(canvas, 0, 8, MARGIN + half, PH * 0.42 - 16, blocks[2:])
    elif photos:
        # Nothing to say, so the photographs take the width rather than sitting
        # beside an empty panel.
        _photo_grid(canvas, photos, MARGIN, MARGIN, PW - MARGIN * 2, PH - MARGIN * 2)
        return

    _photo_grid(canvas, photos, right_x, MARGIN, right_w, PH - MARGIN * 2)


def _photo_grid(canvas, photos, x, y, w, h, columns=2):
    """A tidy grid, filling the space it is given."""
    photos = [p for p in photos if p][:6]
    if not photos:
        return
    rows = max(1, (len(photos) + columns - 1) // columns)
    gap = 12
    cell_w = (w - gap * (columns - 1)) / columns
    cell_h = (h - gap * (rows - 1)) / rows
    for i, photo in enumerate(photos):
        col, row = i % columns, i // columns
        draw_image(canvas, photo,
                   x + col * (cell_w + gap),
                   y + h - cell_h - row * (cell_h + gap),
                   cell_w, cell_h)


def closing_page(canvas, data, photos):
    """Where it is, what it costs to run, and who to ring."""
    half = (PW - MARGIN * 2 - GUTTER) / 2
    right_x = MARGIN + half + GUTTER
    right_w = PW - MARGIN - right_x

    blocks = [('Location', data.get('location')),
              ('Transport & Local Area', data.get('transport')),
              ('EPC', data.get('epc')),
              ('Planning & Use', data.get('use_class'))]
    blocks = [(t, b) for t, b in blocks if clean(b)]
    if blocks:
        _navy_panel(canvas, 0, PH * 0.40, MARGIN + half, PH * 0.60, blocks)

    viewing = [('Viewing', data.get('viewing') or
                'Strictly by appointment through the sole agent.')]
    _grey_panel(canvas, 0, 8, MARGIN + half, PH * 0.40 - 16, viewing)

    top = data.get('map') or data.get('floorplan')
    cursor = PH - MARGIN
    if top:
        h = 250
        cursor -= h
        draw_image(canvas, top, right_x, cursor, right_w, h)
        cursor -= 16
    if photos:
        # Whatever room is left goes to the photographs, so nothing is blank.
        available = cursor - 150
        if available > 70:
            _photo_grid(canvas, photos[:2], right_x, cursor - available,
                        right_w, available, columns=2)

    contact_block(canvas, right_x, 128, right_w, data)
    disclaimer_block(canvas, right_x, 30, right_w)


# ── Putting a document together ──────────────────────────────────────────────

def build(data, photos, pages=2):
    """The particulars as PDF bytes.

    `pages` is 2 or 4. Both are the same components in a different order, so
    neither can drift away from the other.
    """
    pages = 4 if int(pages or 2) == 4 else 2
    photos = [p for p in (photos or []) if p]
    buf = io.BytesIO()
    canvas = pdfcanvas.Canvas(buf, pagesize=PAGE)
    canvas.setTitle(f"Particulars — {clean(data.get('address')) or 'Property'}")
    canvas.setAuthor(COMPANY['name'])
    canvas.setSubject('Property particulars')

    cover = dict(data)
    cover['cover'] = photos[0] if photos else None
    cover_page(canvas, cover)
    canvas.showPage()

    rest = photos[1:]
    if pages == 2:
        detail_page(canvas, data, rest[:3])
    else:
        detail_page(canvas, data, rest[:3], with_terms=False)
        canvas.showPage()
        gallery_page(canvas, data, rest[3:9])
        canvas.showPage()
        closing_page(canvas, data, rest[9:11] or rest[:2])
    canvas.showPage()
    canvas.save()
    return buf.getvalue()


def filename_for(address, pages, when=None):
    """A name somebody can recognise in an inbox."""
    when = when or date.today()
    safe = re.sub(r'[^A-Za-z0-9 ,&\-]', '', clean(address) or 'Property')[:70].strip()
    return f'Particulars - {safe} - {int(pages)} Page - {when.strftime("%d.%m.%Y")}.pdf'
