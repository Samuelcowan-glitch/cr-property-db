"""Find a company's logo from its own website.

Pure logic, no Flask and no database — the same shape as business_rates.py.
app.py owns the model, the route and the background thread; this module only
answers one question: "given this website address, what image should we show,
and is it good enough?"

Nothing here trusts the remote server. Everything it returns has been fetched
under a timeout and a byte cap, validated as a real raster image by Pillow,
and measured against a minimum quality bar. A lookup that cannot clear that
bar returns a result with ok=False and a plain reason, which the caller
records so the lookup is never retried on a page load.

Uses only requests and Pillow, both already in requirements.txt. The HTML is
parsed with the standard library's html.parser, so no BeautifulSoup is needed.
"""

import io
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

# ── Limits ────────────────────────────────────────────────────────────────────
# Deliberately tight. This runs on Railway with waitress threads=4, so a slow
# remote server must never be allowed to hold a thread open.
CONNECT_TIMEOUT = 4           # seconds to get a connection
READ_TIMEOUT    = 6           # seconds to get the body
MAX_BYTES       = 2 * 1024 * 1024   # 2 MB: a logo is never larger
MAX_HTML_BYTES  = 512 * 1024        # we only need <head>
MAX_CANDIDATES  = 8           # stop guessing after this many
MAX_REDIRECTS   = 3

# Quality bar for an image we are willing to show as a company's logo.
MIN_EDGE        = 32          # a 16x16 favicon is not a logo
PREFERRED_EDGE  = 96          # at or above this we stop looking for better
MAX_ASPECT      = 8.0         # a wordmark is wide; a banner is not a logo

USER_AGENT = ('Mozilla/5.0 (compatible; CowanRutterCRM/1.0; '
              '+logo lookup for a company record)')

# Image types we accept. SVG is handled separately and reluctantly — see
# sanitise_svg() and the note there.
RASTER_MIMES = {
    'image/png': 'PNG', 'image/jpeg': 'JPEG', 'image/jpg': 'JPEG',
    'image/webp': 'WEBP', 'image/gif': 'GIF', 'image/x-icon': 'ICO',
    'image/vnd.microsoft.icon': 'ICO', 'image/ico': 'ICO',
}
SVG_MIMES = {'image/svg+xml', 'image/svg'}


# ── Result ────────────────────────────────────────────────────────────────────
# Sources we trust enough to put on the screen without anyone looking at it
# first. A site only declares these to say "this is my icon", so a wrong answer
# means the site itself is wrong.
CONFIDENT_SOURCES = {'apple-touch-icon', 'link-icon', 'manifest', 'favicon.ico'}


def _found(data, mime, width, height, source, url):
    return {'ok': True, 'data': data, 'mime': mime, 'width': width,
            'height': height, 'source': source, 'url': url, 'reason': None,
            'confident': source in CONFIDENT_SOURCES}


def _nothing(reason):
    """A lookup that found nothing. Recorded, so it is not tried again."""
    return {'ok': False, 'data': None, 'mime': None, 'width': None,
            'height': None, 'source': None, 'url': None, 'reason': reason,
            'confident': False}


# ── Where we are allowed to look ──────────────────────────────────────────────
def normalise_website(website):
    """Turn whatever somebody typed into the website box into a base URL.

    Accepts 'example.co.uk', 'www.example.co.uk/about', 'http://example.co.uk'.
    Returns None when there is nothing usable, rather than guessing.
    """
    raw = (website or '').strip()
    if not raw or ' ' in raw.strip():
        raw = raw.split()[0] if raw.split() else ''
    if not raw:
        return None
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', raw):
        raw = 'https://' + raw
    parts = urlsplit(raw)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return None
    host = parts.hostname.lower().rstrip('.')
    # A hostname with no dot is not a public website — it is something on the
    # network this server sits on.
    if '.' not in host or host.endswith('.local'):
        return None
    netloc = host
    if parts.port and parts.port not in (80, 443):
        netloc = f'{host}:{parts.port}'
    return urlunsplit((parts.scheme, netloc, '/', '', ''))


def is_public_host(host):
    """True only when every address this name resolves to is on the internet.

    This is the guard that matters. The website field is free text typed by a
    user, and this code fetches it from inside Railway's network. Without this,
    typing 'http://169.254.170.2/' or an internal service name into a company
    record would make the server fetch it and hand back the body — the private
    network is reachable from the container and the browser is not.

    Checked after resolution, so a public name that resolves to a private
    address is caught too.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False
    return True


# ── Reading the company's own <head> ─────────────────────────────────────────
class _HeadParser(HTMLParser):
    """Collect icon and image declarations from a page's head.

    Stops at </head> or <body>: we never need the rest, and a 500 KB page of
    markup should not be walked to find a link tag in the first 2 KB.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.icons = []      # (href, declared_size, rel)
        self.og_images = []  # href
        self.manifest = None
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        a = {k.lower(): (v or '') for k, v in attrs}
        if tag == 'body':
            self.done = True
        elif tag == 'link':
            rels = (a.get('rel') or '').lower().split()
            href = a.get('href') or ''
            if not href:
                return
            if 'manifest' in rels and not self.manifest:
                self.manifest = href
            elif {'icon', 'shortcut', 'apple-touch-icon',
                  'apple-touch-icon-precomposed', 'mask-icon'} & set(rels):
                self.icons.append((href, _declared_edge(a.get('sizes')), ' '.join(rels)))
        elif tag == 'meta':
            prop = (a.get('property') or a.get('name') or '').lower()
            content = a.get('content') or ''
            if content and prop in ('og:image', 'og:image:url',
                                    'og:image:secure_url', 'twitter:image'):
                self.og_images.append(content)

    def handle_endtag(self, tag):
        if tag == 'head':
            self.done = True


def _declared_edge(sizes):
    """Largest edge a sizes="" attribute claims, or 0 when it claims nothing.

    Only a hint for ordering. The real dimensions come from Pillow, because a
    site declaring sizes="192x192" on a 16x16 file is common.
    """
    best = 0
    for match in re.finditer(r'(\d+)\s*[xX]\s*(\d+)', sizes or ''):
        best = max(best, int(match.group(1)), int(match.group(2)))
    return best


def candidate_urls(base_url, html, manifest_icons=()):
    """Every image worth trying, best first.

    Ordering is the whole point. apple-touch-icon is nearly always a clean
    square logo at 180x180, so it comes first. Open Graph images come last of
    the declared ones because they are frequently a photograph or a banner
    rather than a mark. /favicon.ico is the final fallback and usually fails
    the size bar, which is the correct outcome.
    """
    parser = _HeadParser()
    try:
        parser.feed(html)
    except Exception:
        pass   # malformed markup: keep whatever was collected before the fault

    scored = []
    for href, edge, rel in parser.icons:
        if 'apple-touch-icon' in rel:
            rank = 0
        elif 'mask-icon' in rel:
            rank = 3       # usually a monochrome SVG silhouette
        else:
            rank = 1
        scored.append((rank, -edge, href, 'apple-touch-icon' if rank == 0 else 'link-icon'))

    for href, edge in manifest_icons:
        scored.append((1, -edge, href, 'manifest'))

    # og:image is a social sharing card. On an agency site it is very often a
    # photograph of a building, which would be shown as the company's logo and
    # look like the CRM had picked the wrong company. It is collected last and
    # never marked confident, so it can only ever be offered for a human to
    # approve — never displayed on its own.
    for href in parser.og_images:
        scored.append((4, 0, href, 'og:image'))

    scored.sort(key=lambda row: (row[0], row[1]))

    out, seen = [], set()
    for _rank, _edge, href, source in scored:
        url = _absolute(base_url, href)
        if url and url not in seen:
            seen.add(url)
            out.append((url, source))

    fallback = urljoin(base_url, '/favicon.ico')
    if fallback not in seen:
        out.append((fallback, 'favicon.ico'))

    return out[:MAX_CANDIDATES], parser.manifest


def _absolute(base_url, href):
    href = (href or '').strip()
    if not href or href.startswith('data:'):
        return None
    try:
        url = urljoin(base_url, href)
    except Exception:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return None
    return url


def manifest_icons(payload):
    """Icon entries from a web app manifest, as (href, edge) pairs."""
    out = []
    for entry in (payload or {}).get('icons') or []:
        if isinstance(entry, dict) and entry.get('src'):
            out.append((entry['src'], _declared_edge(entry.get('sizes'))))
    out.sort(key=lambda row: -row[1])
    return out[:4]


# ── Is this actually a logo? ─────────────────────────────────────────────────
def assess(data, declared_mime=''):
    """Decide whether these bytes are good enough to show as a logo.

    Returns (mime, width, height) on success, or raises ValueError with the
    reason. The reason is stored, so a rejection is explainable later without
    re-fetching anything.
    """
    if not data:
        raise ValueError('empty response')
    if len(data) > MAX_BYTES:
        raise ValueError('larger than 2 MB')

    mime = (declared_mime or '').split(';')[0].strip().lower()
    if mime in SVG_MIMES or data[:200].lstrip()[:5] in (b'<?xml', b'<svg'):
        cleaned = sanitise_svg(data)
        if cleaned is None:
            raise ValueError('SVG could not be made safe to serve')
        return 'image/svg+xml', None, None

    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or '').upper()
            if fmt == 'ICO':
                # An .ico is a container. Pillow reports the first frame, which
                # is usually the smallest; ask for the largest it holds.
                try:
                    im.size = max(im.ico.sizes())
                except Exception:
                    pass
            width, height = im.size
            im.load()
            flat = _is_flat(im)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f'not a readable image ({type(exc).__name__})')

    if fmt not in ('PNG', 'JPEG', 'WEBP', 'GIF', 'ICO'):
        raise ValueError(f'unsupported image format {fmt or "unknown"}')
    if width < MIN_EDGE or height < MIN_EDGE:
        # Catches the 1x1 tracking pixel and the 16x16 favicon in one rule.
        raise ValueError(f'too small ({width}x{height}, minimum {MIN_EDGE})')
    long_edge, short_edge = max(width, height), min(width, height)
    if short_edge and long_edge / short_edge > MAX_ASPECT:
        raise ValueError(f'wrong shape for a logo ({width}x{height})')
    if flat:
        # A single flat colour is a placeholder, a spacer or a blank default
        # icon. Showing it would look like a deliberate grey square.
        raise ValueError('single flat colour, not a logo')

    out_mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp',
                'GIF': 'image/gif', 'ICO': 'image/png'}[fmt]
    if fmt == 'ICO':
        # Re-encode an .ico to PNG so the browser and our own thumbnailer both
        # get a format they handle predictably.
        data, out_mime = _to_png(data, (width, height)), 'image/png'
    return out_mime, width, height


def _is_flat(im):
    """True when the image carries essentially no detail.

    Measured on the visible pixels only: a logo is usually a dark mark on a
    transparent background, and counting the transparent pixels would make
    every such logo look flat.
    """
    try:
        small = im.convert('RGBA').resize((16, 16))
        pixels = [p for p in small.getdata() if p[3] > 24]
        if len(pixels) < 8:
            return True            # almost entirely transparent
        return len({p[:3] for p in pixels}) <= 2
    except Exception:
        return False               # cannot tell: let it through


def _to_png(data, size):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        try:
            im.size = size
        except Exception:
            pass
        im = im.convert('RGBA')
        buf = io.BytesIO()
        im.save(buf, format='PNG', optimize=True)
        return buf.getvalue()


# ── SVG ───────────────────────────────────────────────────────────────────────
_SVG_DANGER = re.compile(
    rb'<\s*(script|foreignObject|iframe|embed|object|use)\b'
    rb'|\son[a-z]+\s*='
    rb'|javascript\s*:'
    rb'|<!ENTITY',
    re.IGNORECASE)


def sanitise_svg(data):
    """Return the SVG unchanged if it is plainly inert, otherwise None.

    An SVG is a document, not a picture. Served from the CRM's own origin it
    can run script in a logged-in session, so anything with a script, an event
    handler, an external reference or an entity declaration is refused outright
    rather than rewritten — rewriting an attacker's markup is a losing game and
    a company logo is never worth it. The route that serves it must also send
    a restrictive Content-Security-Policy and X-Content-Type-Options: nosniff.
    """
    if not data or len(data) > 512 * 1024:
        return None
    if b'<svg' not in data[:4096].lower() and b'<svg' not in data.lower():
        return None
    if _SVG_DANGER.search(data):
        return None
    return data


# ── Fetching ─────────────────────────────────────────────────────────────────
def _session():
    s = requests.Session()
    s.headers.update({'User-Agent': USER_AGENT, 'Accept': '*/*'})
    s.max_redirects = MAX_REDIRECTS
    return s


def _get(session, url, max_bytes, accept=None, truncate=False):
    """GET a URL, streamed and capped, with the host checked before connecting.

    Streamed because Content-Length is advisory: a server claiming 2 KB can
    send 2 GB, and read() without a cap would take the container's memory with
    it.

    truncate=True returns the bytes read so far once the cap is reached instead
    of failing. That is what we want for markup: every modern home page is far
    bigger than the cap, the <head> we need is in the first few kilobytes, and
    treating "page is big" as "no logo" loses the logo on most large sites.
    For an image the cap stays fatal, because half an image is no use.
    """
    host = urlsplit(url).hostname
    if not host or not is_public_host(host):
        raise ValueError('host is not on the public internet')
    headers = {'Accept': accept} if accept else {}
    resp = session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                       stream=True, allow_redirects=True, headers=headers)
    try:
        if resp.status_code != 200:
            raise ValueError(f'HTTP {resp.status_code}')
        # A redirect may have left the public internet, so check where we ended.
        final_host = urlsplit(resp.url).hostname
        if final_host != host and (not final_host or not is_public_host(final_host)):
            raise ValueError('redirected off the public internet')
        chunks, total = [], 0
        for chunk in resp.iter_content(16384):
            total += len(chunk)
            if total > max_bytes:
                if truncate:
                    chunks.append(chunk)
                    break
                raise ValueError('response too large')
            chunks.append(chunk)
        return b''.join(chunks), (resp.headers.get('Content-Type') or ''), resp.url
    finally:
        resp.close()


def _better(candidate, incumbent):
    """Is this candidate a better logo than the one we are holding?"""
    if candidate['confident'] != incumbent['confident']:
        return candidate['confident']
    return (candidate['width'] or 0) > (incumbent['width'] or 0)


def fetch_logo(website):
    """Find the best logo for a website. Never raises.

    Always returns a result dict. ok=True carries the bytes and the dimensions;
    ok=False carries a reason, and the caller stores that reason with a
    timestamp so the lookup is not repeated on the next page load.

    Only the company's own server is contacted. No third party is told which
    companies are on our books.
    """
    base = normalise_website(website)
    if not base:
        return _nothing('no usable website address')

    session = _session()
    html = ''
    try:
        html_bytes, _ctype, final = _get(session, base, MAX_HTML_BYTES,
                                         accept='text/html,*/*', truncate=True)
        base = final or base
        html = html_bytes.decode('utf-8', errors='replace')
    except Exception as exc:
        # The home page may be unreachable while /favicon.ico still is, so
        # carry on with no markup rather than giving up here.
        html = ''
        _ = exc

    candidates, manifest_href = candidate_urls(base, html)

    if manifest_href and len(candidates) <= 2:
        # Only worth a request when the markup gave us almost nothing.
        try:
            raw, _ctype, _final = _get(session, _absolute(base, manifest_href),
                                       64 * 1024, accept='application/json')
            import json
            icons = manifest_icons(json.loads(raw.decode('utf-8', errors='replace')))
            extra, _ = candidate_urls(base, html, manifest_icons=icons)
            candidates = extra
        except Exception:
            pass

    best, reasons = None, []
    for url, source in candidates:
        try:
            data, ctype, _final = _get(session, url, MAX_BYTES, accept='image/*')
            mime, width, height = assess(data, ctype)
        except Exception as exc:
            reasons.append(f'{source}: {exc}')
            continue
        result = _found(data, mime, width, height, source, url)
        if mime == 'image/svg+xml':
            # Keep it, but keep looking for a raster we can measure.
            best = best or result
            continue
        # A confident source always beats a non-confident one, however large
        # the non-confident image is. Otherwise a 1024px marketing photograph
        # from og:image would outrank the company's own 180px apple-touch-icon.
        if best is None or _better(result, best):
            best = result
        if result['confident'] and (width or 0) >= PREFERRED_EDGE:
            break      # good enough; stop making requests of their server

    if best is not None:
        return best
    return _nothing('; '.join(reasons[:4]) or 'no icon declared and no favicon')
