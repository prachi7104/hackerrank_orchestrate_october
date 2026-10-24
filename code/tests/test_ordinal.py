"""A4/A4b ordinal alignment under adversarial perturbations (synthetic worker streams)."""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import linking  # noqa: E402


def case(cid, req="2026-09-01T10:00:00Z", rep="2026-09-02T12:00:00Z", audit="2026-09-10T00:00:00Z"):
    return {"case_id": cid, "requested_at": req, "reported_at": rep, "audit_at": audit, "reported_status": ""}


def act(aid, rid, t, target="account_service", etype="DELETE_RECORD"):
    return {"activity_id": aid, "event_time": t, "source_system": "deletion_worker", "target_system": target,
            "event_type": etype, "resource_id": rid, "reported_result": "", "details": ""}


BASE = [act("activity_101", "account_1", "2026-09-02T10:50:00Z"), act("activity_102", "account_2", "2026-09-02T11:05:00Z"),
        act("activity_110", "search_1", "2026-09-02T11:38:00Z", "search_service"),
        act("activity_111", "search_2", "2026-09-02T11:39:00Z", "search_service")]
EVID = {"c1": {"search_1"}, "c2": {"search_2"}}
CASES = [case("c1"), case("c2")]


@pytest.fixture(autouse=True)
def ordinal_on():
    old = (linking.ORDINAL, linking.ORDINAL_SAME_STREAM)
    linking.ORDINAL, linking.ORDINAL_SAME_STREAM = True, True
    yield
    linking.ORDINAL, linking.ORDINAL_SAME_STREAM = old


def run(acts, cases=CASES, evid=EVID):
    out = linking.conditional_ownership(cases, {}, acts, lambda c, r: False, lambda c: evid.get(c["case_id"], set()))
    return {k: (v.tier, v.resource_id) for k, v in out.items()}


def test_aligned_group_is_matched_in_order():
    assert run(BASE) == {"c1": ("CONDITIONALLY_INFERRED", "account_1"), "c2": ("CONDITIONALLY_INFERRED", "account_2")}


def test_file_order_does_not_matter():
    acts = BASE[:]
    random.Random(3).shuffle(acts)
    assert run(acts) == run(BASE)


def test_equal_anchor_timestamps_abstain():
    acts = BASE[:3] + [act("activity_111", "search_2", "2026-09-02T11:38:00Z", "search_service")]
    assert {v[0] for v in run(acts).values()} == {"UNRESOLVED"}


def test_decoy_deletion_abstains():
    acts = BASE + [act("activity_103", "account_9", "2026-09-02T11:10:00Z")]
    assert {v[0] for v in run(acts).values()} == {"UNRESOLVED"}


def test_missing_deletion_abstains():
    assert {v[0] for v in run([a for a in BASE if a["resource_id"] != "account_2"]).values()} == {"UNRESOLVED"}


def test_retry_counts_once_at_earliest_event():
    acts = BASE + [act("activity_120", "account_1", "2026-09-02T11:20:00Z")]   # retried later
    assert run(acts) == run(BASE)


def test_different_operation_streams_abstain():
    acts = BASE[:3] + [act("activity_111", "data_flow_2", "2026-09-02T11:39:00Z", "integration_service", "CHECK_DATA_FLOW")]
    assert {v[0] for v in run(acts, evid={"c1": {"search_1"}, "c2": {"data_flow_2"}}).values()} == {"UNRESOLVED"}


def test_plausible_outsider_without_anchor_abstains():
    cases = CASES + [case("c3")]          # c3 has no own operations but its lifecycle admits both deletions
    out = run(BASE, cases=cases)
    assert out["c1"][0] == out["c2"][0] == "UNRESOLVED"
