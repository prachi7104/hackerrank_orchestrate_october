"""Primary-account linkage from the deletion worker's activity stream.

In most cases no evidence file shows the primary account together with the subject's identifiers
(after deletion the account row has no identifiers left). The deletion worker, however, submits one
primary-profile deletion per request shortly before the workflow reports its status. This module
assigns those activities to cases one-to-one, using only:
  * direct evidence links (an account already linked to the case by identifiers wins),
  * the time window around reported_at,
  * optionally, compatibility between reported_result and reported_status.
Candidates are removed only when another case is *uniquely* assigned to them. Anything still
ambiguous is returned as ambiguous; it is never forced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

WINDOW_BEFORE_MIN = 120   # measured on samples: worker deletes land 45-75 min before reported_at
WINDOW_AFTER_MIN = 15     # handcrafted samples report a few minutes before the worker event
STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass
class AccountLink:
    resource_id: str | None
    method: str                     # direct | activity | ambiguous | none
    candidates: list = field(default_factory=list)
    activity_id: str | None = None


def primary_deletes(activities: list[dict]) -> dict[str, dict]:
    return {a["resource_id"]: a for a in activities
            if a["source_system"] == "deletion_worker" and a["target_system"] == "account_service"
            and a["event_type"] == "DELETE_RECORD"}


def assign_accounts(cases: list[dict], direct: dict[str, set], activities: list[dict],
                    status_compat: bool = True) -> dict[str, AccountLink]:
    deletes = primary_deletes(activities)
    edges: dict[str, set] = {}
    links: dict[str, AccountLink] = {}
    for c in cases:
        cid = c["case_id"]
        if len(direct.get(cid, ())) == 1:
            links[cid] = AccountLink(next(iter(direct[cid])), "direct")
            continue
        rep = _t(c["reported_at"])
        cand = set()
        for rid, a in deletes.items():
            delta = (_t(a["event_time"]) - rep).total_seconds() / 60
            if not -WINDOW_BEFORE_MIN <= delta <= WINDOW_AFTER_MIN:
                continue
            if status_compat and a["reported_result"] != STATUS_RESULT.get(c["reported_status"]):
                continue
            if a["event_time"] > c["audit_at"]:
                continue
            cand.add(rid)
        if direct.get(cid):
            cand &= direct[cid]
        edges[cid] = cand
    taken = {l.resource_id for l in links.values()}
    changed = True
    while changed:
        changed = False
        for cid, cand in edges.items():
            if cid in links:
                continue
            free = cand - taken
            if len(free) == 1:
                rid = free.pop()
                links[cid] = AccountLink(rid, "activity", sorted(cand), deletes[rid]["activity_id"])
                taken.add(rid)
                changed = True
    for cid, cand in edges.items():
        if cid not in links:
            free = sorted(cand - taken)
            links[cid] = AccountLink(None, "ambiguous" if free else "none", free)
    return links
