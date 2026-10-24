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


LIFECYCLE_FALLBACK = False   # experiment X1: anchorless cases may use a lifecycle-unique candidate
LIFECYCLE_TOLERANCE_MIN = 15
PROPAGATE = False      # experiment A1: iterate exclusivity after each round of assignments
STATUS_FILTER = False  # experiment A2: also require reported_result to match reported_status
ORDINAL = False        # experiment A4: status-free ordinal alignment of competing worker batches
ORDINAL_SAME_STREAM = False  # A4b: order is compared only when all anchors share operation type and system
ANCHOR_WINDOW_MIN = 180   # result is flat for windows of 120-360 min (experiments.md, E14)


@dataclass
class Ownership:
    """Three-tier primary-account ownership for one case."""
    tier: str                       # DIRECT | CONDITIONALLY_INFERRED | UNRESOLVED
    resource_id: str | None = None
    candidates: list = field(default_factory=list)
    basis: str = ""


def conditional_ownership(cases: list[dict], direct: dict[str, set], activities: list[dict],
                          other_owner, evidenced_resources) -> dict[str, Ownership]:
    """DIRECT when exactly one account is evidenced for the subject after the request.

    CONDITIONALLY_INFERRED only when all of these hold:
      * the case has operation history of its own: a deletion-worker operation, after requested_at
        and before audit_at, on a non-account resource that evidence ties to the subject (anchor);
      * after removing every directly evidenced account and every account whose evidence names a
        different person, exactly one primary-profile deletion falls in [anchor - window, anchor]
        and after requested_at;
      * no other case admits that same account.
    reported_status and reported_result are never consulted. Everything else is UNRESOLVED.
    other_owner(case, rid) -> bool; evidenced_resources(case) -> set of resource ids.
    """
    deletes = primary_deletes(activities)
    worker = [a for a in activities if a["source_system"] == "deletion_worker" and a["target_system"] != "account_service"]
    out: dict[str, Ownership] = {}
    for c in cases:
        d = direct.get(c["case_id"], set())
        if len(d) == 1:
            out[c["case_id"]] = Ownership("DIRECT", next(iter(d)), basis="identifier evidence after requested_at")
        elif len(d) > 1:
            out[c["case_id"]] = Ownership("UNRESOLVED", None, sorted(d), "several accounts evidenced")
    taken = {rid for d in direct.values() for rid in d}
    cands: dict[str, set | None] = {}
    anchors: dict[str, str] = {}
    anchor_events: dict[str, dict] = {}
    for c in cases:
        cid = c["case_id"]
        if cid in out:
            continue
        mine = {r for r in evidenced_resources(c) if not r.startswith("account_")}
        ops = [a for a in worker if a["resource_id"] in mine and c["requested_at"] <= a["event_time"] <= c["audit_at"]]
        if not ops:
            if not LIFECYCLE_FALLBACK:
                cands[cid] = None
                continue
            # X1: no own operation history; the request lifecycle itself is the window
            cands[cid] = {rid for rid, a in deletes.items()
                          if rid not in taken and c["requested_at"] <= a["event_time"] <= c["audit_at"]
                          and (_t(a["event_time"]) - _t(c["reported_at"])).total_seconds() / 60 <= LIFECYCLE_TOLERANCE_MIN
                          and not other_owner(c, rid)}
            anchors[cid] = "lifecycle"
            continue
        anchor = min(ops, key=event_key)
        anchors[cid] = anchor["activity_id"]
        anchor_events[cid] = anchor
        t0 = _t(anchor["event_time"])
        cands[cid] = {rid for rid, a in deletes.items()
                      if rid not in taken and a["event_time"] >= c["requested_at"]
                      and 0 <= (t0 - _t(a["event_time"])).total_seconds() / 60 <= ANCHOR_WINDOW_MIN
                      and not other_owner(c, rid)
                      and (not STATUS_FILTER or a["reported_result"] == STATUS_RESULT.get(c["reported_status"]))}
    claimants: dict[str, int] = {}
    for cand in cands.values():
        for rid in cand or ():
            claimants[rid] = claimants.get(rid, 0) + 1
    if PROPAGATE:   # A1: an account given to one request leaves every other request's candidate set
        changed = True
        while changed:
            changed = False
            for cid, cand in cands.items():
                if cand and len(cand) == 1 and claimants[next(iter(cand))] == 1:
                    rid = next(iter(cand))
                    for other, oc in cands.items():
                        if other != cid and oc and rid in oc:
                            oc.discard(rid)
                            changed = True
                    claimants[rid] = 1
            claimants = {}
            for cand in cands.values():
                for rid in cand or ():
                    claimants[rid] = claimants.get(rid, 0) + 1
    for cid, cand in cands.items():
        if cand and len(cand) == 1 and claimants[next(iter(cand))] == 1:
            rid = next(iter(cand))
            out[cid] = Ownership("CONDITIONALLY_INFERRED", rid, [rid],
                                 f"only worker primary-profile deletion ({deletes[rid]['activity_id']}) "
                                 + ("inside the request lifecycle (no own operation history)" if anchors[cid] == "lifecycle"
                                    else f"preceding the subject's own evidenced operation {anchors[cid]}"))
        else:
            reason = ("no evidenced operation history" if cand is None else "no candidate in window" if not cand
                      else "competing candidates" if len(cand) > 1 else "candidate also fits another request")
            out[cid] = Ownership("UNRESOLVED", None, sorted(cand or ()), reason)
    if ORDINAL:
        out.update(ordinal_alignment(cases, out, cands, anchor_events, activities, other_owner, taken))
    return out


def event_key(a: dict) -> tuple:
    """Total order of activity events: event time, then the numeric activity id (file order never used)."""
    digits = "".join(ch for ch in a["activity_id"] if ch.isdigit())
    return a["event_time"], int(digits or 0), a["activity_id"]


def ordinal_alignment(cases, out, cands, anchor_events, activities, other_owner, taken) -> dict:
    """A4. Within a group of requests that compete for the same primary-profile deletions, match the
    k-th request (ordered by its own earliest evidenced worker operation) to the k-th deletion
    (ordered by its earliest event). Applied only when every condition below holds; otherwise the
    whole group stays UNRESOLVED:
      * the group is closed: every request in it has its own anchor, its candidates are exactly the
        group's deletions, and no other unassigned request (anchored or not) could plausibly claim one
        of them (an anchorless request's plausible set is its lifecycle requested_at .. reported_at+15m);
      * the group has as many deletions as requests (k >= 2): no missing, extra or decoy events;
      * request anchors and deletions each have strictly increasing event times (no equal timestamps);
      * (A4b) every anchor in the group is the same operation type on the same system;
      * each rank-matched deletion is in that request's own candidate set and precedes its anchor.
    A retried deletion of the same account counts once, at its earliest event. reported_status and
    reported_result are never read. Results are CONDITIONALLY_INFERRED: uncited future checks only.
    """
    first_delete: dict[str, dict] = {}
    for a in activities:
        if a["source_system"] == "deletion_worker" and a["target_system"] == "account_service" and a["event_type"] == "DELETE_RECORD":
            if a["resource_id"] not in first_delete or event_key(a) < event_key(first_delete[a["resource_id"]]):
                first_delete[a["resource_id"]] = a
    open_ids = {cid for cid, o in out.items() if o.tier == "UNRESOLVED"}
    anchored = {cid: set(cands[cid]) for cid in open_ids if cands.get(cid) and cid in anchor_events}
    assigned = {o.resource_id for o in out.values() if o.resource_id}
    by_id = {c["case_id"]: c for c in cases}

    def plausible(cid) -> set:
        if cid in anchored:
            return anchored[cid]
        c = by_id[cid]
        hi = _t(c["reported_at"])
        return {rid for rid, a in first_delete.items()
                if rid not in taken and rid not in assigned and c["requested_at"] <= a["event_time"] <= c["audit_at"]
                and (_t(a["event_time"]) - hi).total_seconds() / 60 <= LIFECYCLE_TOLERANCE_MIN and not other_owner(c, rid)}

    groups, seen = [], set()
    for start in anchored:          # connected components over shared candidate deletions
        if start in seen:
            continue
        reqs, accts, todo = set(), set(), [start]
        while todo:
            cid = todo.pop()
            if cid in reqs:
                continue
            reqs.add(cid)
            accts |= anchored[cid]
            todo += [o for o in anchored if o not in reqs and anchored[o] & accts]
        seen |= reqs
        groups.append((reqs, accts))
    result = {}
    for reqs, accts in groups:
        if len(reqs) < 2 or len(reqs) != len(accts):
            continue
        outsiders = [cid for cid in open_ids - reqs if plausible(cid) & accts]
        if outsiders:
            continue
        if ORDINAL_SAME_STREAM and len({(anchor_events[c]["event_type"], anchor_events[c]["target_system"]) for c in reqs}) > 1:
            continue    # A4b: anchors of different operation types sit at different offsets in a batch
        r_order = sorted(reqs, key=lambda cid: event_key(anchor_events[cid]))
        a_order = sorted(accts, key=lambda rid: event_key(first_delete[rid]))
        r_times = [anchor_events[cid]["event_time"] for cid in r_order]
        a_times = [first_delete[rid]["event_time"] for rid in a_order]
        if len(set(r_times)) < len(r_times) or len(set(a_times)) < len(a_times):
            continue
        pairs = list(zip(r_order, a_order))
        if not all(rid in anchored[cid] and first_delete[rid]["event_time"] < anchor_events[cid]["event_time"]
                   for cid, rid in pairs):
            continue
        for k, (cid, rid) in enumerate(pairs, 1):
            result[cid] = Ownership("CONDITIONALLY_INFERRED", rid, sorted(accts),
                                    f"ordinal alignment {k}/{len(pairs)}: worker deletion {first_delete[rid]['activity_id']} "
                                    f"matches the subject's operation order (anchor {anchor_events[cid]['activity_id']})")
    return result


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
