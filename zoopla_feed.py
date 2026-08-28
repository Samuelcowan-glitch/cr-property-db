"""
Zoopla data-feed export for Cowan & Rutter.

Generates a standard ADF/BLM v3 feed file from the app's website Listings and
(optionally) uploads it, with the listing photos, to Zoopla over SFTP.

HOW ZOOPLA INGESTS THIS
-----------------------
Zoopla (like Rightmove) accepts listings from member branches as a BLM feed:
a single pipe/caret-delimited text file (`.blm`) plus the referenced image
files, dropped into your branch's feed directory on their SFTP server. Zoopla
picks it up on a schedule and reconciles: a listing present with
PUBLISHED_FLAG=1 is live; setting PUBLISHED_FLAG=0 (or dropping it from the
file) takes it down.

BEFORE THIS CAN SEND ANYTHING you must ask your Zoopla account manager to
"enable a custom/third-party data feed for our branch". They return:
  * SFTP host / username / password  -> ZOOPLA_FTP_* env vars
  * your branch/member id            -> ZOOPLA_BRANCH_ID env var
  * the exact BLM column set + property-type codes they expect.

The column set below is the common ADF/BLM v3 subset. The pieces most likely
to need tweaking against Zoopla's spec doc are marked ``SPEC:``. Nothing here
guesses about your account — with no env vars set, generate_feed() still works
(for the preview screen) and upload_feed() is a no-op.
"""

from datetime import datetime
import io
import os
import posixpath

# Field / record separators defined by the BLM spec.
EOF = '^'   # end of field
EOR = '~'   # end of record


# ── small mapping helpers ────────────────────────────────────────────────────

def _is_sale(listing):
    """A listing is a sale (vs a letting) when priced as a sale."""
    unit = (listing.listing_price_unit or '').lower()
    return unit == 'sale' or bool(getattr(listing, 'set_as_for_sale', False))


def _trans_type_id(listing):
    # SPEC: 1 = resale / for-sale, 2 = lettings.
    return 1 if _is_sale(listing) else 2


def _status_id(listing):
    """Map our listing_status onto BLM STATUS_ID.

    SPEC (BLM v3): 0 = Available, 1 = Under Offer / SSTC,
                   2 = Sold / Let Agreed. Confirm exact ids with Zoopla.
    """
    s = (listing.listing_status or 'available').lower().replace('_', '-')
    if s in ('under-offer', 'sstc', 'sold-stc'):
        return 1
    if s in ('let-agreed', 'sold', 'let', 'completed', 'withdrawn'):
        return 2
    return 0


def _published_flag(listing):
    """1 = show on Zoopla, 0 = take down. Driven by the per-listing toggle."""
    return 1 if getattr(listing, 'zoopla_listed', False) else 0


def _price(listing):
    return int(round(listing.listing_price or listing.sale_price or 0))


def _price_qualifier_id(listing):
    # SPEC: 0 = default/none. Zoopla's qualifier table (POA, OIEO, guide, etc.)
    # can be mapped here later; POA is the only one we can infer safely.
    unit = (listing.listing_price_unit or '').lower()
    if unit == 'poa' or _price(listing) == 0:
        return 3   # SPEC: 3 is commonly "POA" in the ADF qualifier table.
    return 0


def _rent_frequency(listing):
    # SPEC: 0 = weekly, 1 = monthly, ... Feed docs vary; we send monthly for
    # pcm and annual-as-monthly is avoided by leaving PA prices as-is with a
    # comment. Zoopla residential lettings expect pcm.
    unit = (listing.listing_price_unit or '').lower()
    return 1 if unit in ('pcm', 'pa') else ''


def _display_address(listing, prop):
    try:
        return listing.display_title
    except Exception:
        return (prop.address if prop else '') or (listing.unit_name or 'Listing')


def _split_postcode(postcode):
    """'SW10 0SZ' -> ('SW10', '0SZ'). BLM wants the two halves separately."""
    pc = (postcode or '').strip().upper()
    if ' ' in pc:
        a, b = pc.rsplit(' ', 1)
        return a, b
    if len(pc) > 3:
        return pc[:-3].strip(), pc[-3:].strip()
    return pc, ''


# SPEC: Zoopla's own limit for the summary field, to be confirmed against
# their feed document. The previous code used 2000 and truncated silently;
# the number is kept, but nothing is cut without the user being told.
SUMMARY_LIMIT = 2000

# SPEC: characters the feed is known to dislike. The vertical bar is not among
# them — it is a field separator in some feeds but not in BLM, which uses ^ and
# ~ — so a strapline written with bars is sent as written. If Zoopla refuse it,
# their response is shown rather than the strapline being quietly changed.
SUMMARY_FORBIDDEN = '^~'


def summary_problems(strapline):
    """What is wrong with a strapline, in words, or nothing if it is fine.

    Nothing here alters the text. A strapline that is too long or carries a
    character the feed cannot take is reported so somebody can amend it, which
    is the only way it stays the wording that was chosen.
    """
    text = ' '.join(str(strapline or '').split())
    problems = []
    if not text:
        problems.append('No strapline has been written, so Zoopla has no summary.')
        return problems, text
    if len(text) > SUMMARY_LIMIT:
        problems.append(
            f'The strapline is {len(text)} characters; Zoopla accepts '
            f'{SUMMARY_LIMIT}. Shorten it in the Marketing section.')
    bad = sorted({c for c in text if c in SUMMARY_FORBIDDEN})
    if bad:
        problems.append(
            'The feed cannot carry ' + ', '.join(repr(c) for c in bad) +
            '. Please amend the strapline.')
    return problems, text


def _summary(listing, prop):
    """What Zoopla shows as the summary: the strapline, as written.

    Never the marketing description — that goes to the description field on
    its own — and never a made-up line. An empty strapline sends nothing,
    which is what makes the missing-summary warning honest.
    """
    text = ' '.join(str(getattr(listing, 'strapline', '') or '').split())
    return text


def _description(listing, prop):
    parts = []
    if listing.blurb:
        parts.append(listing.blurb)
    elif prop and prop.description:
        parts.append(prop.description)
    # Size range ("from / to"), when quoted as a range for commercial space.
    lo, hi = getattr(listing, 'min_size', None), getattr(listing, 'max_size', None)
    if lo or hi:
        if lo and hi:
            parts.append(f'Size: {int(lo):,} - {int(hi):,} sq ft')
        else:
            parts.append(f'Size: {int(lo or hi):,} sq ft')
    if listing.location_description:
        parts.append('Location: ' + listing.location_description)
    if listing.key_terms:
        parts.append('Key terms: ' + listing.key_terms)
    if listing.epc_band:
        parts.append('EPC: ' + str(listing.epc_band))
    txt = '\n\n'.join(str(p) for p in parts if p)
    # Newlines are illegal inside a BLM field; encode them the way the spec asks.
    return txt.replace('\r', '').replace('\n', ' / ')


def _clean(value):
    """Make any value safe for a caret/tilde-delimited field."""
    if value is None:
        return ''
    s = str(value)
    return s.replace(EOF, ' ').replace(EOR, ' ').replace('\r', ' ').replace('\n', ' ').strip()


# ── media ────────────────────────────────────────────────────────────────────

def _image_ext(photo):
    name = (photo.filename or '').lower()
    for ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
        if name.endswith(ext):
            return '.jpg' if ext == '.jpeg' else ext
    mime = (photo.file_mime or '').lower()
    if 'png' in mime:
        return '.png'
    if 'gif' in mime:
        return '.gif'
    if 'webp' in mime:
        return '.webp'
    return '.jpg'


def _media_for_listing(listing, agent_ref, max_images=20):
    """Return (columns_dict, files) for a listing's photos.

    files is a list of (remote_filename, bytes) to ship alongside the .blm.
    columns_dict maps MEDIA_IMAGE_NN / MEDIA_IMAGE_TEXT_NN to values.
    """
    cols, files = {}, []
    photos = list(getattr(listing, 'photos', []) or [])[:max_images]
    for i, ph in enumerate(photos):
        fname = f'{agent_ref}_IMG_{i:02d}{_image_ext(ph)}'
        cols[f'MEDIA_IMAGE_{i:02d}'] = fname
        cols[f'MEDIA_IMAGE_TEXT_{i:02d}'] = _clean(ph.caption or '')
        if ph.file_data:
            files.append((fname, ph.file_data))
    # Brochure / floor plan as documents (SPEC: MEDIA_DOCUMENT_NN).
    doc_i = 0
    if getattr(listing, 'floor_plan_data', None):
        fname = f'{agent_ref}_FP_{doc_i:02d}.pdf'
        cols[f'MEDIA_FLOOR_PLAN_{doc_i:02d}'] = fname
        files.append((fname, listing.floor_plan_data))
    if getattr(listing, 'brochure_data', None):
        fname = f'{agent_ref}_DOC_{doc_i:02d}.pdf'
        cols[f'MEDIA_DOCUMENT_{doc_i:02d}'] = fname
        files.append((fname, listing.brochure_data))
    return cols, files


# ── the BLM column order ─────────────────────────────────────────────────────
# The base (non-media) columns, in the order they appear in #DEFINITION# and
# #DATA#. Media columns are appended per-file after these. SPEC: reconcile this
# list against the definition Zoopla sends for your branch.
BASE_COLUMNS = [
    'AGENT_REF', 'BRANCH_ID', 'STATUS_ID', 'PUBLISHED_FLAG',
    'TRANS_TYPE_ID', 'PROP_SUB_ID', 'PRICE', 'PRICE_QUALIFIER',
    'RENT_FREQUENCY', 'BEDROOMS', 'BATHROOMS', 'RECEPTIONS',
    'SUMMARY', 'DESCRIPTION',
    'DISPLAY_ADDRESS', 'ADDRESS_1', 'ADDRESS_2', 'TOWN', 'POSTCODE1', 'POSTCODE2',
    'LATITUDE', 'LONGITUDE',
    'FEATURE1', 'FEATURE2', 'FEATURE3',
    'CREATE_DATE', 'UPDATE_DATE',
]


# How a CRM property type is sent to Zoopla.
#
# Zoopla's commercial code table has no Creative / Art Studio and no Light
# Industrial. Each is sent as the nearest category it does support, and the
# CRM keeps its own type unchanged — the portal's fallback is never shown as
# the property's type anywhere in the CRM or on the company website.
#
#   CRM type                Sent to Zoopla as        Why
#   ---------------------   ----------------------   ----------------------------
#   Office                  Office                   exact
#   Retail                  Retail                   exact
#   Industrial              Industrial               exact
#   Light Industrial        Industrial               nearest supported
#   Creative / Art Studio   Office                   studios are let as workspace
#   anything else           0 (unset)                rather than assert a wrong one
#
# SPEC: the numbers below are placeholders until Zoopla send their code table.
# Nothing here refuses a listing: an unmapped type sends 0, which leaves the
# field present without claiming a type, so publishing is never blocked.
ZOOPLA_TYPE_MAP = {
    'office': 'Office',
    'retail': 'Retail',
    'industrial': 'Industrial',
    'light industrial': 'Industrial',
    'creative / art studio': 'Office',
}

ZOOPLA_TYPE_CODES = {'Office': 0, 'Retail': 0, 'Industrial': 0}


def zoopla_category_for(property_type):
    """The Zoopla category a CRM type is published under, or None."""
    return ZOOPLA_TYPE_MAP.get((property_type or '').strip().lower())


def _prop_sub_id(listing, prop):
    """SPEC: property-type code, from the mapping above.

    An unmapped type sends 0 rather than a guess, so a listing is never held
    back for want of a category.
    """
    category = zoopla_category_for(getattr(prop, 'property_type', None) if prop else None)
    return ZOOPLA_TYPE_CODES.get(category, 0)


def _base_row(listing, branch_id):
    prop = getattr(listing, 'prop', None)
    a1, a2 = '', ''
    addr = (prop.address if prop else '') or ''
    if ',' in addr:
        a1, a2 = [p.strip() for p in addr.split(',', 1)]
    else:
        a1 = addr
    pc1, pc2 = _split_postcode(prop.postcode if prop else '')
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    created = (listing.created_at or datetime.utcnow()).strftime('%Y-%m-%d %H:%M:%S')
    return {
        'AGENT_REF':      f'CR-{listing.id}',
        'BRANCH_ID':      branch_id or '',
        'STATUS_ID':      _status_id(listing),
        'PUBLISHED_FLAG': _published_flag(listing),
        'TRANS_TYPE_ID':  _trans_type_id(listing),
        'PROP_SUB_ID':    _prop_sub_id(listing, prop),
        'PRICE':          _price(listing),
        'PRICE_QUALIFIER': _price_qualifier_id(listing),
        'RENT_FREQUENCY': _rent_frequency(listing),
        'BEDROOMS':       listing.beds or '',
        'BATHROOMS':      listing.baths or '',
        'RECEPTIONS':     '',
        'SUMMARY':        _summary(listing, prop),
        'DESCRIPTION':    _description(listing, prop),
        'DISPLAY_ADDRESS': _display_address(listing, prop),
        'ADDRESS_1':      a1,
        'ADDRESS_2':      a2,
        'TOWN':           listing.area or (prop.postcode if prop else '') or 'London',
        'POSTCODE1':      pc1,
        'POSTCODE2':      pc2,
        'LATITUDE':       listing.lat or (prop.lat if prop else '') or '',
        'LONGITUDE':      listing.lng or (prop.lng if prop else '') or '',
        **_features(listing),
        'CREATE_DATE':    created,
        'UPDATE_DATE':    now,
    }


# Zoopla accepts FEATURE1 to FEATURE10.
FEATURE_SLOTS = 10


def _features(listing):
    """The key terms as separate features, one per slot.

    Everything used to go into FEATURE1 as a single string, so a property with
    five key terms published one long feature and the other nine slots stayed
    empty. Each term now gets its own slot, in the order it was entered, with
    the EPC and use class taking whatever slots are left over.
    """
    from app import key_terms_list
    terms = key_terms_list(listing.key_terms, limit=FEATURE_SLOTS)
    extras = []
    if listing.epc_band:
        extras.append(f'EPC {listing.epc_band}')
    if listing.use_class or listing.residential_use:
        extras.append(str(listing.use_class or listing.residential_use))
    for extra in extras:
        if len(terms) >= FEATURE_SLOTS:
            break
        if extra.lower() not in {t.lower() for t in terms}:
            terms.append(extra)
    return {f'FEATURE{i}': (terms[i - 1] if i <= len(terms) else '')
            for i in range(1, FEATURE_SLOTS + 1)}


def generate_feed(listings, branch_id=None):
    """Build a BLM v3 feed from listings.

    Returns (blm_text, media_files) where media_files is a list of
    (remote_filename, bytes). Only listings with the Zoopla toggle on are
    included; those toggled off are emitted with PUBLISHED_FLAG=0 only if you
    pass them in (so Zoopla takes them down) — callers decide which set to send.
    """
    branch_id = branch_id if branch_id is not None else os.environ.get('ZOOPLA_BRANCH_ID', '')

    rows, media_files = [], []
    # Collect each row's full column dict (base + its media columns) and the
    # union of media column names, so the definition covers every image slot.
    media_col_names = []
    seen_media_cols = set()
    row_dicts = []
    for l in listings:
        base = _base_row(l, branch_id)
        mcols, mfiles = _media_for_listing(l, base['AGENT_REF'])
        media_files.extend(mfiles)
        for k in mcols:
            if k not in seen_media_cols:
                seen_media_cols.add(k)
                media_col_names.append(k)
        merged = dict(base)
        merged.update(mcols)
        row_dicts.append(merged)

    # Deterministic media column order (IMAGE_00, IMAGE_TEXT_00, IMAGE_01, ...).
    media_col_names.sort()
    columns = BASE_COLUMNS + media_col_names

    for merged in row_dicts:
        rows.append(EOF.join(_clean(merged.get(c, '')) for c in columns) + EOF + EOR)

    header = [
        '#HEADER#',
        'Version : 3',
        f"EOF : '{EOF}'",
        f"EOR : '{EOR}'",
        f'Property Count : {len(listings)}',
        f"Generated Date : {datetime.utcnow().strftime('%d-%b-%Y %H:%M')}",
    ]
    definition = ['#DEFINITION#', EOF.join(columns) + EOF + EOR]
    data = ['#DATA#'] + rows
    end = ['#END#']

    blm_text = '\n'.join(header + definition + data + end) + '\n'
    return blm_text, media_files


# ── SFTP upload ──────────────────────────────────────────────────────────────

def feed_config():
    """Read Zoopla feed settings from the environment. Returns a dict; the
    'ready' key is True only when host/user/pass/branch are all present."""
    cfg = {
        'host':      os.environ.get('ZOOPLA_FTP_HOST', ''),
        'port':      int(os.environ.get('ZOOPLA_FTP_PORT', '22') or 22),
        'user':      os.environ.get('ZOOPLA_FTP_USER', ''),
        'password':  os.environ.get('ZOOPLA_FTP_PASS', ''),
        'remote_dir': os.environ.get('ZOOPLA_FTP_DIR', '.'),
        'branch_id': os.environ.get('ZOOPLA_BRANCH_ID', ''),
        'filename':  os.environ.get('ZOOPLA_FEED_FILENAME', 'cowan-rutter.blm'),
    }
    cfg['ready'] = bool(cfg['host'] and cfg['user'] and cfg['password'] and cfg['branch_id'])
    return cfg


def upload_feed(blm_text, media_files, cfg=None):
    """Upload the .blm plus its images to Zoopla over SFTP.

    Returns (ok, message). A no-op (ok=False) if the feed isn't configured, so
    it's safe to call before credentials exist.
    """
    cfg = cfg or feed_config()
    if not cfg['ready']:
        return False, ('Zoopla feed not configured — set ZOOPLA_FTP_HOST, '
                       'ZOOPLA_FTP_USER, ZOOPLA_FTP_PASS and ZOOPLA_BRANCH_ID.')
    try:
        import paramiko
    except ImportError:
        return False, "paramiko is not installed (add it to requirements.txt)."

    transport = None
    try:
        transport = paramiko.Transport((cfg['host'], cfg['port']))
        transport.connect(username=cfg['user'], password=cfg['password'])
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_dir = cfg['remote_dir'] or '.'
        try:
            sftp.chdir(remote_dir)
        except IOError:
            pass  # some feeds land in the login home; ignore a missing dir

        # Images first, then the .blm last so Zoopla never sees the manifest
        # before the files it references.
        for name, data in media_files:
            with sftp.open(posixpath.join(remote_dir, name), 'wb') as f:
                f.write(data)
        with sftp.open(posixpath.join(remote_dir, cfg['filename']), 'w') as f:
            f.write(blm_text)
        sftp.close()
        return True, (f"Uploaded {cfg['filename']} + {len(media_files)} media "
                      f"file(s) to {cfg['host']}:{remote_dir}")
    except Exception as e:  # noqa: BLE001 - surface any SFTP error to the admin UI
        return False, f'SFTP upload failed: {e}'
    finally:
        if transport is not None:
            transport.close()
