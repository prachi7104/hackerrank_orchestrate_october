"""R1/R2 experiments: record-time evidence from later-exported files may only plan uncited future checks."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core  # noqa: E402
from evidence import Observation  # noqa: E402
from pipeline import build_context, read_rows  # noqa: E402

CASE = next(c for c in read_rows("sample_deletion_requests.csv") if c["case_id"] == "case_24")   # audit 2026-09-15T12:00
KEY = "user_24"


def obs(rid, system, observed, captured="2026-09-17T12:00:00Z", **kw):
    return Observation("evidence_02", captured, observed, rid, system, exists=True, content={"customer_reference": KEY}, **kw)


def with_obs(ctx, o):
    ctx.by_resource[o.resource_id].append(o)
    ctx.by_key[KEY].append(o)


def test_r1_uses_only_records_timed_inside_the_request_window():
    ctx = build_context()
    for rid, t in (("audit_record_90001", "2026-08-31T00:00:00Z"),     # before requested_at
                   ("audit_record_90002", "2026-09-15T12:00:01Z"),     # after audit_at
                   ("audit_record_90003", "2026-09-10T00:00:00Z")):    # inside
        with_obs(ctx, obs(rid, "security_service", t))
    found = core.retained_future_checks(ctx, CASE, set())
    assert "audit_record_90003" in found and "audit_record_90001" not in found and "audit_record_90002" not in found


def test_r1_ignores_non_retained_categories_and_files_captured_in_time():
    ctx = build_context()
    with_obs(ctx, obs("search_90004", "search_service", "2026-09-10T00:00:00Z"))                       # DELETE category
    with_obs(ctx, obs("audit_record_90005", "security_service", "2026-09-10T00:00:00Z",
                      captured="2026-09-14T00:00:00Z"))                                                 # admissible file: regular path
    found = core.retained_future_checks(ctx, CASE, set())
    assert "search_90004" not in found and "audit_record_90005" not in found


def test_r1_r2_never_cite_and_never_change_status():
    ctx = build_context()
    base = core.audit_case(ctx, CASE, True)
    core.RETAINED_FUTURE_CHECK = core.ACCOUNT_FUTURE_CHECK = True
    try:
        new = core.audit_case(ctx, CASE, True)
    finally:
        core.RETAINED_FUTURE_CHECK = core.ACCOUNT_FUTURE_CHECK = False
    added = set(new.steps) - set(base.steps)
    assert new.status == base.status and added and all(s.evidence_id == "" and s.phase == "VERIFICATION" for s in added)


def test_r2_abstains_on_two_accounts_or_foreign_owner():
    ctx = build_context()
    with_obs(ctx, obs("account_90006", "account_service", "2026-09-10T00:00:00Z"))
    with_obs(ctx, obs("account_90007", "account_service", "2026-09-11T00:00:00Z"))
    found = core.record_time_accounts(ctx, CASE)
    assert {"account_90006", "account_90007"} <= set(found) and len(found) > 1   # caller adds a step only for exactly one
    foreign = Observation("evidence_02", "2026-09-17T12:00:00Z", "2026-09-10T00:00:00Z", "account_90006", "account_service",
                          exists=True, content={"email": "stranger.owner@example.com", "customer_reference": "user_999"})
    ctx.by_resource["account_90006"].append(foreign)
    ctx.owner_of_key["user_999"] = "user_999"
    assert "account_90006" not in core.record_time_accounts(ctx, CASE)
