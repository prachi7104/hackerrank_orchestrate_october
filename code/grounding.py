"""Check that each cited step is supported by what the cited evidence actually says.

A citation is supported only if the cited file (or activity) contains an observation of the same
resource whose state matches the step, captured at or before audit_at, and the resource is tied to
the case subject by some visible evidence. An evidence id that merely exists is not support.
"""
from __future__ import annotations

from core import AuditContext, REFERENCE_IS_IDENTIFYING
from evidence import DIRECT_FIELDS, field_name, norm

IDENTIFYING = DIRECT_FIELDS | {"message_text", "correlation_key"}


def identifying(o, category: str) -> bool:
    fields = {field_name(k) for k in o.content}
    extra = {"customer_reference", "user_id"} if category in REFERENCE_IS_IDENTIFYING else set()
    return bool(fields & (IDENTIFYING | extra)) or o.result == "IDENTIFIABLE"


def state_supports(op: str, o, category: str) -> bool:
    if op == "REPORT_REMAINING_RECORD":
        return o.exists is True
    if op == "REPORT_EXPOSED_PII":
        return o.exists is True and identifying(o, category)
    if op in ("REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"):
        return o.active is True
    if op == "VERIFY_RECORD_ABSENT":
        return o.exists is False
    if op == "VERIFY_RECORD_PRESENT":
        return o.exists is True
    if op in ("VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED"):
        return o.exists is True and not identifying(o, category) or o.result in ("REDACTED", "ANONYMIZED")
    if op in ("VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"):
        return o.active is False
    return False


def check_step(ctx: AuditContext, case: dict, step, category: str, subject_linked: bool) -> tuple[bool, str]:
    cited = step.evidence_id
    if not cited:
        return True, "future step (no citation required)"
    if not subject_linked:
        return False, "resource not tied to the subject by visible evidence"
    obs = [o for o in ctx.by_resource.get(step.resource_id, []) if o.file_id == cited]
    if not obs and step.operation == "VERIFY_RECORD_ABSENT":
        absent = [s for s in ctx.absence if s[0] == cited and step.resource_id not in s[2]]
        if absent and absent[0][1] <= case["audit_at"]:
            return True, "absent from a complete profile capture"
    if not obs:
        return False, f"{cited} says nothing about {step.resource_id}"
    if all(o.captured_at > case["audit_at"] for o in obs):
        return False, f"{cited} captured after audit_at"
    if not any(state_supports(step.operation, o, category) for o in obs):
        return False, f"{cited} does not show the state {step.state}"
    keys = ctx.subjects[case["case_id"]].link_keys()
    named = {norm(o.content[f]) for o in obs for f in ("email", "requester_email", "customer_email", "owner_email")
             if f in o.content}
    if named and not named & keys:
        return False, f"{cited} names another person on {step.resource_id}"
    return True, "supported"


def audit_result(ctx: AuditContext, case: dict, result) -> list[dict]:
    """Per cited step: supported or not, and why."""
    subject_keys = ctx.subjects[case["case_id"]].link_keys()
    linked = ctx.linked(case, ctx.subjects[case["case_id"]])
    categories = {a["resource_id"]: a["category"] for a in result.trace.get("resources", [])}
    out = []
    for s in result.steps:
        if not s.evidence_id:
            continue
        ok, why = check_step(ctx, case, s, categories.get(s.resource_id, ""), s.resource_id in linked or not subject_keys)
        out.append({"resource_id": s.resource_id, "operation": s.operation, "evidence_id": s.evidence_id,
                    "supported": ok, "reason": why})
    return out
