"""Parse every supplied evidence file into one table of typed resource observations.

An Observation records what one evidence unit says about one resource at one time. Parsers only
translate formats; they never decide policy or case membership. Screenshots are not parsed here
(they need a vision model); PDFs are read for their text layer only.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

PREFIX_SYSTEM = [
    ("data_flow_", "integration_service"),
    ("audit_record_", "security_service"),
    ("email_delivery_", "email_delivery_service"),
    ("legal_hold_", "legal_service"),
    ("tombstone_", "identity_service"),
    ("analytics_", "analytics_platform"),
    ("account_", "account_service"),
    ("search_", "search_service"),
    ("support_", "support_platform"),
    ("export_", "export_service"),
    ("backup_", "backup_service"),
    ("marketing_", "marketing_platform"),
    ("cache_", "cache_service"),
    ("object_", "object_storage"),
]
RESOURCE_RE = re.compile(r"\b(?:" + "|".join(p for p, _ in PREFIX_SYSTEM) + r")\d+\b")

# Fields whose non-null value is a direct identifier stored in the record.
DIRECT_FIELDS = {
    "name", "full_name", "display_name", "email", "phone", "email_hash",
    "requester_name", "requester_email", "requester_phone", "owner_email", "owner_phone",
    "customer_email", "uploaded_by", "matched_value",
}
# Stable internal references (user_id); identifying for analytics, permitted for tombstones/audit.
REFERENCE_FIELDS = {"customer_reference", "user_id", "subject_reference", "subject_selector", "subject_ref"}
# Values used to find the record; they link it to a subject but are not record content.
LOOKUP_FIELDS = {
    "lookup_key", "match_key", "subject_key", "correlation_key", "owner_key", "scan_key",
    "query", "lookup_value",
}
NULLS = {None, "", "null", "None", "[REDACTED]"}


def field_name(key: str) -> str:
    return key.split("#", 1)[0]


def system_for(resource_id: str) -> str:
    for prefix, system in PREFIX_SYSTEM:
        if resource_id.startswith(prefix):
            return system
    return "unknown"


@dataclass
class Observation:
    file_id: str
    captured_at: str            # manifest capture time of the file (governs citability)
    observed_at: str            # time the record state was observed
    resource_id: str
    system_id: str
    category: str | None = None  # explicit record_type when the evidence states one
    exists: bool | None = None
    active: bool | None = None   # data flow enabled / restore allowed
    result: str | None = None    # explicit result label (FOUND, IDENTIFIABLE, ...)
    content: dict = field(default_factory=dict)   # non-null direct identifiers and references
    lookups: set = field(default_factory=set)      # lookup values that found this record
    collection: str = ""
    locator: str = ""

    def keys(self) -> set[str]:
        return {norm(v) for v in list(self.content.values()) + list(self.lookups) if v not in NULLS}


def norm(value) -> str:
    v = str(value).strip().lower()
    if re.fullmatch(r"\+?[\d\s().-]{8,}", v):
        return "tel:" + re.sub(r"\D", "", v)
    return v


def split_record(rec: dict) -> tuple[dict, set]:
    content, lookups = {}, set()
    for k, v in rec.items():
        if isinstance(v, (dict, list)) or v in NULLS:
            continue
        if k in DIRECT_FIELDS or k in REFERENCE_FIELDS:
            content[k] = str(v)
        elif k in LOOKUP_FIELDS:
            lookups.add(str(v))
    return content, lookups


RESULT_STATE = {
    # label: (exists, active)
    "FOUND": (True, None), "PRESENT": (True, None), "IDENTIFIABLE": (True, None),
    "ANONYMIZED": (True, None), "REDACTED": (True, None),
    "NOT_FOUND": (False, None), "ABSENT": (False, None),
    "ACTIVE": (True, True), "DISABLED": (True, False), "BLOCKED": (True, False),
}


def lookup_record(fid, cap, rec, observed_at, locator) -> Observation:
    """Generic 'record lookup' row: system, resource_id, record_type, result, subject/lookup value."""
    rid = rec["resource_id"]
    result = rec.get("result")
    exists, active = RESULT_STATE.get(result, (None, None))
    subject = rec.get("subject_reference") or rec.get("lookup_value")
    obs = Observation(fid, cap, observed_at, rid, rec.get("system") or system_for(rid),
                      category=rec.get("record_type"), exists=exists, active=active, result=result,
                      locator=locator)
    if subject not in NULLS:
        obs.lookups.add(subject)
    return obs


# ---------------------------------------------------------------- JSON records

def record_observation(fid, cap, observed_at, rec, collection, locator) -> Observation:
    rid = rec["resource_id"]
    content, lookups = split_record(rec)
    obs = Observation(fid, cap, observed_at, rid, system_for(rid), content=content, lookups=lookups,
                      collection=collection, locator=locator, exists=True)
    if "status_code" in rec:                       # account profile lookup
        obs.exists = rec["status_code"] == 200
        if isinstance(rec.get("profile"), dict):
            c, _ = split_record(rec["profile"])
            obs.content.update(c)
    if "enabled" in rec:
        obs.active = bool(rec["enabled"])
    if "restore_allowed" in rec:
        obs.active = bool(rec["restore_allowed"])
    if "download_available" in rec:
        obs.exists = bool(rec["download_available"])
    if collection == "ticket_attachments":
        obs.category = "SUPPORT_ATTACHMENT"
    if "message_text" in rec and rec["message_text"] not in NULLS:
        obs.content["message_text"] = rec["message_text"]
    if "account_status" in rec and rec.get("resource_id", "").startswith("tombstone_"):
        obs.category = "ACCOUNT_TOMBSTONE"
    return obs


def parse_json(fid, cap, path: Path) -> list[Observation]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    if "service_exports" in data:
        for system, collections in data["service_exports"].items():
            for coll, recs in collections.items():
                for i, rec in enumerate(recs):
                    loc = f"{system}.{coll}[{i}]"
                    if coll == "index_queries":
                        docs = rec.get("hits", {}).get("documents", [])
                        doc = next((d for d in docs if d.get("resource_id") == rec["resource_id"]), None)
                        obs = record_observation(fid, cap, rec["observed_at"], doc or {"resource_id": rec["resource_id"]}, coll, loc)
                        obs.exists = doc is not None
                        obs.lookups.add(rec.get("query", ""))
                        out.append(obs)
                    else:
                        out.append(record_observation(fid, cap, rec["observed_at"], rec, coll, loc))
    elif "snapshots" in data:
        for si, snap in enumerate(data["snapshots"]):
            for i, rec in enumerate(snap["records"]):
                loc = f"snapshots[{si}].{snap['collection']}[{i}]"
                if "record_type" in rec:
                    out.append(lookup_record(fid, cap, rec, rec.get("recorded_at", snap["captured_at"]), loc))
                    continue
                if "document" in rec:
                    rec = {**rec["document"], "resource_id": rec["resource_id"]}
                out.append(record_observation(fid, cap, snap["captured_at"], rec, snap["collection"], loc))
    else:
        default_time = data.get("captured_at") or data.get("exported_at") or cap
        for i, rec in enumerate(data.get("records", [])):
            loc = f"records[{i}]"
            if "record_type" in rec:
                out.append(lookup_record(fid, cap, rec, rec.get("recorded_at", default_time), loc))
            else:
                out.append(record_observation(fid, cap, default_time, rec, data.get("collection", ""), loc))
    return out


# ---------------------------------------------------------------- text handover

HANDOVER_RE = re.compile(r"^- (\S+) \| (\w+) \| (\w+) \| (\w+) \| (\w+) \| ?(.*)$")


def parse_handover(fid, cap, path: Path) -> list[Observation]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = HANDOVER_RE.match(line.strip())
        if m:
            ts, system, rid, rtype, result, value = m.groups()
            value = value.strip()
            rec = {"resource_id": rid, "system": system, "record_type": rtype, "result": result,
                   "lookup_value": None if value in ("", "no direct identifier") else value}
            out.append(lookup_record(fid, cap, rec, ts, f"line {n}"))
    return out


# ---------------------------------------------------------------- logs

KV_RE = re.compile(r'(\w+)=(?:"([^"]*)"|([^\s"]+))')
TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s+(.*)$")
POSITIONAL = [
    re.compile(r"^\[\w+\] \[(?P<svc>\w+)\] \[(?P<trace>[\w-]+)\] (?P<event>\w+):"),
    re.compile(r"^\|?\s*\w+ \| (?P<svc>\w+) \| (?P<trace>[\w-]+) \| (?P<event>\w+) \|"),
    re.compile(r"^<\d+>1 \S+ (?P<svc>\w+) (?P<trace>[\w-]+) - -"),
]


def parse_log_line(line: str) -> dict | None:
    m = TS_RE.match(line.strip())
    if not m:
        return None
    ts, body = m.groups()
    body = body.replace('\\"', '"')
    if body.startswith("{"):
        kv = {k: str(v) for k, v in json.loads(body).items()}
    else:
        kv = {g[0]: (g[1] if g[1] is not None else g[2]) for g in
              ((mm.group(1), mm.group(2), mm.group(3)) for mm in KV_RE.finditer(body))}
        for pat in POSITIONAL:
            pm = pat.match(body)
            if pm:
                d = pm.groupdict()
                kv.setdefault("service", d["svc"])
                kv.setdefault("trace_id", d["trace"])
                if d.get("event"):
                    kv.setdefault("event", d["event"])
                break
    kv["_ts"] = ts
    kv.setdefault("service", kv.get("function") or kv.get("logger") or "")
    kv.setdefault("trace_id", kv.get("requestId", ""))
    return kv


def truthy(v):
    return None if v is None else str(v).lower() in ("true", "1", "yes", "hit")


def log_event_observation(fid, cap, kv: dict, context: dict, locator: str) -> Observation | None:
    """Map one completed log event (plus lookup context from its trace) to an observation."""
    ev, rid = kv.get("event", ""), kv.get("resource_id")
    if not rid or not RESOURCE_RE.fullmatch(rid):
        return None
    rec = {k: v for k, v in {**context, **kv}.items() if not k.startswith("_")}
    content, lookups = split_record(rec)
    # On the completed line itself, correlation_key reports the record's own key value, so it is
    # record content (e.g. a hashed email); on a *_started line it is only the query.
    if kv.get("correlation_key") not in NULLS:
        content["correlation_key"] = kv["correlation_key"]
    obs = Observation(fid, cap, kv["_ts"], rid, rec.get("system") or system_for(rid), content=content,
                      lookups=lookups, locator=locator, collection=ev)
    if ev == "record_lookup":
        return lookup_record(fid, cap, {"resource_id": rid, "system": rec.get("system"),
                                        "record_type": rec.get("record_type"), "result": rec.get("result"),
                                        "subject_reference": rec.get("subject_reference")}, kv["_ts"], locator)
    if ev in ("profile_lookup_completed", "audit_record_read_completed", "contact_lookup_completed",
              "contact_query_completed"):
        rows, status = rec.get("rows"), rec.get("http_status")
        obs.exists = not (rows == "0" or status == "404")
    elif ev == "index_query_completed":
        obs.exists = rec.get("result", "hit") == "hit" and rec.get("total_hits", "1") != "0"
    elif ev in ("analytics_profile_read_completed", "analytics_profile_read", "support_ticket_read_completed",
                "message_read", "retained_order_loaded"):
        obs.exists = True
    elif ev == "analytics_identity_scan":
        obs.exists = True
    elif ev in ("message_pii_scan", "attachment_pii_scan"):
        obs.exists = True
        obs.category = "SUPPORT_ATTACHMENT" if ev.startswith("attachment") else "SUPPORT_MESSAGE"
    elif ev in ("connector_config_read_completed", "connector_heartbeat", "connector_config_loaded"):
        obs.exists, obs.active = True, truthy(rec.get("enabled"))
    elif ev == "restore_policy_read_completed":
        obs.exists, obs.active = truthy(rec.get("archive_present", "true")), truthy(rec.get("restore_allowed"))
    elif ev == "restore_candidate_evaluated":
        obs.exists = True
        obs.active = truthy(rec.get("eligible")) and not truthy(rec.get("deletion_block"))
    elif ev == "restore_authorization_completed":
        obs.exists, obs.active = True, rec.get("decision") != "deny"
    elif ev in ("export_download_check_completed", "export_metadata_read_completed"):
        obs.exists = truthy(rec.get("available"))
    elif ev == "deletion_marker_check":
        obs.exists = not truthy(rec.get("marker_found"))
    else:
        return None
    return obs


def parse_log(fid, cap, path: Path) -> list[Observation]:
    events = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        kv = parse_log_line(line)
        if kv:
            kv["_line"] = n
            events.append(kv)
    # Lookup values often appear on the *_started line of the same trace; carry them as context.
    context: dict[str, dict] = {}
    for kv in events:
        t = kv.get("trace_id")
        if t:   # readback "started" lines carry both the resource_id and the lookup key
            context.setdefault(t, {}).update({k: v for k, v in kv.items() if k in LOOKUP_FIELDS or k == "subject_ref"})
    out = []
    for kv in events:
        ctx = context.get(kv.get("trace_id"), {}) if kv.get("trace_id") else {}
        obs = log_event_observation(fid, cap, kv, ctx, f"line {kv['_line']}")
        if obs:
            out.append(obs)
    return out


# ---------------------------------------------------------------- SQL snapshots

def parse_sql(fid, cap, path: Path) -> list[Observation]:
    db = sqlite3.connect(":memory:")
    db.executescript(path.read_text(encoding="utf-8"))
    db.row_factory = sqlite3.Row
    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
    scope = {}
    if "snapshot_scope" in tables:
        scope = {r["origin_system"]: r["captured_at"] for r in db.execute("select * from snapshot_scope")}
    out = []
    for t in tables:
        if t == "snapshot_scope":
            continue
        for i, row in enumerate(db.execute(f'select * from "{t}"')):
            rec = dict(row)
            loc = f"{t}[{i}]"
            if "payload" in rec or any(isinstance(v, str) and v.startswith("{") for v in rec.values()):
                payload = next(json.loads(v) for v in rec.values() if isinstance(v, str) and v.startswith("{"))
                if "document" in payload:
                    payload = {**payload["document"], "resource_id": payload["resource_id"]}
                when = next((v for k, v in rec.items() if k in ("captured_at", "snapshot_at", "updated_at")), cap)
                obs = record_observation(fid, cap, when, payload, t, loc)
            else:
                when = rec.get("updated_at") or rec.get("indexed_at") or scope.get(system_for(rec["resource_id"])) or cap
                obs = record_observation(fid, cap, when, rec, t, loc)
                if "account_status" in rec and rec["account_status"] == "DELETED":
                    obs.exists = False
            obs.collection = t
            out.append(obs)
    return out


# ---------------------------------------------------------------- PDFs (text layer only)

def pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"\+\d[\d ()-]{7,}\d")
USER_RE = re.compile(r"\buser_\d+\b")


def parse_pdf(fid, cap, path: Path, origin: str, aliases: dict | None = None) -> list[Observation]:
    """A business document that names a resource id is evidence that the record exists at capture
    time and carries the identifiers printed in it. A support-platform document is an attachment."""
    text = pdf_text(path)
    content = {}
    for i, v in enumerate(EMAIL_RE.findall(text)):
        content[f"email#{i}"] = v
    for i, v in enumerate(PHONE_RE.findall(text)):
        content[f"phone#{i}"] = v
    refs = {f"customer_reference#{i}": v for i, v in enumerate(USER_RE.findall(text))}
    out = []  # repeated fields are keyed field#n; field_name() strips the suffix
    named = set(RESOURCE_RE.findall(text)) | {rid for alias, rid in (aliases or {}).items() if alias in text}
    for rid in sorted(named):
        obs = Observation(fid, cap, cap, rid, system_for(rid), exists=True,
                          content={**content, **refs}, collection="pdf", locator="text layer")
        if origin == "support_platform" and rid.startswith("support_"):
            obs.category = "SUPPORT_ATTACHMENT"
        out.append(obs)
    return out


def account_absence_snapshots(dataset: Path) -> list[tuple[str, str, set]]:
    """SQL captures that contain a complete account-profile table: (file_id, captured_at, ids present).

    A capture qualifies when it declares account_service in snapshot_scope, or when it is a
    dedicated profile-table dump (primary/failover profile tables). Absence of an account from such
    a capture is evidence the account did not exist at that time.
    """
    out = []
    for f in load_manifest(dataset):
        if f.kind != "sql":
            continue
        db = sqlite3.connect(":memory:")
        db.executescript(f.path.read_text(encoding="utf-8"))
        tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
        scoped = "snapshot_scope" in tables and db.execute(
            "select 1 from snapshot_scope where origin_system='account_service'").fetchone()
        profile_tables = [t for t in tables if t.startswith("customer_profiles")]
        if not profile_tables or not (scoped or len(profile_tables) == len([t for t in tables if t != "snapshot_scope"])):
            continue
        present = set()
        for t in profile_tables:
            present |= {r[0] for r in db.execute(f'select resource_id from "{t}"')}
        out.append((f.file_id, f.captured_at, present))
    for f in load_manifest(dataset):   # snapshot bundles that captured the whole profile collection
        if f.kind == "json" and "snapshot_bundle" in f.path.name:
            for snap in json.loads(f.path.read_text(encoding="utf-8"))["snapshots"]:
                if snap["data_store"] == "account_service" and snap["collection"] == "customer_profiles":
                    out.append((f.file_id, max(snap["captured_at"], f.captured_at),
                                {r["resource_id"] for r in snap["records"]}))
    return out


# ---------------------------------------------------------------- manifest

@dataclass
class EvidenceFile:
    file_id: str
    path: Path
    origin: str
    captured_at: str
    kind: str


def load_manifest(dataset: Path) -> list[EvidenceFile]:
    files = []
    for r in csv.DictReader(open(dataset / "evidence_files.csv", encoding="utf-8")):
        p = dataset / r["file_path"]
        files.append(EvidenceFile(r["file_id"], p, r["origin"], r["captured_at"], p.suffix.lstrip(".")))
    return files


def load_observations(dataset: Path, aliases: dict | None = None) -> tuple[list[Observation], dict]:
    """Returns all observations and a coverage report {file_id: count or reason}."""
    obs, coverage = [], {}
    for f in load_manifest(dataset):
        if f.kind == "json":
            got = parse_json(f.file_id, f.captured_at, f.path)
        elif f.kind == "txt":
            got = parse_handover(f.file_id, f.captured_at, f.path)
        elif f.kind == "log":
            got = parse_log(f.file_id, f.captured_at, f.path)
        elif f.kind == "sql":
            got = parse_sql(f.file_id, f.captured_at, f.path)
        elif f.kind == "pdf":
            got = parse_pdf(f.file_id, f.captured_at, f.path, f.origin, aliases)
        else:
            coverage[f.file_id] = f"not parsed ({f.kind})"
            continue
        coverage[f.file_id] = len(got)
        obs.extend(got)
    return obs, coverage
