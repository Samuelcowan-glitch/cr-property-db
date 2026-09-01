import os
import requests as http
from datetime import datetime

TENANT_ID     = os.environ.get('MS_TENANT_ID')
CLIENT_ID     = os.environ.get('MS_CLIENT_ID')
CLIENT_SECRET = os.environ.get('MS_CLIENT_SECRET')
MAILBOX       = os.environ.get('MS_EMAIL', 'bc@cowanandrutter.co.uk')

_token_cache = {}


def check_configured():
    return all([TENANT_ID, CLIENT_ID, CLIENT_SECRET])


def _get_token():
    import time
    if _token_cache.get('expires_at', 0) > time.time() + 60:
        return _token_cache['token']
    resp = http.post(
        f'https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token',
        data={
            'client_id':     CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'scope':         'https://graph.microsoft.com/.default',
            'grant_type':    'client_credentials',
        }
    )
    resp.raise_for_status()
    j = resp.json()
    _token_cache['token']      = j['access_token']
    _token_cache['expires_at'] = time.time() + j['expires_in']
    return _token_cache['token']


def _headers():
    return {'Authorization': f'Bearer {_get_token()}'}


def fetch_recent_emails(top=50):
    # `body` is included so portal lead emails can be parsed in full — the
    # applicant's phone number and message sit well past bodyPreview's 255
    # characters, which is why Zoopla enquiries used to arrive gutted.
    resp = http.get(
        f'https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages',
        headers=_headers(),
        params={
            '$top':     top,
            '$orderby': 'receivedDateTime desc',
            '$select':  'id,subject,from,toRecipients,receivedDateTime,bodyPreview,body,isRead',
        }
    )
    resp.raise_for_status()
    return resp.json().get('value', [])


def send_email(to_address, subject, body_html):
    resp = http.post(
        f'https://graph.microsoft.com/v1.0/users/{MAILBOX}/sendMail',
        headers={**_headers(), 'Content-Type': 'application/json'},
        json={
            'message': {
                'subject': subject,
                'body': {'contentType': 'HTML', 'content': body_html},
                'toRecipients': [{'emailAddress': {'address': to_address}}],
            },
            'saveToSentItems': True,
        }
    )
    resp.raise_for_status()


# Populated by the background poller so the UI can show when mail was last
# checked, and why it failed if it did.
LAST_SYNC = {'at': None, 'emails': 0, 'leads': 0, 'error': None}


def start_background_sync(app, db, Contact, Enquiry, EnquiryNote):
    """Poll the mailbox on a timer so leads arrive without anyone clicking.

    Interval comes from EMAIL_SYNC_MINUTES (default 10; set to 0 to disable).
    Runs as a daemon thread inside the web process — waitress serves from a
    single process, so there is exactly one poller, and ms_message_id is unique
    anyway, so a duplicate run could not double-file a lead.
    """
    import threading
    import time as _time

    minutes = int(os.environ.get('EMAIL_SYNC_MINUTES', '10') or 0)
    if minutes <= 0 or not check_configured():
        return False

    def worker():
        while True:
            _time.sleep(minutes * 60)
            try:
                with app.app_context():
                    counts = sync_inbox(db, Contact, Enquiry, EnquiryNote)
                LAST_SYNC.update(at=datetime.utcnow(), error=None, **counts)
                if counts['leads'] or counts['emails']:
                    print(f"Email sync: {counts['emails']} email(s), {counts['leads']} lead(s)")
            except Exception as ex:                     # never kill the thread
                LAST_SYNC.update(at=datetime.utcnow(), error=str(ex))
                print(f'Email sync failed: {ex}')

    threading.Thread(target=worker, daemon=True).start()
    print(f'Email sync: polling {MAILBOX} every {minutes} min')
    return True


def _body_text(msg):
    """Plain-text version of a Graph message body, falling back to the preview."""
    import portal_leads
    body = (msg.get('body') or {})
    content = body.get('content') or ''
    if not content:
        return msg.get('bodyPreview') or ''
    if (body.get('contentType') or '').lower() == 'html' or '<' in content:
        return portal_leads.html_to_text(content)
    return content


def _ingest_portal_lead(db, Contact, Enquiry, EnquiryNote, parsed, subject,
                        body_text, mid, received_at):
    """Create a Contact + Enquiry for the *applicant* named in a portal email.

    Imported late from app to avoid a circular import — app imports this module
    inside its request handlers.
    """
    from app import (Property, Project, Listing, ProjectApplicant,
                     match_property_from_text)

    portal = parsed['portal']

    # ── Which property is this about? ────────────────────────────────────────
    listing = None
    prop = None
    if parsed['listing_id']:
        listing = Listing.query.get(parsed['listing_id'])
    if listing and listing.property_id:
        prop = Property.query.get(listing.property_id)
    if not prop and parsed['property_text']:
        prop = match_property_from_text(parsed['property_text'])
    if not prop:
        prop = match_property_from_text(subject or '')

    project = None
    if listing and listing.project_id:
        project = Project.query.get(listing.project_id)
    if not project and prop:
        project = Project.query.filter_by(property_id=prop.id, status='Active').first()

    is_sale = bool(listing and listing.listing_price_unit == 'sale')
    contact_type = 'Prospective Buyer' if is_sale else 'Prospective Tenant'

    # ── The applicant ────────────────────────────────────────────────────────
    name = parsed['name'] or 'Unknown'
    parts = name.split(' ', 1)
    contact = None
    if parsed['email']:
        contact = Contact.query.filter_by(email=parsed['email']).first()
    if not contact:
        contact = Contact(
            first_name=parts[0] or 'Unknown',
            last_name=parts[1] if len(parts) > 1 else '.',
            email=parsed['email'],
            phone=parsed['phone'],
            contact_type=contact_type,
        )
        db.session.add(contact)
        db.session.flush()
    else:
        if parsed['phone'] and not contact.phone:
            contact.phone = parsed['phone']
        if contact.contact_type in (None, 'Enquiry', 'Prospect', 'Other'):
            contact.contact_type = contact_type

    # The enquirer's own organisation, where they are already on record with
    # one. Never the agency's, and never the project's.
    org_id = getattr(contact, 'organisation_id', None)

    # ── The enquiry ──────────────────────────────────────────────────────────
    where = parsed['property_text'] or (prop.address if prop else None)
    enq_subject = f"{portal} — {where[:80]}" if where else f"{portal} — {subject[:80]}"

    enquiry = Enquiry(
        subject=enq_subject,
        # Filed under the same headings as a manually entered enquiry, so the
        # register reads consistently whichever way the lead arrived.
        enquiry_type=('Buyer — Looking to Buy' if is_sale
                      else 'Tenant — Looking to Rent'),
        status='Open',
        source=portal,
        contact_id=contact.id,
        organisation_id=org_id,
        property_id=prop.id if prop else None,
        project_id=project.id if project else None,
        notes=parsed['message'] or None,
        received_date=received_at.date(),
        last_contact_date=received_at.date(),
    )
    db.session.add(enquiry)
    db.session.flush()

    db.session.add(EnquiryNote(
        enquiry_id=enquiry.id,
        direction='inbound',
        subject=subject,
        body=body_text[:20000],
        author=parsed['name'] or portal,
        ms_message_id=mid,
    ))

    # Register the applicant against the instruction, as a website enquiry does.
    if project:
        already = ProjectApplicant.query.filter_by(
            project_id=project.id, contact_id=contact.id).first()
        if not already:
            db.session.add(ProjectApplicant(
                project_id=project.id, contact_id=contact.id,
                status='Active Applicant', auto_linked=True,
                notes=f'Enquired via {portal}',
            ))

    return enquiry


def sync_inbox(db, Contact, Enquiry, EnquiryNote):
    """Pull new mail into the CRM. Returns {'emails': n, 'leads': n}."""
    import portal_leads

    msgs = fetch_recent_emails(top=50)
    counts = {'emails': 0, 'leads': 0}

    for msg in msgs:
        mid = msg['id']
        if EnquiryNote.query.filter_by(ms_message_id=mid).first():
            continue

        from_addr = msg['from']['emailAddress']['address'].lower()
        from_name = msg['from']['emailAddress'].get('name', from_addr)
        subject   = msg.get('subject') or '(no subject)'
        preview   = msg.get('bodyPreview') or ''
        body_text = _body_text(msg)
        received_str = msg.get('receivedDateTime', '')

        try:
            received_at = datetime.strptime(received_str[:19], '%Y-%m-%dT%H:%M:%S')
        except Exception:
            received_at = datetime.utcnow()

        # Skip emails sent from the mailbox itself
        if from_addr == MAILBOX.lower():
            continue

        # Portal leads (Zoopla, Rightmove, OnTheMarket) name the applicant in
        # the body, not the sender — they get their own enquiry per lead.
        if portal_leads.is_lead_email(from_addr, subject, body_text):
            parsed = portal_leads.parse_lead(subject, body_text, from_addr, from_name)
            _ingest_portal_lead(db, Contact, Enquiry, EnquiryNote, parsed,
                                subject, body_text, mid, received_at)
            counts['leads'] += 1
            continue

        # Find or create contact
        contact = Contact.query.filter_by(email=from_addr).first()
        if not contact:
            parts   = from_name.split(' ', 1)
            contact = Contact(
                first_name=parts[0] or 'Unknown',
                last_name=parts[1] if len(parts) > 1 else '.',
                email=from_addr,
                contact_type='Prospect',
            )
            db.session.add(contact)
            db.session.flush()

        # Find most recent open enquiry for this contact, or create one
        enquiry = Enquiry.query.filter_by(
            contact_id=contact.id, status='Open'
        ).order_by(Enquiry.created_at.desc()).first()

        if not enquiry:
            enquiry = Enquiry(
                subject=f'Email — {subject[:100]}',
                enquiry_type='Other',
                status='Open',
                source='Email',
                contact_id=contact.id,
                received_date=received_at.date(),
            )
            db.session.add(enquiry)
            db.session.flush()

        note = EnquiryNote(
            enquiry_id=enquiry.id,
            direction='inbound',
            subject=subject,
            body=(body_text or preview)[:20000],
            author=from_name,
            ms_message_id=mid,
        )
        db.session.add(note)
        enquiry.last_contact_date = received_at.date()
        counts['emails'] += 1

    db.session.commit()
    return counts
