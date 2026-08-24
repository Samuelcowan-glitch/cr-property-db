"""Parse property-portal lead emails (Zoopla, Rightmove, OnTheMarket).

Zoopla does not post enquiries to us — it emails them to the branch mailbox.
Those emails name the *applicant* in the body while the sender is the portal,
so the generic inbox sync would file every lead under one "Zoopla" contact and
throw away everything past the preview text. This module pulls the applicant's
details out of the email so a real Contact + Enquiry can be created.

Portals change their templates from time to time. Parsing is therefore
deliberately forgiving: each field has a labelled pattern and a positional
fallback, and the caller always stores the full email body on the enquiry, so
a missed field is an inconvenience rather than a lost lead.
"""
import re
from html import unescape

PORTAL_SENDERS = {
    'zoopla': 'Zoopla',
    'zpg': 'Zoopla',
    'rightmove': 'Rightmove',
    'onthemarket': 'OnTheMarket',
}

# Portal addresses that appear inside the body and must never be mistaken for
# the applicant's own email.
_PORTAL_EMAIL_HINTS = ('zoopla', 'zpg', 'rightmove', 'onthemarket', 'noreply',
                       'no-reply', 'donotreply', 'do-not-reply')

_UK_PHONE = re.compile(r'(?:(?:\+44\s?|0)(?:\d\s?){9,12})')
_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_POSTCODE = re.compile(r'\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b', re.I)
_AGENT_REF = re.compile(r'\bCR-(\d+)\b', re.I)


def detect_portal(from_address, subject=''):
    """Return 'Zoopla' / 'Rightmove' / 'OnTheMarket', or None if not a portal."""
    haystack = f'{from_address or ""} {subject or ""}'.lower()
    for needle, name in PORTAL_SENDERS.items():
        if needle in haystack:
            return name
    return None


def is_lead_email(from_address, subject='', body_text=''):
    """True when the message looks like an applicant enquiry, not a portal notice.

    Portals also send invoices, performance reports and marketing from the same
    domains, so a portal sender alone is not enough.
    """
    if not detect_portal(from_address, subject):
        return False
    blob = f'{subject or ""} {body_text or ""}'.lower()
    lead_words = ('enquiry', 'enquiries', 'inquiry', 'lead', 'interested in',
                  'viewing', 'wants to view', 'has contacted', 'new applicant',
                  'property alert response', 'request details')
    not_lead = ('invoice', 'statement', 'performance report', 'monthly report',
                'your account', 'password', 'newsletter', 'webinar')
    if any(w in blob for w in not_lead) and not any(w in blob for w in lead_words):
        return False
    return any(w in blob for w in lead_words)


def html_to_text(html):
    """Flatten an HTML email body to plain text, keeping line structure."""
    if not html:
        return ''
    text = re.sub(r'(?is)<(script|style).*?</\1>', ' ', html)
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(p|div|tr|li|h[1-6]|table)>', '\n', text)
    text = re.sub(r'(?i)</t[dh]>', ': ', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = unescape(text)
    text = text.replace(' ', ' ')
    lines = [re.sub(r'[ \t]+', ' ', ln).strip(' :–-') for ln in text.splitlines()]
    return '\n'.join(ln for ln in lines if ln)


def _labelled(text, labels, same_line_only=True):
    """Find `Label: value`, or the line following a label on its own line."""
    for label in labels:
        m = re.search(rf'(?im)^\s*{label}\s*[:\-–]\s*(.+?)\s*$', text)
        if m and m.group(1).strip():
            return m.group(1).strip()
    if not same_line_only:
        for label in labels:
            m = re.search(rf'(?im)^\s*{label}\s*$\n(.+?)$', text)
            if m and m.group(1).strip():
                return m.group(1).strip()
    return None


def _clean_name(value):
    if not value:
        return None
    value = re.sub(r'\s+', ' ', value).strip(' .,')
    # Drop anything trailing after an email address or phone number
    value = _EMAIL.sub('', value)
    value = _UK_PHONE.sub('', value)
    value = value.strip(' .,|-')
    if not value or len(value) > 80:
        return None
    if value.lower() in ('name', 'applicant', 'customer', 'unknown'):
        return None
    return value


def _applicant_email(text):
    labelled = _labelled(text, ['e-?mail(?: address)?', 'email'], same_line_only=False)
    if labelled:
        found = _EMAIL.search(labelled)
        if found and not _is_portal_email(found.group(0)):
            return found.group(0).lower()
    for candidate in _EMAIL.findall(text):
        if not _is_portal_email(candidate):
            return candidate.lower()
    return None


def _is_portal_email(address):
    low = address.lower()
    return any(h in low for h in _PORTAL_EMAIL_HINTS)


def _applicant_phone(text):
    labelled = _labelled(text, ['telephone(?: number)?', 'tel', 'phone(?: number)?',
                                'mobile', 'contact number'], same_line_only=False)
    source = labelled or text
    m = _UK_PHONE.search(source)
    if not m and labelled:
        m = _UK_PHONE.search(text)
    if not m:
        return None
    phone = re.sub(r'\s+', ' ', m.group(0)).strip()
    digits = re.sub(r'\D', '', phone)
    if len(digits) < 10 or len(digits) > 13:
        return None
    return phone


def _message(text):
    labelled = _labelled(text, ['message', 'comments?', 'their message',
                                'enquiry(?: details)?', 'note'], same_line_only=False)
    if labelled and len(labelled) > 3:
        return labelled
    # Fall back to the longest sentence-like line that is not a labelled field
    candidates = [ln for ln in text.splitlines()
                  if len(ln) > 40 and ':' not in ln[:20] and not _EMAIL.search(ln)]
    return max(candidates, key=len) if candidates else None


def _property_text(text, subject):
    labelled = _labelled(text, ['property', 'address', 'regarding', 'listing',
                                'property address'], same_line_only=False)
    if labelled:
        return labelled
    m = _POSTCODE.search(text)
    if m:
        line = next((ln for ln in text.splitlines() if m.group(0) in ln), None)
        if line:
            # "Frank Churchill has enquired about 10 Plato Place, SW6 2PY"
            # → keep only the property part.
            tail = re.search(r'(?i)\b(?:enquired about|interested in|regarding|about)\b\s*(.+)$', line)
            return (tail.group(1) if tail else line).strip()
    # Subjects often read "Enquiry about 57B New Kings Road, Chelsea"
    m = re.search(r'(?i)(?:about|for|re:?)\s+(.{6,90})$', subject or '')
    return m.group(1).strip() if m else None


def parse_lead(subject, body, from_address='', from_name=''):
    """Return a dict of applicant details extracted from a portal lead email.

    Keys: portal, name, email, phone, message, property_text, listing_id.
    Every value may be None — the caller keeps the raw body regardless.
    """
    text = html_to_text(body) if '<' in (body or '') else (body or '')
    portal = detect_portal(from_address, subject) or 'Portal'

    name = _clean_name(_labelled(text, ['name', 'full name', 'applicant(?: name)?',
                                        'customer(?: name)?', 'from'],
                                 same_line_only=False))
    if not name:
        # "John Smith has enquired about ..." / "Enquiry from John Smith"
        # [ \t] rather than \s so a name can never run across a line break.
        m = re.search(r'(?i)(?:enquiry|enquired|message|lead)[ \t]+from[ \t]+([A-Z][\w\'-]+(?:[ \t]+[A-Z][\w\'-]+){0,3})', f'{subject}\n{text}')
        if not m:
            m = re.search(r'(?im)^([A-Z][\w\'-]+(?:\s+[A-Z][\w\'-]+){0,3})\s+(?:has\s+)?(?:enquired|is interested|would like)', text)
        name = _clean_name(m.group(1)) if m else None

    ref = _AGENT_REF.search(f'{subject}\n{text}')

    return {
        'portal':        portal,
        'name':          name,
        'email':         _applicant_email(text),
        'phone':         _applicant_phone(text),
        'message':       _message(text),
        'property_text': _property_text(text, subject),
        'listing_id':    int(ref.group(1)) if ref else None,
        'text':          text,
    }


if __name__ == '__main__':
    sample = """
    <html><body>
    <p>You have a new enquiry from Zoopla.</p>
    <table>
      <tr><td>Name</td><td>Jane Fairfax</td></tr>
      <tr><td>Email</td><td>jane.fairfax@example.com</td></tr>
      <tr><td>Telephone</td><td>07700 900123</td></tr>
      <tr><td>Property</td><td>57B New Kings Road, Chelsea, SW6 4SE (ref CR-45)</td></tr>
      <tr><td>Message</td><td>I would like to arrange a viewing this week if possible.</td></tr>
    </table>
    <p>Sent by Zoopla, noreply@zoopla.co.uk</p>
    </body></html>
    """
    assert is_lead_email('noreply@zoopla.co.uk', 'New enquiry for 57B New Kings Road', sample)
    assert not is_lead_email('billing@zoopla.co.uk', 'Your monthly invoice', 'invoice attached')
    assert not is_lead_email('someone@example.com', 'Hello', 'just a normal email')
    parsed = parse_lead('New enquiry for 57B New Kings Road', sample, 'noreply@zoopla.co.uk')
    assert parsed['name'] == 'Jane Fairfax', parsed
    assert parsed['email'] == 'jane.fairfax@example.com', parsed
    assert parsed['phone'] == '07700 900123', parsed
    assert parsed['listing_id'] == 45, parsed
    assert 'viewing' in (parsed['message'] or ''), parsed
    assert 'New Kings Road' in (parsed['property_text'] or ''), parsed

    plain = ("Enquiry from Frank Churchill\n"
             "Frank Churchill has enquired about Unit 10, Plato Place, 72-74 St Dionis Road, SW6 2PY\n"
             "Email: frank@example.co.uk\n"
             "Tel: 0207 946 0123\n"
             "Message: Is the unit still available from September?\n"
             "This lead was sent to you by Zoopla.")
    assert is_lead_email('leads@zoopla.co.uk', 'New lead', plain)
    p2 = parse_lead('New lead', plain, 'leads@zoopla.co.uk')
    assert p2['name'] == 'Frank Churchill', p2
    assert p2['email'] == 'frank@example.co.uk', p2
    assert p2['phone'] == '0207 946 0123', p2
    assert 'September' in (p2['message'] or ''), p2
    print('portal_leads: all checks passed')
