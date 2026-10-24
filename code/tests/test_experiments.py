"""Experiment-branch tests: C1 hashed-identifier rule, the inclusive audit_at boundary, unrelated evidence."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from evidence import Observation  # noqa: E402
from pipeline import build_context, read_rows  # noqa: E402

HASH = "44a65154bfd8e408852578fbd76813d094d1944f255ac1769ee81ba90492f10e"


def _case(cid):
    return next(c for c in read_rows("sample_deletion_requests.csv") + read_rows("deletion_requests.csv") if c["case_id"] == cid)


def test_hash_only_record_is_secondary_identifier_but_hash_plus_email_is_not():
    ctx = build_context()
    case = _case("case_12")
    a = core.assess(ctx, "analytics_9914", case, "test")
    assert a.category == "SECONDARY_IDENTIFIER" and a.action == "DELETE"
    # metamorphic: the same record also showing an email is an ordinary analytics profile
    extra = Observation("evidence_01", "2026-09-15T11:59:39Z", "2026-09-15T11:58:30Z", "analytics_9914",
                        "analytics_platform", exists=True, content={"correlation_key": HASH, "email": "x@example.com"})
    ctx.by_resource["analytics_9914"].append(extra)
    try:
        assert core.assess(ctx, "analytics_9914", case, "test").category == "ANALYTICS_PROFILE"
    finally:
        ctx.by_resource["analytics_9914"].remove(extra)


def test_audit_at_boundary_is_inclusive_and_strict():
    # case_14's evidence_03 is captured exactly at audit_at: usable at the boundary, unusable one second earlier.
    ctx = build_context()
    case = _case("case_14")
    at = core.audit_case(ctx, case, True)
    before = core.audit_case(ctx, {**case, "audit_at": "2026-10-01T11:59:59Z"}, True)
    assert any(s.evidence_id == "evidence_03" for s in at.steps)
    assert not any(s.evidence_id == "evidence_03" for s in before.steps)


def test_unrelated_subject_evidence_does_not_change_a_case():
    # adding a record for a stranger (no subject identifier) must not alter case_11
    ctx = build_context()
    case = _case("case_11")
    base = core.audit_case(ctx, case, True).steps
    stranger = Observation("evidence_03", "2026-10-01T12:00:00Z", "2026-10-01T11:00:00Z", "search_99999", "search_service",
                           exists=True, content={"email": "stranger@example.com"})
    ctx.by_resource["search_99999"].append(stranger)
    ctx.by_key["stranger@example.com"].append(stranger)
    assert core.audit_case(ctx, case, True).steps == base
