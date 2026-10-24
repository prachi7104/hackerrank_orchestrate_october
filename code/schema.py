"""Output contract: the single source of truth for statuses, operations, ordering and serialisation."""
from __future__ import annotations

import json
from dataclasses import dataclass

OUTPUT_COLUMNS = ["case_id", "deletion_status", "steps_json"]
STATUSES = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")

# (phase, operation, state) in the order of the problem statement's table; the index is the sort key.
OPERATIONS = [
    ("FINDING", "REPORT_REMAINING_RECORD", "PRESENT"),
    ("FINDING", "REPORT_EXPOSED_PII", "PRESENT"),
    ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE"),
    ("FINDING", "REPORT_RESTORE_RISK", "ACTIVE"),
    ("REMEDIATION", "DISABLE_DATA_FLOW", "DISABLED"),
    ("REMEDIATION", "BLOCK_RESTORE", "BLOCKED"),
    ("REMEDIATION", "DELETE_RECORD", "ABSENT"),
    ("REMEDIATION", "REDACT_RECORD", "REDACTED"),
    ("REMEDIATION", "ANONYMIZE_RECORD", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT"),
    ("VERIFICATION", "VERIFY_RECORD_PRESENT", "PRESENT"),
    ("VERIFICATION", "VERIFY_RECORD_REDACTED", "REDACTED"),
    ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED"),
    ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
    ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
]
OP_INDEX = {op: i for i, (_, op, _) in enumerate(OPERATIONS)}
OP_PHASE = {op: ph for ph, op, _ in OPERATIONS}
OP_STATE = {op: st for _, op, st in OPERATIONS}

# Required treatment chains: finding -> remediation -> post-remediation verification.
CHAINS = {
    "REMAINING": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "EXPOSED_REDACT": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "EXPOSED_ANONYMIZE": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "FLOW": ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    "RESTORE": ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
}


@dataclass(frozen=True)
class Step:
    operation: str
    system_id: str
    resource_id: str
    evidence_id: str = ""

    @property
    def phase(self) -> str:
        return OP_PHASE[self.operation]

    @property
    def state(self) -> str:
        return OP_STATE[self.operation]

    def sort_key(self):
        return (OP_INDEX[self.operation], self.system_id, self.resource_id)


def order_steps(steps) -> list[Step]:
    """Phase, then operation-table order, then system_id, then resource_id (problem statement, Rules)."""
    unique = {(s.operation, s.system_id, s.resource_id): s for s in steps}
    return sorted(unique.values(), key=Step.sort_key)


def serialise_steps(steps: list[Step]) -> str:
    rows = [
        {
            "step_no": i,
            "phase": s.phase,
            "system_id": s.system_id,
            "resource_id": s.resource_id,
            "operation": s.operation,
            "state": s.state,
            "evidence_id": s.evidence_id,
        }
        for i, s in enumerate(steps, 1)
    ]
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def parse_steps(text: str) -> list[dict]:
    """Strict parse of a steps_json cell; raises ValueError on any contract violation."""
    steps = json.loads(text)
    if not isinstance(steps, list):
        raise ValueError("steps_json is not an array")
    keys = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
    for i, s in enumerate(steps, 1):
        if set(s) != keys:
            raise ValueError(f"step {i}: keys {sorted(s)}")
        if s["step_no"] != i:
            raise ValueError(f"step {i}: step_no {s['step_no']}")
        if s["operation"] not in OP_INDEX or OP_PHASE[s["operation"]] != s["phase"] or OP_STATE[s["operation"]] != s["state"]:
            raise ValueError(f"step {i}: invalid phase/operation/state {s['phase']}/{s['operation']}/{s['state']}")
    return steps
