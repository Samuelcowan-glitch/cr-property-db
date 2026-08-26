"""Microsoft 365 for the Cowan & Rutter database.

One place for talking to Microsoft Graph on behalf of the office mailbox
(bc@cowanandrutter.co.uk by default). The mail sync has its own module and
its own history; this holds the token, the calendar and the contacts, so
everything new shares one connection and one set of credentials.

Configured entirely by environment variables, and every call degrades to
"not connected" rather than raising, so the CRM works unchanged until the
Azure app registration is in place:

    MS_TENANT_ID      the directory (tenant) id
    MS_CLIENT_ID      the application (client) id
    MS_CLIENT_SECRET  a client secret
    MS_EMAIL          the mailbox to act on, default bc@cowanandrutter.co.uk

The application permissions this needs, granted with admin consent:

    Mail.Read, Mail.Send        already used by the inbox sync
    Calendars.ReadWrite         the diary
    Contacts.ReadWrite          the address book
    User.Read.All               to confirm the mailbox exists

Nothing here stores a password, and no token is ever written to the database
or the logs.
"""
import os
import time
import requests as http

GRAPH = 'https://graph.microsoft.com/v1.0'
TIMEOUT = 20

TENANT_ID = os.environ.get('MS_TENANT_ID')
CLIENT_ID = os.environ.get('MS_CLIENT_ID')
CLIENT_SECRET = os.environ.get('MS_CLIENT_SECRET')
MAILBOX = os.environ.get('MS_EMAIL', 'bc@cowanandrutter.co.uk')

_token = {}


class GraphError(RuntimeError):
    """Microsoft refused or could not answer. Carries the readable reason."""


def is_configured():
    """Whether there is enough to try at all."""
    return all([TENANT_ID, CLIENT_ID, CLIENT_SECRET])


def _access_token():
    if _token.get('expires_at', 0) > time.time() + 60:
        return _token['token']
    if not is_configured():
        raise GraphError('Microsoft 365 is not connected yet.')
    r = http.post(
        f'https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token',
        data={'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
              'scope': 'https://graph.microsoft.com/.default',
              'grant_type': 'client_credentials'},
        timeout=TIMEOUT)
    if r.status_code != 200:
        raise GraphError(_reason(r, 'Could not sign in to Microsoft 365'))
    j = r.json()
    _token['token'] = j['access_token']
    _token['expires_at'] = time.time() + j.get('expires_in', 3600)
    return _token['token']


def _reason(resp, fallback):
    """The message Microsoft gave, if it gave one."""
    try:
        err = resp.json().get('error', {})
        msg = err.get('message') or err.get('error_description') or ''
    except Exception:
        msg = ''
    return f'{fallback}: {msg}'.strip(': ') if msg else f'{fallback} ({resp.status_code}).'


def call(method, path, **kw):
    """One Graph request. Returns the parsed body, or {} for an empty reply."""
    url = path if path.startswith('http') else f'{GRAPH}{path}'
    headers = kw.pop('headers', {})
    headers['Authorization'] = f'Bearer {_access_token()}'
    if 'json' in kw:
        headers.setdefault('Content-Type', 'application/json')
    r = http.request(method, url, headers=headers, timeout=TIMEOUT, **kw)
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise GraphError(_reason(r, 'Microsoft 365 refused the request'))
    if r.status_code == 204 or not r.content:
        return {}
    return r.json()


# ── Connection ───────────────────────────────────────────────────────────────

def connection_status():
    """What is working, for the settings page. Never raises."""
    state = {'configured': is_configured(), 'mailbox': MAILBOX,
             'tenant': TENANT_ID, 'checks': [], 'ok': False}
    if not is_configured():
        state['checks'].append(('Credentials', False,
                                'Set MS_TENANT_ID, MS_CLIENT_ID and MS_CLIENT_SECRET.'))
        return state

    def check(label, fn, hint):
        try:
            fn()
            state['checks'].append((label, True, ''))
            return True
        except GraphError as e:
            state['checks'].append((label, False, f'{e} {hint}'.strip()))
        except Exception as e:                       # network, DNS, anything
            state['checks'].append((label, False, f'{e}'))
        return False

    signed_in = check('Sign in', _access_token, '')
    if not signed_in:
        return state
    check('Mailbox', lambda: call('GET', f'/users/{MAILBOX}?$select=id,mail'),
          'Check MS_EMAIL and the User.Read.All permission.')
    check('Calendar', lambda: call('GET', f'/users/{MAILBOX}/calendar?$select=id'),
          'Grant Calendars.ReadWrite and admin consent.')
    check('Contacts', lambda: call('GET', f'/users/{MAILBOX}/contacts?$top=1&$select=id'),
          'Grant Contacts.ReadWrite and admin consent.')
    state['ok'] = all(ok for _, ok, _ in state['checks'])
    return state


# ── Calendar ─────────────────────────────────────────────────────────────────

def _event_body(title, start_utc, end_utc, location=None, notes=None, all_day=False):
    """A diary appointment in the shape Graph expects. Times are UTC."""
    body = {
        'subject': title or 'Appointment',
        'start': {'dateTime': start_utc.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'UTC'},
        'end': {'dateTime': end_utc.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'UTC'},
        'isAllDay': bool(all_day),
    }
    if location:
        body['location'] = {'displayName': location}
    if notes:
        body['body'] = {'contentType': 'text', 'content': notes}
    return body


def create_event(title, start_utc, end_utc, location=None, notes=None, all_day=False):
    """Put an appointment in the mailbox's calendar. Returns (id, etag)."""
    j = call('POST', f'/users/{MAILBOX}/events',
             json=_event_body(title, start_utc, end_utc, location, notes, all_day))
    return (j or {}).get('id'), (j or {}).get('@odata.etag')


def update_event(event_id, title, start_utc, end_utc, location=None, notes=None,
                 all_day=False):
    """Change an appointment already in the calendar. Returns the new etag."""
    j = call('PATCH', f'/users/{MAILBOX}/events/{event_id}',
             json=_event_body(title, start_utc, end_utc, location, notes, all_day))
    if j is None:
        return None                                   # gone from Outlook
    return j.get('@odata.etag')


def delete_event(event_id):
    """Remove an appointment from the calendar. Missing is a success."""
    call('DELETE', f'/users/{MAILBOX}/events/{event_id}')
    return True


def list_events(start_utc, end_utc, top=250):
    """Appointments in the mailbox's calendar between two UTC moments."""
    path = (f'/users/{MAILBOX}/calendarView'
            f'?startDateTime={start_utc.strftime("%Y-%m-%dT%H:%M:%S")}Z'
            f'&endDateTime={end_utc.strftime("%Y-%m-%dT%H:%M:%S")}Z'
            f'&$top={top}&$orderby=start/dateTime'
            f'&$select=id,subject,start,end,isAllDay,location,bodyPreview,organizer')
    j = call('GET', path, headers={'Prefer': 'outlook.timezone="UTC"'})
    return (j or {}).get('value', [])


# ── Contacts ─────────────────────────────────────────────────────────────────

def _contact_body(contact):
    """A CRM contact in the shape Graph expects."""
    body = {
        'givenName': contact.first_name or '',
        'surname': contact.last_name or '',
    }
    if contact.email:
        body['emailAddresses'] = [{'address': contact.email,
                                   'name': contact.full_name}]
    if contact.mobile:
        body['mobilePhone'] = contact.mobile
    if contact.phone:
        body['businessPhones'] = [contact.phone]
    if getattr(contact, 'job_title', None):
        body['jobTitle'] = contact.job_title
    org = getattr(contact, 'organisation', None)
    if org is not None and getattr(org, 'name', None):
        body['companyName'] = org.name
    if contact.contact_type:
        body['personalNotes'] = f'Cowan & Rutter — {contact.contact_type}'
    return body


def create_contact(contact):
    """Add a CRM contact to the mailbox's address book. Returns its id."""
    j = call('POST', f'/users/{MAILBOX}/contacts', json=_contact_body(contact))
    return (j or {}).get('id')


def update_contact(ms_id, contact):
    """Update one already there. Returns False if Outlook no longer has it."""
    j = call('PATCH', f'/users/{MAILBOX}/contacts/{ms_id}', json=_contact_body(contact))
    return j is not None


def delete_contact(ms_id):
    call('DELETE', f'/users/{MAILBOX}/contacts/{ms_id}')
    return True
