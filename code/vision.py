"""Screenshot evidence: model transcription, then code validation, then typed observations.

The model only transcribes what a screen shows. Code decides what is usable:
  * identifiers must be resource ids of a known form, or aliases the data catalog maps to one;
  * an email/phone/user_id printed on a record must agree with the identity tables when the
    value is known there (a contradiction rejects the record's identity link);
  * status text maps to existence / activity through a fixed table; unknown text stays unknown.
Rejected records are kept in the validation report, never in the observation table.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from core import catalog_aliases
from evidence import RESOURCE_RE, Observation, load_manifest, norm, system_for
from llm import CallFailed, Gemini, image_part

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "screenshot_extraction.md"
PROMPT_VERSION = "shot-v1"
CONFIG = {"thinkingConfig": {"thinkingLevel": "low"}}
KINDS = ["account_profile", "analytics_profile", "backup_archive", "data_flow", "export", "search_document",
         "support_ticket", "marketing_profile", "security_event", "stored_object", "customer_reference", "other"]
SCHEMA = {
    "type": "object",
    "properties": {
        "screen_title": {"type": "string"},
        "shown_at": {"type": "string"},
        "records": {"type": "array", "items": {"type": "object", "properties": {
            "record_id": {"type": "string"},
            "record_kind": {"type": "string", "enum": KINDS},
            "status_shown": {"type": "string"},
            "name": {"type": "string"}, "email": {"type": "string"},
            "phone": {"type": "string"}, "user_id": {"type": "string"},
            "linked_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["record_id", "record_kind", "status_shown", "name", "email", "phone", "user_id", "linked_ids"]}},
    },
    "required": ["screen_title", "shown_at", "records"],
}
# status text -> (exists, active); anything else leaves both unknown
STATUS = {
    "ready": (True, None), "live": (True, None), "active": (True, None), "valid": (True, None),
    "archive": (True, None), "healthy": (True, True), "continuous": (True, True), "enabled": (True, True),
    "disabled": (True, False), "paused": (True, False), "stopped": (True, False), "deleted": (False, None),
    "not found": (False, None), "expired": (False, None), "retained reference": (True, None),
}


@dataclass
class ShotResult:
    file_id: str
    status: str                      # ok | failed
    accepted: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    error: str = ""


def resolve_id(raw: str, aliases: dict[str, str]) -> str | None:
    raw = raw.strip()
    if RESOURCE_RE.fullmatch(raw):
        return raw
    return aliases.get(raw)


KIND_PREFIX = {"account_profile": "account_", "analytics_profile": "analytics_", "backup_archive": "backup_",
               "data_flow": "data_flow_", "export": "export_", "search_document": "search_",
               "support_ticket": "support_", "marketing_profile": "marketing_", "security_event": "audit_record_",
               "stored_object": "object_", "customer_reference": "tombstone_"}


def validate_record(rec: dict, aliases: dict[str, str], identities: list[dict]) -> tuple[str | None, str]:
    rid = resolve_id(rec.get("record_id", ""), aliases)
    if not rid:
        # A header may list "user_20 · analytics_7438"; take the one linked id of the record's own kind.
        prefix = KIND_PREFIX.get(rec.get("record_kind"), "\0")
        own = {r for r in (resolve_id(x, aliases) for x in rec.get("linked_ids", [])) if r and r.startswith(prefix)}
        rid = own.pop() if len(own) == 1 else None
    if not rid:
        return None, f"unsupported identifier {rec.get('record_id')!r}"
    by_email = {norm(u["email"]): u["user_id"] for u in identities}
    email, uid = norm(rec.get("email", "")), rec.get("user_id", "").strip()
    if email and uid and email in by_email and by_email[email] != uid:
        return None, f"identity conflict: {rec['email']} belongs to {by_email[email]}, screen says {uid}"
    return rid, ""


def to_observation(fid: str, cap: str, rid: str, rec: dict) -> Observation:
    exists, active = STATUS.get(rec.get("status_shown", "").strip().lower(), (True, None))
    content = {k: rec[k] for k in ("name", "email", "phone") if rec.get(k)}
    if rec.get("user_id"):
        content["customer_reference"] = rec["user_id"]
    obs = Observation(fid, cap, cap, rid, system_for(rid), exists=exists, active=active, content=content,
                      collection="screenshot", locator=f"screen record {rec.get('record_id')}")
    if rec.get("record_kind") == "stored_object" or rid.startswith("object_"):
        obs.category = "SUPPORT_ATTACHMENT"
    return obs


def extract(g: Gemini, dataset: Path, activities: list[dict], identities: list[dict]) -> tuple[list[Observation], list[ShotResult]]:
    aliases = catalog_aliases(activities)
    observations, results = [], []
    for f in load_manifest(dataset):
        if f.kind != "png":
            continue
        try:
            reply = g.generate("vision", [f.file_id], [{"role": "user", "parts": [
                image_part(f.path), {"text": "Transcribe the records on this screen."}]}],
                system=PROMPT_FILE.read_text(encoding="utf-8"), prompt_version=PROMPT_VERSION,
                schema=SCHEMA, config=CONFIG)
            data = json.loads(reply.text)
        except (CallFailed, ValueError) as e:
            results.append(ShotResult(f.file_id, "failed", error=str(e)[:200]))
            g.log({"kind": "parse_error" if isinstance(e, ValueError) else "call_failed", "purpose": "vision",
                   "items": [f.file_id], "detail": str(e)[:200]})
            continue
        res = ShotResult(f.file_id, "ok")
        for rec in data.get("records", []):
            rid, why = validate_record(rec, aliases, identities)
            if rid:
                observations.append(to_observation(f.file_id, f.captured_at, rid, rec))
                res.accepted.append({"resource_id": rid, **rec})
            else:
                res.rejected.append({"reason": why, **rec})
        results.append(res)
    return observations, results


def planned_requests(dataset: Path) -> int:
    return sum(1 for f in load_manifest(dataset) if f.kind == "png")


RESTORE_SCHEMA = {"type": "object", "properties": {"completed_restores": {"type": "array", "items": {
    "type": "object", "properties": {"text": {"type": "string"}, "backup_id": {"type": "string"}},
    "required": ["text", "backup_id"]}}}, "required": ["completed_restores"]}


def restore_capability(g: Gemini, dataset: Path, observations: list[Observation]) -> dict[str, list[str]]:
    """For screenshots that show backup archives: printed lines reporting a *completed* restore.

    Evidence-gap policy input only. A line is kept when it names a backup id of the known form and
    the word restore; everything else is discarded. Returns {file_id: [verbatim lines]}.
    """
    files = {f.file_id: f for f in load_manifest(dataset) if f.kind == "png"}
    shots = sorted({o.file_id for o in observations if o.resource_id.startswith("backup_") and o.file_id in files})
    out = {}
    for fid in shots:
        try:
            reply = g.generate("vision:restore", [fid], [{"role": "user", "parts": [image_part(files[fid].path), {
                "text": "List every line printed on this screen that reports a completed restore of a backup "
                        "archive. Copy each line verbatim and give the backup id it names. Return an empty list "
                        "if there is none."}]}],
                prompt_version="restore-v1", schema=RESTORE_SCHEMA, config={"thinkingConfig": {"thinkingLevel": "low"}})
            lines = json.loads(reply.text)["completed_restores"]
        except (CallFailed, ValueError, KeyError) as e:
            g.log({"kind": "parse_error", "purpose": "vision:restore", "items": [fid], "detail": str(e)[:200]})
            continue
        kept = [x["text"] for x in lines if RESOURCE_RE.fullmatch(x.get("backup_id", "").strip())
                and x["backup_id"].strip() in x["text"] and "restor" in x["text"].lower()]
        if kept:
            out[fid] = kept
    return out
