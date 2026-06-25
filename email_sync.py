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
    resp = http.get(
        f'https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages',
        headers=_headers(),
        params={
            '$top':     top,
            '$orderby': 'receivedDateTime desc',
            '$select':  'id,subject,from,toRecipients,receivedDateTime,bodyPreview,isRead',
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


def sync_inbox(db, Contact, Enquiry, EnquiryNote):
    msgs  = fetch_recent_emails(top=50)
    count = 0

    for msg in msgs:
        mid = msg['id']
        if EnquiryNote.query.filter_by(ms_message_id=mid).first():
            continue

        from_addr = msg['from']['emailAddress']['address'].lower()
        from_name = msg['from']['emailAddress'].get('name', from_addr)
        subject   = msg.get('subject') or '(no subject)'
        preview   = msg.get('bodyPreview') or ''
        received_str = msg.get('receivedDateTime', '')

        try:
            received_at = datetime.strptime(received_str[:19], '%Y-%m-%dT%H:%M:%S')
        except Exception:
            received_at = datetime.utcnow()

        # Skip emails sent from the mailbox itself
        if from_addr == MAILBOX.lower():
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
                contact_id=contact.id,
                received_date=received_at.date(),
            )
            db.session.add(enquiry)
            db.session.flush()

        note = EnquiryNote(
            enquiry_id=enquiry.id,
            direction='inbound',
            subject=subject,
            body=preview,
            author=from_name,
            ms_message_id=mid,
        )
        db.session.add(note)
        enquiry.last_contact_date = received_at.date()
        count += 1

    db.session.commit()
    return count
