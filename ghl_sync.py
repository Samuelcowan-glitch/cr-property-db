"""GoHighLevel live sync for the Cowan & Rutter property database.

Pushes Contacts, Enquiries (as opportunities) and Projects/Properties
(as custom object records, best-effort) into the Cowan & Rutter GHL
sub-account whenever they are created or updated in this app.

Setup (environment variables on the host):
    GHL_API_TOKEN    - Private Integration token (pit-...)
    GHL_LOCATION_ID  - GHL sub-account/location id
    GHL_PIPELINE     - optional, pipeline name for enquiries (default "Enquiries")

Wire-up (bottom of app.py):
    import ghl_sync
    ghl_sync.init(app, db, Contact=Contact, Enquiry=Enquiry, Project=Project)

Backfill (after deploy, as a logged-in user):
    visit /admin/ghl-backfill
"""
import os
import threading
import logging

import requests

log = logging.getLogger("ghl_sync")

BASE = "https://services.leadconnectorhq.com"
TOKEN = os.environ.get("GHL_API_TOKEN", "")
LOCATION_ID = os.environ.get("GHL_LOCATION_ID", "")
PIPELINE_NAME = os.environ.get("GHL_PIPELINE", "Enquiries")

_pipeline_cache = {}


def enabled():
    return bool(TOKEN and LOCATION_ID)


def _headers():
    return {
        "Authorization": "Bearer " + TOKEN,
        "Version": "2021-07-28",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _req(method, path, **kwargs):
    try:
        r = requests.request(method, BASE + path, headers=_headers(),
                             timeout=15, **kwargs)
        if r.status_code >= 400:
            log.warning("GHL %s %s -> %s %s", method, path, r.status_code, r.text[:300])
            return None
        return r.json() if r.text else {}
    except Exception as e:  # network failure must never break the app
        log.warning("GHL request failed: %s", e)
        return None


# ---------------- contacts ----------------

def sync_contact(c):
    """Upsert one app Contact into GHL. Returns GHL contact id or None."""
    if not enabled() or not (c.email or c.phone or c.mobile):
        return None
    tags = ["property-db"]
    if c.contact_type:
        tags.append(c.contact_type.lower().replace(" ", "-"))
    body = {
        "locationId": LOCATION_ID,
        "firstName": c.first_name or "",
        "lastName": c.last_name or "",
        "email": c.email or None,
        "phone": c.mobile or c.phone or None,
        "source": "Cowan & Rutter Property DB",
        "tags": tags,
    }
    body = {k: v for k, v in body.items() if v}
    out = _req("POST", "/contacts/upsert", json=body)
    if out and out.get("contact"):
        return out["contact"].get("id")
    return None


# ---------------- enquiries -> opportunities ----------------

def _pipeline():
    """Find the enquiries pipeline (cached). Returns (pipelineId, firstStageId, stagesByName)."""
    if _pipeline_cache:
        return _pipeline_cache.get("data")
    out = _req("GET", "/opportunities/pipelines", params={"locationId": LOCATION_ID})
    data = None
    if out:
        for p in out.get("pipelines", []):
            if p.get("name", "").lower() == PIPELINE_NAME.lower():
                stages = p.get("stages", [])
                by_name = {s.get("name", "").lower(): s.get("id") for s in stages}
                first = stages[0]["id"] if stages else None
                data = (p["id"], first, by_name)
                break
    _pipeline_cache["data"] = data
    return data


_STATUS_MAP = {"Open": "open", "Won": "won", "Lost": "lost", "On Hold": "open"}


def sync_enquiry(e):
    if not enabled():
        return None
    pipe = _pipeline()
    if not pipe:
        log.warning("GHL pipeline '%s' not found - skipping enquiry sync", PIPELINE_NAME)
        return None
    pipeline_id, first_stage, stages = pipe
    contact_id = sync_contact(e.contact) if e.contact else None
    if not contact_id:
        return None  # opportunities need a contact
    stage_id = stages.get((e.status or "Open").lower(), first_stage)
    value = e.req_budget_max or e.req_budget_min or 0
    body = {
        "locationId": LOCATION_ID,
        "pipelineId": pipeline_id,
        "pipelineStageId": stage_id or first_stage,
        "contactId": contact_id,
        "name": e.subject or ("Enquiry #%s" % e.id),
        "status": _STATUS_MAP.get(e.status or "Open", "open"),
        "monetaryValue": value or 0,
        "source": "Cowan & Rutter Property DB",
    }
    return _req("POST", "/opportunities/", json=body)


# ---------------- projects -> custom object records (best effort) ----------------

_OBJECT_KEY = "custom_objects.projects"
_object_ready = {}


def _ensure_project_object():
    if _object_ready.get("ok") is not None:
        return _object_ready["ok"]
    out = _req("GET", "/objects/", params={"locationId": LOCATION_ID})
    ok = False
    if out is not None:
        keys = [o.get("key") for o in out.get("objects", [])]
        if _OBJECT_KEY in keys:
            ok = True
        else:
            created = _req("POST", "/objects/", json={
                "locationId": LOCATION_ID,
                "labels": {"singular": "Project", "plural": "Projects"},
                "key": "projects",
                "primaryDisplayPropertyDetails": {
                    "key": "custom_objects.projects.name",
                    "name": "Name",
                    "dataType": "TEXT",
                },
            })
            ok = created is not None
    _object_ready["ok"] = ok
    return ok


def sync_project(p):
    if not enabled() or not _ensure_project_object():
        return None
    props = {
        "name": p.name or ("Project #%s" % p.id),
        "status": p.status or "",
        "ref": p.project_ref or "",
        "client": p.client or "",
        "fee_earner": p.fee_earner or "",
    }
    return _req("POST", "/objects/%s/records" % _OBJECT_KEY, json={
        "locationId": LOCATION_ID,
        "properties": {k: v for k, v in props.items() if v},
    })


# ---------------- flask / sqlalchemy wiring ----------------

def init(app, db, Contact=None, Enquiry=None, Project=None):
    if not enabled():
        app.logger.info("ghl_sync: GHL_API_TOKEN/GHL_LOCATION_ID not set - sync disabled")

    from sqlalchemy import event

    watched = tuple(m for m in (Contact, Enquiry, Project) if m is not None)

    @event.listens_for(db.session.__class__, "after_flush")
    def _collect(session, ctx):
        items = session.info.setdefault("ghl_pending", [])
        for obj in list(session.new) + list(session.dirty):
            if isinstance(obj, watched):
                items.append((type(obj).__name__, obj.id))

    @event.listens_for(db.session.__class__, "after_commit")
    def _push(session):
        items = session.info.pop("ghl_pending", [])
        if not items or not enabled():
            return
        uniq = list(dict.fromkeys(items))

        def worker(pairs):
            with app.app_context():
                for kind, oid in pairs:
                    try:
                        if Contact is not None and kind == "Contact":
                            obj = db.session.get(Contact, oid)
                            if obj is not None:
                                sync_contact(obj)
                        elif Enquiry is not None and kind == "Enquiry":
                            obj = db.session.get(Enquiry, oid)
                            if obj is not None:
                                sync_enquiry(obj)
                        elif Project is not None and kind == "Project":
                            obj = db.session.get(Project, oid)
                            if obj is not None:
                                sync_project(obj)
                    except Exception as e:
                        log.warning("ghl_sync worker error: %s", e)

        threading.Thread(target=worker, args=(uniq,), daemon=True).start()

    # one-off backfill, admin URL (requires login via flask_login)
    @app.route("/admin/ghl-backfill")
    def ghl_backfill():
        try:
            from flask_login import current_user
            if not getattr(current_user, "is_authenticated", False):
                return "Login required", 403
        except Exception:
            return "Login required", 403
        if not enabled():
            return "GHL_API_TOKEN / GHL_LOCATION_ID not configured", 400
        counts = {"contacts": 0, "enquiries": 0, "projects": 0}
        if Contact is not None:
            for c in Contact.query.all():
                if sync_contact(c):
                    counts["contacts"] += 1
        if Enquiry is not None:
            for e in Enquiry.query.all():
                if sync_enquiry(e):
                    counts["enquiries"] += 1
        if Project is not None:
            for p in Project.query.all():
                if sync_project(p):
                    counts["projects"] += 1
        return counts, 200
