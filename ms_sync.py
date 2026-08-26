"""Keeping the CRM and Microsoft 365 in step.

The Graph calls live in ms_graph.py; this decides what to send and when. It
is deliberately one-way where two-way would be ambiguous:

  Diary  → Outlook   every CRM appointment is mirrored, and kept in step when
                     it is edited, moved or deleted.
  Outlook → Diary    appointments made in Outlook are pulled in and shown, but
                     are not editable here — the CRM is not their home.
  Contacts → Outlook a CRM contact is mirrored into the office address book so
                     it is there on a phone.

Nothing here raises into a request: every entry point returns a short report
and swallows the failure, so a Microsoft outage can never stop somebody
saving a record in the CRM.
"""
from datetime import datetime, timedelta

import ms_graph


def _log(app, message):
    try:
        app.logger.info('Microsoft 365: %s', message)
    except Exception:
        pass


# ── Diary → Outlook ──────────────────────────────────────────────────────────

def push_event(app, db, ev):
    """Mirror one CRM appointment into Outlook, creating or updating it.

    Returns (True, note) if the calendar now matches, (False, why) if not.
    Never raises: a failed sync leaves the appointment untouched in the CRM.
    """
    if not ms_graph.is_configured():
        return False, 'Microsoft 365 is not connected.'
    if getattr(ev, 'from_outlook', False) and not ev.ms_event_id:
        return False, 'This came from Outlook; it is not ours to push.'
    try:
        title = ev.title or 'Appointment'
        where = ev.place if hasattr(ev, 'place') else (ev.location or '')
        if ev.ms_event_id:
            etag = ms_graph.update_event(ev.ms_event_id, title, ev.start_at, ev.end_at,
                                         where, ev.notes, ev.all_day)
            if etag is None:
                # Someone deleted it in Outlook; put it back rather than lose it.
                ev.ms_event_id, ev.ms_etag = ms_graph.create_event(
                    title, ev.start_at, ev.end_at, where, ev.notes, ev.all_day)
            else:
                ev.ms_etag = etag
        else:
            ev.ms_event_id, ev.ms_etag = ms_graph.create_event(
                title, ev.start_at, ev.end_at, where, ev.notes, ev.all_day)
        ev.synced_at = datetime.utcnow()
        db.session.commit()
        return True, 'In the Outlook calendar.'
    except Exception as e:
        db.session.rollback()
        _log(app, f'could not push appointment {ev.id}: {e}')
        return False, str(e)


def remove_event(app, db, ev):
    """Take a deleted CRM appointment out of Outlook too."""
    if not (ms_graph.is_configured() and ev.ms_event_id):
        return False, 'Nothing to remove.'
    try:
        ms_graph.delete_event(ev.ms_event_id)
        return True, 'Removed from the Outlook calendar.'
    except Exception as e:
        _log(app, f'could not remove appointment {ev.id}: {e}')
        return False, str(e)


# ── Outlook → Diary ──────────────────────────────────────────────────────────

def pull_events(app, db, DiaryEvent, days_back=30, days_ahead=180):
    """Bring appointments made in Outlook into the diary.

    Matched on the Outlook id, so running this repeatedly updates rather than
    duplicates. Appointments the CRM created are skipped — they are already
    ours and pushing is what keeps them right.
    """
    if not ms_graph.is_configured():
        return {'added': 0, 'updated': 0, 'error': 'Microsoft 365 is not connected.'}
    now = datetime.utcnow()
    try:
        remote = ms_graph.list_events(now - timedelta(days=days_back),
                                      now + timedelta(days=days_ahead))
    except Exception as e:
        _log(app, f'could not read the calendar: {e}')
        return {'added': 0, 'updated': 0, 'error': str(e)}

    ours = {e.ms_event_id: e for e in
            DiaryEvent.query.filter(DiaryEvent.ms_event_id.isnot(None)).all()}
    added = updated = 0
    for item in remote:
        ms_id = item.get('id')
        if not ms_id:
            continue
        start = _parse(item.get('start', {}).get('dateTime'))
        end = _parse(item.get('end', {}).get('dateTime'))
        if not (start and end):
            continue
        title = item.get('subject') or 'Appointment'
        where = (item.get('location') or {}).get('displayName') or ''
        notes = item.get('bodyPreview') or None
        existing = ours.get(ms_id)
        if existing:
            # Only touch the ones Outlook owns; ours are pushed, not pulled.
            if existing.from_outlook_only:
                existing.title, existing.start_at, existing.end_at = title, start, end
                existing.location, existing.notes = where, notes
                existing.all_day = bool(item.get('isAllDay'))
                existing.synced_at = now
                updated += 1
            continue
        ev = DiaryEvent(title=title, start_at=start, end_at=end,
                        all_day=bool(item.get('isAllDay')), event_type='appointment',
                        owner=(item.get('organizer', {}).get('emailAddress', {}) or {}).get('name'),
                        location=where, notes=notes,
                        ms_event_id=ms_id, ms_etag=item.get('@odata.etag'),
                        synced_at=now, created_by='Outlook')
        db.session.add(ev)
        added += 1
    db.session.commit()
    return {'added': added, 'updated': updated, 'error': None}


def _parse(value):
    """Graph hands back 2026-08-26T09:00:00.0000000 in UTC."""
    if not value:
        return None
    text = value.split('.')[0].replace('Z', '')
    try:
        return datetime.strptime(text, '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        return None


# ── Contacts → Outlook ───────────────────────────────────────────────────────

def push_contact(app, db, contact):
    """Mirror one CRM contact into the office address book."""
    if not ms_graph.is_configured():
        return False, 'Microsoft 365 is not connected.'
    if not (contact.first_name or contact.last_name):
        return False, 'A contact needs a name before it can be shared.'
    try:
        if contact.ms_contact_id:
            if not ms_graph.update_contact(contact.ms_contact_id, contact):
                contact.ms_contact_id = ms_graph.create_contact(contact)
        else:
            contact.ms_contact_id = ms_graph.create_contact(contact)
        contact.ms_synced_at = datetime.utcnow()
        db.session.commit()
        return True, 'In the Outlook address book.'
    except Exception as e:
        db.session.rollback()
        _log(app, f'could not push contact {contact.id}: {e}')
        return False, str(e)


def push_all_contacts(app, db, Contact, limit=500):
    """Mirror every contact that has a name. Returns a short report."""
    if not ms_graph.is_configured():
        return {'sent': 0, 'failed': 0, 'error': 'Microsoft 365 is not connected.'}
    sent = failed = 0
    for c in Contact.query.limit(limit).all():
        ok, _ = push_contact(app, db, c)
        if ok:
            sent += 1
        else:
            failed += 1
    return {'sent': sent, 'failed': failed, 'error': None}
