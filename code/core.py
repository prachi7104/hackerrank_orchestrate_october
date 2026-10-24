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
from linking import AccountLink, assign_accounts
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
        direct = {c["case_id"]: {r for r, _ in self.linked(c, self.subjects[c["case_id"]]).items()
                                 if r.startswith("account_")} for c in cases}
        self.accounts: dict[str, AccountLink] = assign_accounts(cases, direct, self.activities, status_compat)

    def visible(self, o: Observation, audit_at: str) -> bool:
        return o.observed_at <= audit_at and (self.record_time or o.captured_at <= audit_at)

    def linked(self, case: dict, subject: Subject) -> dict[str, str]:
        """resource_id -> provenance, for resources whose evidence (<= audit_at) carries a subject key."""
        keys = subject.link_keys() | {norm(case["email"])}
        out = {}
        for k in keys:
            for o in self.by_key.get(k, []):
                if self.visible(o, case["audit_at"]):
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


def assess(ctx: AuditContext, rid: str, case: dict, link: str) -> ResourceAssessment:
    visible = [o for o in ctx.by_resource.get(rid, []) if ctx.visible(o, case["audit_at"])]
    system = next((o.system_id for o in visible if o.system_id != "unknown"), system_for(rid))
    category = next((o.category for o in sorted(visible, key=lambda o: o.observed_at, reverse=True) if o.category), None)
    category = category or SYSTEM_CATEGORY.get(system, "UNKNOWN")
    action = "PROCESS" if category == "DATA_FLOW" else ctx.rules.get(category, {}).get("required_action", "UNKNOWN")
    # State must be observed after the request was made: an earlier capture shows the record before
    # the deletion workflow ran, so it can link the resource but cannot say what remains at audit_at.
    stateful = [o for o in visible if (o.exists is not None or o.active is not None or o.result)
                and o.observed_at >= case["requested_at"]]
    last = latest(stateful)
    if last is None and rid.startswith("account_"):
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
    if a.category == "DATA_FLOW":
        return ("FLOW", None) if a.state == "ACTIVE" else (None, "VERIFY_DATA_FLOW_DISABLED")
    if a.category == "BACKUP_ARCHIVE":
        return ("RESTORE", None) if a.state == "ACTIVE" else (None, "VERIFY_RESTORE_BLOCKED")
    if a.state == "UNKNOWN":
        return None, None
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


def audit_case(ctx: AuditContext, case: dict, authorizes_deletion: bool) -> CaseResult:
    cid = case["case_id"]
    if not authorizes_deletion:
        return CaseResult(cid, "NOT_APPLICABLE", [], {"reason": "request does not authorise deletion"})
    subject = ctx.subjects[cid]
    links = ctx.linked(case, subject)
    acct = ctx.accounts.get(cid)
    if acct and acct.resource_id and acct.resource_id not in links:
        links[acct.resource_id] = f"activity:{acct.activity_id} ({acct.method})"
    assessments = [assess(ctx, rid, case, why) for rid, why in sorted(links.items())]

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
    if findings:
        status = "INCOMPLETE"
        # The primary account must end ABSENT; that future check needs no current evidence.
        for a in assessments:
            if a.resource_id == (acct.resource_id if acct else None) and a.state == "UNKNOWN":
                verify_after.append(Step("VERIFY_RECORD_ABSENT", "account_service", a.resource_id))
        steps = order_steps(findings) + order_steps(remediation) + order_steps(
            verify_after + [Step(s.operation, s.system_id, s.resource_id) for s in verify_now])
    else:
        status = "COMPLETE"
        steps = order_steps(verify_now)
    trace = {
        "subject": {"user_ids": sorted(subject.user_ids), "identifiers": len(subject.identifiers)},
        "account_link": acct.__dict__ if acct else None,
        "resources": [a.__dict__ for a in assessments],
    }
    return CaseResult(cid, status, steps, trace)
