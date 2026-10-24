"""Three-tier ownership tests: synthetic cases for each rule, plus invariance on the real data."""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from linking import conditional_ownership  # noqa: E402
from pipeline import build_context, read_rows  # noqa: E402


def case(cid, req="2026-09-01T10:00:00Z", rep="2026-09-02T12:00:00Z", audit="2026-09-10T00:00:00Z", status="COMPLETE"):
    return {"case_id": cid, "requested_at": req, "reported_at": rep, "audit_at": audit, "reported_status": status}


def worker(aid, rid, t, target="account_service", result="SUCCESS"):
    return {"activity_id": aid, "event_time": t, "source_system": "deletion_worker", "target_system": target,
            "event_type": "DELETE_RECORD", "resource_id": rid, "reported_result": result, "details": ""}


EVIDENCED = {"c1": {"search_1"}, "c2": {"search_2"}}


def run(cases, acts, direct=None, other=lambda c, r: False, evidenced=EVIDENCED):
    return conditional_ownership(cases, direct or {}, acts, other, lambda c: evidenced.get(c["case_id"], set()))


def test_unique_candidate_before_own_operation_is_inferred():
    out = run([case("c1")], [worker("a1", "account_1", "2026-09-02T11:00:00Z"),
                              worker("a2", "search_1", "2026-09-02T12:30:00Z", "search_service")])
    assert out["c1"].tier == "CONDITIONALLY_INFERRED" and out["c1"].resource_id == "account_1"


def test_duplicate_candidates_stay_unresolved():
    out = run([case("c1")], [worker("a1", "account_1", "2026-09-02T11:00:00Z"),
                              worker("a3", "account_3", "2026-09-02T11:30:00Z"),
                              worker("a2", "search_1", "2026-09-02T12:30:00Z", "search_service")])
    assert out["c1"].tier == "UNRESOLVED" and out["c1"].candidates == ["account_1", "account_3"]


def test_candidate_admitted_by_two_requests_is_not_given_to_either():
    acts = [worker("a1", "account_1", "2026-09-02T11:00:00Z"),
            worker("a2", "search_1", "2026-09-02T12:30:00Z", "search_service"),
            worker("a4", "search_2", "2026-09-02T12:40:00Z", "search_service")]
    out = run([case("c1"), case("c2")], acts)
    assert out["c1"].tier == out["c2"].tier == "UNRESOLVED"


def test_conflicting_identity_evidence_excludes_candidate():
    out = run([case("c1")], [worker("a1", "account_1", "2026-09-02T11:00:00Z"),
                              worker("a2", "search_1", "2026-09-02T12:30:00Z", "search_service")],
              other=lambda c, r: r == "account_1")
    assert out["c1"].tier == "UNRESOLVED"


def test_unmatched_event_and_missing_operation_history_infer_nothing():
    # an account deletion with no evidenced operation of the subject is never assigned
    out = run([case("c1")], [worker("a1", "account_1", "2026-09-02T11:00:00Z")])
    assert out["c1"].tier == "UNRESOLVED" and out["c1"].basis == "no evidenced operation history"
    # an event before the request cannot belong to it
    out = run([case("c1")], [worker("a1", "account_1", "2026-08-30T11:00:00Z"),
                              worker("a2", "search_1", "2026-09-02T12:30:00Z", "search_service")])
    assert out["c1"].tier == "UNRESOLVED"


def test_direct_evidence_wins_and_removes_account_from_others():
    acts = [worker("a1", "account_1", "2026-09-02T11:00:00Z"),
            worker("a2", "search_1", "2026-09-02T12:30:00Z", "search_service")]
    out = run([case("c1"), case("c2")], acts, direct={"c2": {"account_1"}})
    assert out["c2"].tier == "DIRECT" and out["c1"].tier == "UNRESOLVED"


@pytest.fixture(scope="module")
def real():
    return build_context(), read_rows("sample_deletion_requests.csv") + read_rows("deletion_requests.csv")


@pytest.mark.parametrize("mutation", ["shuffle", "remove"])
def test_ownership_ignores_reported_status_and_results(real, mutation):
    ctx, cases = real
    baseline = {k: (v.tier, v.resource_id) for k, v in ctx.ownership.items()}
    rng = random.Random(7)
    statuses = [c["reported_status"] for c in cases]
    rng.shuffle(statuses)
    mutated = [{**c, "reported_status": s if mutation == "shuffle" else ""} for c, s in zip(cases, statuses)]
    acts = [{**a, "reported_result": "" if mutation == "remove" else rng.choice(["SUCCESS", "FAILED", "PARTIAL"])}
            for a in ctx.activities]
    direct = {c["case_id"]: {r for r in ctx.linked(c, ctx.subjects[c["case_id"]], since=c["requested_at"])
                             if r.startswith("account_")} for c in mutated}
    out = conditional_ownership(mutated, direct, acts, ctx.owned_by_other,
                                lambda c: set(ctx.linked(c, ctx.subjects[c["case_id"]])))
    assert {k: (v.tier, v.resource_id) for k, v in out.items()} == baseline
