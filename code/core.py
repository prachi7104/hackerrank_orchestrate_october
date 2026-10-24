"""Deterministic decision engine: one function, audit_case, used by evaluation and final generation.

Pipeline per case: subject identifiers -> linked resources (evidence at or before audit_at) ->
latest state per resource -> deletion_rules.csv treatment -> findings / remediation / verification.
Every linked resource carries its provenance so the trace can show why it belongs to the case.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from evidence import (DIRECT_FIELDS, Observation, account_absence_snapshots, field_name, load_observations,
                      norm, system_for)
from identity import IdentityIndex, Subject
from linking import AccountLink, Ownership, assign_accounts, conditional_ownership
from schema import CHAINS, Step, order_steps

SYSTEM_CATEGORY = {
    "account_service": "USER_PROFILE",
    "search_service": "SEARCH_INDEX_ENTRY",
    "support_platform": "SUPPORT_MESSAGE",
    "analytics_platform": "ANALYTICS_PROFILE",
    "export_service": "USER_EXPORT",
    "integration_service": "DATA_FLOW",
    "backup_service": "BACKUP_ARCHIVE",
    "security_service": "SECURITY_AUDIT_LOG",
    "identity_service": "ACCOUNT_TOMBSTONE",
    "legal_service": "LEGAL_HOLD_RECORD",
    "marketing_platform": "MARKETING_PROFILE",
    "cache_service": "CACHE_ENTRY",
    "email_delivery_service": "EMAIL_DELIVERY_LOG",
    "object_storage": "SUPPORT_ATTACHMENT",
}
# Primary-account ownership policy (review decisions, 24 Oct):
#   A  evidence (DIRECT) only
#   B  status-compatible timing match, used only for uncited post-remediation checks
#   C  status-compatible timing match used as ownership (experimental comparison only)
#   D  three-tier: DIRECT, else CONDITIONALLY_INFERRED (unique lifecycle match, no status),
#      used only for uncited post-remediation checks; otherwise UNRESOLVED
ACCOUNT_POLICY = "D"
EMAIL_FIELDS = ("email", "requester_email", "customer_email", "owner_email")
# Categories whose state may be taken from captures made before requested_at (development
# experiments only; empty means the post-request rule applies to every category).
PRE_REQUEST_STATE_CATEGORIES: set[str] = set()
# For these categories a stable user reference alone still makes the record attributable.
REFERENCE_IS_IDENTIFYING = {"ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"}


@dataclass
class ResourceAssessment:
    resource_id: str
    system_id: str
    category: str
    action: str                 # DELETE | REDACT | ANONYMIZE | RETAIN | PROCESS
    state: str                  # ABSENT | PRESENT_IDENTIFIABLE | PRESENT_CLEAN | ACTIVE | INACTIVE | UNKNOWN
    evidence_id: str            # file that established the state ("" if none)
    link: str                   # why this resource belongs to the case
    chain: str | None = None    # CHAINS key when a violation was found


@dataclass
class CaseResult:
    case_id: str
    status: str
    steps: list
    trace: dict = field(default_factory=dict)


class AuditContext:
    """Everything loaded once: observations, identities, rules, activity, account assignment."""

    def __init__(self, dataset: Path, cases: list[dict], status_compat: bool = True, record_time: bool = False,
                 extra_observations: list[Observation] = ()):
        """record_time=False (default): an observation is usable only if its file was captured by
        audit_at. record_time=True also uses records whose own capture time is <= audit_at inside a
        file exported later; such observations can inform linkage and state but are never cited."""
        self.dataset = dataset
        self.record_time = record_time
        self.activities = list(csv.DictReader(open(dataset / "system_activity.csv", encoding="utf-8")))
        self.observations, self.coverage = load_observations(dataset, catalog_aliases(self.activities))
        self.observations += catalog_observations(self.activities) + list(extra_observations)
        self.by_resource: dict[str, list[Observation]] = defaultdict(list)
        self.by_key: dict[str, list[Observation]] = defaultdict(list)
        for o in self.observations:
            self.by_resource[o.resource_id].append(o)
            for k in o.keys():
                self.by_key[k].append(o)
        self.absence = account_absence_snapshots(dataset)
        self.identities = IdentityIndex(dataset)
        self.rules = {r["data_category"]: r for r in csv.DictReader(open(dataset / "deletion_rules.csv", encoding="utf-8"))}
        self.subjects = {c["case_id"]: self.identities.resolve(c["email"], c["audit_at"]) for c in cases}
        # The primary account of *this* request must be evidenced after the request was made; an
        # account seen only earlier may belong to a previous request by the same person (case_17).
        direct = {c["case_id"]: {r for r in self.linked(c, self.subjects[c["case_id"]], since=c["requested_at"])
                                 if r.startswith("account_")} for c in cases}
        self.accounts: dict[str, AccountLink] = assign_accounts(cases, direct, self.activities, status_compat)
        self.owner_of_key = self.identities.owner_index()
        self.ownership: dict[str, Ownership] = conditional_ownership(
            cases, direct, self.activities, self.owned_by_other,
            lambda c: set(self.linked(c, self.subjects[c["case_id"]])))

    def owned_by_other(self, case: dict, rid: str) -> bool:
        """Admissible evidence (<= audit_at) ties this resource to a person other than the subject."""
        mine = self.subjects[case["case_id"]].user_ids
        for o in self.by_resource.get(rid, []):
            if self.visible(o, case["audit_at"]):
                for k in o.keys():
                    owner = self.owner_of_key.get(k)
                    if owner and owner not in mine:
                        return True
        return False

    def contradicted(self, case: dict, rid: str) -> str:
        """Content-bearing records about rid name other people and never the subject."""
        keys = self.subjects[case["case_id"]].link_keys()
        named = {norm(o.content[f]) for o in self.by_resource.get(rid, []) if self.visible(o, case["audit_at"])
                 for f in EMAIL_FIELDS if f in o.content}
        others = named - keys
        return f"records name {sorted(others)} but never the subject" if others and not named & keys else ""

    def visible(self, o: Observation, audit_at: str) -> bool:
        return o.observed_at <= audit_at and (self.record_time or o.captured_at <= audit_at)

    def linked(self, case: dict, subject: Subject, since: str = "") -> dict[str, str]:
        """resource_id -> provenance, for resources whose evidence (<= audit_at, and >= since when
        given) carries a subject key."""
        keys = subject.link_keys() | {norm(case["email"])}
        out = {}
        for k in keys:
            for o in self.by_key.get(k, []):
                if self.visible(o, case["audit_at"]) and o.observed_at >= since:
                    out.setdefault(o.resource_id, f"{o.file_id}:{o.locator} key={k}")
        return out


CATALOG_RE = re.compile(r"(?:linked|confirmed) (\S+) \(([^)]+)\) (?:to|belongs only to) (\S+) and (user_\d+)")


def catalog_observations(activities: list[dict]) -> list[Observation]:
    """Data-catalog LINK_RESOURCE_IDENTITY activity links a resource to a subject. It carries no state,
    so it can bring a resource into a case but never decides whether the resource is compliant."""
    out = []
    for a in activities:
        m = CATALOG_RE.search(a["details"]) if a["event_type"] == "LINK_RESOURCE_IDENTITY" else None
        if m:
            rid, _alias, email, user = m.groups()
            system = system_for(rid) if system_for(rid) != "unknown" else a["target_system"]
            out.append(Observation(a["activity_id"], a["event_time"], a["event_time"], rid, system,
                                   lookups={email, user}, collection="data_catalog", locator=a["activity_id"]))
    return out


def catalog_aliases(activities: list[dict]) -> dict[str, str]:
    """alias (file name, statement number, user id) -> resource id, from data-catalog activity."""
    out = {}
    for a in activities:
        m = CATALOG_RE.search(a["details"]) if a["event_type"] == "LINK_RESOURCE_IDENTITY" else None
        if m and m.group(2) != m.group(1):
            out[m.group(2)] = m.group(1)
    return out


def latest(obs: list[Observation]) -> Observation | None:
    return max(obs, key=lambda o: (o.observed_at, o.captured_at), default=None)


def assess(ctx: AuditContext, rid: str, case: dict, link: str, primary: str | None = None) -> ResourceAssessment:
    visible = [o for o in ctx.by_resource.get(rid, []) if ctx.visible(o, case["audit_at"])]
    system = next((o.system_id for o in visible if o.system_id != "unknown"), system_for(rid))
    category = next((o.category for o in sorted(visible, key=lambda o: o.observed_at, reverse=True) if o.category), None)
    category = category or SYSTEM_CATEGORY.get(system, "UNKNOWN")
    action = "PROCESS" if category == "DATA_FLOW" else ctx.rules.get(category, {}).get("required_action", "UNKNOWN")
    # State must be observed after the request was made: an earlier capture shows the record before
    # the deletion workflow ran, so it can link the resource but cannot say what remains at audit_at.
    stateful = [o for o in visible if (o.exists is not None or o.active is not None or o.result)
                and (o.observed_at >= case["requested_at"] or category in PRE_REQUEST_STATE_CATEGORIES)]
    if category in ("DATA_FLOW", "BACKUP_ARCHIVE"):
        # A flow or archive is judged by whether it can run/restore; a capture that only shows the
        # record exists (e.g. a listing screen) says nothing about that.
        stateful = [o for o in stateful if o.active is not None]
    last = latest(stateful)
    if last is None and rid == primary:   # absence from a full profile capture proves only the primary account
        proof = [s for s in ctx.absence if s[1] <= case["audit_at"] and rid not in s[2]]
        if proof:
            fid = max(proof, key=lambda s: s[1])[0]
            return ResourceAssessment(rid, "account_service", category, action, "ABSENT", fid, link + f"; absent from {fid}")
    if last is None:
        return ResourceAssessment(rid, system, category, action, "UNKNOWN", "", link)
    citable = [o for o in stateful if o.captured_at <= case["audit_at"]]
    cite = last.file_id if last in citable else (latest(citable).file_id if citable else "")
    if category in ("DATA_FLOW", "BACKUP_ARCHIVE") and last.active is not None:
        state = "ACTIVE" if last.active else "INACTIVE"
    elif last.exists is False:
        state = "ABSENT"
    elif last.result in ("IDENTIFIABLE",):
        state = "PRESENT_IDENTIFIABLE"
    elif last.result in ("ANONYMIZED", "REDACTED"):
        state = "PRESENT_CLEAN"
    elif last.result in ("FOUND", "PRESENT"):
        state = "PRESENT_IDENTIFIABLE" if action == "DELETE" else "PRESENT"
    else:
        fields = {field_name(k) for k in last.content}
        identifying = DIRECT_FIELDS | {"message_text", "correlation_key"}
        if category in REFERENCE_IS_IDENTIFYING:
            identifying |= {"customer_reference", "user_id"}
        state = "PRESENT_IDENTIFIABLE" if fields & identifying else "PRESENT_CLEAN"
    return ResourceAssessment(rid, system, category, action, state, cite, link)


def plan(a: ResourceAssessment) -> tuple[str | None, str | None]:
    """Return (violation chain or None, compliant verification op or None) for one resource."""
    if a.state == "UNKNOWN":
        return None, None
    if a.category == "DATA_FLOW":
        return ("FLOW", None) if a.state == "ACTIVE" else (None, "VERIFY_DATA_FLOW_DISABLED")
    if a.category == "BACKUP_ARCHIVE":
        return ("RESTORE", None) if a.state == "ACTIVE" else (None, "VERIFY_RESTORE_BLOCKED")
    if a.action == "RETAIN":
        return (None, "VERIFY_RECORD_PRESENT") if a.state != "ABSENT" else (None, None)
    if a.action == "DELETE":
        return ("REMAINING", None) if a.state.startswith("PRESENT") else (None, "VERIFY_RECORD_ABSENT")
    if a.action == "REDACT":
        if a.state == "PRESENT_IDENTIFIABLE":
            return "EXPOSED_REDACT", None
        return (None, "VERIFY_RECORD_REDACTED") if a.state.startswith("PRESENT") else (None, None)
    if a.action == "ANONYMIZE":
        if a.state == "PRESENT_IDENTIFIABLE":
            return "EXPOSED_ANONYMIZE", None
        return (None, "VERIFY_RECORD_ANONYMIZED") if a.state.startswith("PRESENT") else (None, None)
    return None, None


GAP_ACCOUNT_ABSENCE = False   # experiment X2: cited absence of an inferred account in gap cases


def subject_absence(ctx: AuditContext, case: dict, rid: str) -> str:
    """Latest complete profile capture (after requested_at, <= audit_at) that lacks rid and holds no
    profile carrying any of the subject's identifiers. Returns its file id or ''."""
    keys = ctx.subjects[case["case_id"]].link_keys()
    best = ""
    for fid, cap, present in sorted(ctx.absence, key=lambda s: s[1]):
        if not (case["requested_at"] <= cap <= case["audit_at"]) or rid in present:
            continue
        held = [o for r in present for o in ctx.by_resource.get(r, []) if o.file_id == fid]
        if not any(o.keys() & keys for o in held):
            best = fid
    return best


def evidence_gap(ctx: AuditContext, case: dict, assessments: list, links: dict, excluded: dict):
    """Evidence-gap policy for an authorised deletion with no provable resource state.

    Applies only when the regular rules produce no finding and no verification, so it cannot change
    any other row. Two outcomes, both recorded in trace["gap"] as policy, not proof:
      RESTORE_INFERRED   a linked backup archive is shown existing (admissible capture after the
                         request) on a screen that also prints completed restores of the platform;
                         restore blocking is unproven, so it is reported as a restore risk citing
                         that capture.
      UNRESOLVED_EVIDENCE nothing else is admissible: the row is serialised fail-closed as
                         INCOMPLETE with no steps, a contract exception.
    """
    restore_lines = getattr(ctx, "restore_capability", {})
    findings, remediation, verify_after, verify_now, inferred = [], [], [], [], []
    for a in assessments:
        if a.category != "BACKUP_ARCHIVE" or a.state != "UNKNOWN":
            continue
        shown = [o for o in ctx.by_resource.get(a.resource_id, []) if o.exists and ctx.visible(o, case["audit_at"])
                 and o.observed_at >= case["requested_at"] and o.file_id in restore_lines]
        if shown:
            fid = max(shown, key=lambda o: o.observed_at).file_id
            f_op, r_op, v_op = CHAINS["RESTORE"]
            findings.append(Step(f_op, a.system_id, a.resource_id, fid))
            remediation.append(Step(r_op, a.system_id, a.resource_id))
            verify_after.append(Step(v_op, a.system_id, a.resource_id))
            a.chain = "RESTORE"
            inferred.append({"resource_id": a.resource_id, "evidence_id": fid,
                             "basis": "archive of the subject shown existing; platform restore capability printed on "
                                      "the same screen: " + "; ".join(restore_lines[fid][:3]),
                             "not_proven": "restore access of this specific archive"})
    if inferred:
        return findings, remediation, verify_after, verify_now, {"policy": "RESTORE_INFERRED", "inferences": inferred}
    own = ctx.ownership.get(case["case_id"])
    if GAP_ACCOUNT_ABSENCE and own and own.tier == "CONDITIONALLY_INFERRED":
        proof = subject_absence(ctx, case, own.resource_id)
        if proof:
            verify_now.append(Step("VERIFY_RECORD_ABSENT", "account_service", own.resource_id, proof))
            return [], [], [], verify_now, {
                "policy": "ACCOUNT_ABSENCE_INFERRED", "resource_id": own.resource_id, "evidence_id": proof,
                "basis": f"{proof} is a complete profile capture after the request holding no profile with any "
                         f"subject identifier and not {own.resource_id}; {own.basis}",
                "not_proven": f"that {own.resource_id} was the subject's account (ownership inferred)"}
    return [], [], [], [], {"policy": "UNRESOLVED_EVIDENCE", "contract_exception": True,
                        "unknown_state": [a.resource_id for a in assessments if a.state == "UNKNOWN"],
                        "linked": sorted(links), "excluded_as_contradicted": excluded}


def audit_case(ctx: AuditContext, case: dict, authorizes_deletion: bool, overrides: dict | None = None) -> CaseResult:
    """overrides (investigator resolutions that passed code verification): add_links {rid: why},
    drop {rid}, action {rid: DELETE|REDACT|ANONYMIZE|RETAIN}, primary rid."""
    cid = case["case_id"]
    ov = overrides or {}
    if not authorizes_deletion:
        return CaseResult(cid, "NOT_APPLICABLE", [], {"reason": "request does not authorise deletion"})
    subject = ctx.subjects[cid]
    links = ctx.linked(case, subject)
    acct = ctx.accounts.get(cid)
    own = ctx.ownership.get(cid)
    excluded = {rid: why for rid in links if (why := ctx.contradicted(case, rid))}
    for rid in excluded:
        links.pop(rid)
    if ACCOUNT_POLICY == "C" and acct and acct.resource_id and acct.resource_id not in links:
        links[acct.resource_id] = f"activity:{acct.activity_id} ({acct.method})"
    links.update(ov.get("add_links", {}))
    for rid in ov.get("drop", ()):
        links.pop(rid, None)
    direct_account = own.resource_id if own and own.tier == "DIRECT" else None
    if ACCOUNT_POLICY in ("B", "C"):
        primary = acct.resource_id if acct else None
        future_only = acct.resource_id if ACCOUNT_POLICY == "B" and acct and acct.method == "activity" else None
    elif ACCOUNT_POLICY == "D":
        primary = direct_account or (own.resource_id if own else None)
        future_only = own.resource_id if own and own.tier == "CONDITIONALLY_INFERRED" else None
    else:
        primary, future_only = direct_account, None
    primary = ov.get("primary") or primary
    assessments = [assess(ctx, rid, case, why, primary) for rid, why in sorted(links.items())]
    verified_state = {"PRESENT": "PRESENT_IDENTIFIABLE", "ABSENT": "ABSENT", "RESTORE_ALLOWED": "ACTIVE",
                      "RESTORE_BLOCKED": "INACTIVE", "FLOW_ACTIVE": "ACTIVE", "FLOW_DISABLED": "INACTIVE"}
    for a in assessments:
        if a.resource_id in ov.get("state", {}):
            state, fid = ov["state"][a.resource_id]
            a.state, a.evidence_id = verified_state[state], fid
            if a.action == "RETAIN" and a.state == "PRESENT_IDENTIFIABLE":
                a.state = "PRESENT"
        if a.resource_id in ov.get("action", {}):
            a.action = ov["action"][a.resource_id]
            if a.action == "DELETE" and a.state.startswith("PRESENT"):
                a.state = "PRESENT_IDENTIFIABLE"

    findings, remediation, verify_after, verify_now = [], [], [], []
    for a in assessments:
        chain, ok_op = plan(a)
        a.chain = chain
        if chain:
            f_op, r_op, v_op = CHAINS[chain]
            findings.append(Step(f_op, a.system_id, a.resource_id, a.evidence_id))
            remediation.append(Step(r_op, a.system_id, a.resource_id))
            verify_after.append(Step(v_op, a.system_id, a.resource_id))
        elif ok_op:
            verify_now.append(Step(ok_op, a.system_id, a.resource_id, a.evidence_id))
    gap = None
    if not findings and not verify_now:
        findings, remediation, verify_after, verify_now, gap = evidence_gap(ctx, case, assessments, links, excluded)
    if findings:
        status = "INCOMPLETE"
        # The primary account must end ABSENT; that future check needs no current evidence.
        for a in assessments:
            if a.resource_id == primary and a.state == "UNKNOWN":
                verify_after.append(Step("VERIFY_RECORD_ABSENT", "account_service", a.resource_id))
        # An inferred primary account appears only as an uncited future check, never as a finding
        # or a cited verification, and only when no evidenced account step exists already.
        if future_only and not any(a.system_id == "account_service" for a in assessments):
            verify_after.append(Step("VERIFY_RECORD_ABSENT", "account_service", future_only))
        steps = order_steps(findings) + order_steps(remediation) + order_steps(
            verify_after + [Step(s.operation, s.system_id, s.resource_id) for s in verify_now])
    elif gap and gap["policy"] == "UNRESOLVED_EVIDENCE":
        # Not a contract status: the decision function reports the gap; serialisation is decided
        # outside it (code/main.py --gap-fallback) and only with explicit approval.
        status, steps = "UNRESOLVED_EVIDENCE", []
    else:
        status = "COMPLETE"
        steps = order_steps(verify_now)
    trace = {
        "subject": {"user_ids": sorted(subject.user_ids), "identifiers": len(subject.identifiers)},
        "account_link": acct.__dict__ if acct else None,
        "ownership": {**own.__dict__, "policy": ACCOUNT_POLICY} if own else None,
        "excluded": excluded,
        "gap": gap,
        "overrides": {k: sorted(v) if isinstance(v, set) else v for k, v in ov.items()},
        "resources": [a.__dict__ for a in assessments],
    }
    return CaseResult(cid, status, steps, trace)
