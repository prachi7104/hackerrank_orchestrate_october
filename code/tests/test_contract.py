"""Contract and invariant tests. Oracles: the released output contract and the 32 public answers."""
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import DATASET, build_context, read_rows, run  # noqa: E402
from schema import Step, order_steps, parse_steps, serialise_steps  # noqa: E402


@pytest.fixture(scope="module")
def ctx():
    return build_context()


def test_ordering_rule_reproduces_every_public_answer():
    # Oracle: sample_output.csv. Re-sorting each gold array with our key must not change it.
    for row in read_rows("sample_output.csv"):
        gold = json.loads(row["steps_json"])
        steps = [Step(s["operation"], s["system_id"], s["resource_id"], s["evidence_id"]) for s in gold]
        phases = [s.phase for s in steps]
        assert phases == sorted(phases, key=["FINDING", "REMEDIATION", "VERIFICATION"].index)
        assert [s.sort_key() for s in steps] == sorted(s.sort_key() for s in steps), row["case_id"]


def test_serialisation_round_trip_and_strict_parse():
    steps = order_steps([Step("VERIFY_RECORD_ABSENT", "search_service", "search_2"),
                         Step("DELETE_RECORD", "search_service", "search_2"),
                         Step("REPORT_REMAINING_RECORD", "search_service", "search_2", "evidence_01")])
    parsed = parse_steps(serialise_steps(steps))
    assert [s["step_no"] for s in parsed] == [1, 2, 3]
    assert [s["phase"] for s in parsed] == ["FINDING", "REMEDIATION", "VERIFICATION"]
    bad = json.loads(serialise_steps(steps))
    bad[1]["step_no"] = 5
    with pytest.raises(ValueError):
        parse_steps(json.dumps(bad))


def test_historical_identifiers_are_resolved(ctx):
    # case_12: request uses the current email; the evidence only carries the old email's hash.
    subject = ctx.identities.resolve("minjun.kim@gmail.com", "2026-09-15T12:00:00Z")
    assert "44a65154bfd8e408852578fbd76813d094d1944f255ac1769ee81ba90492f10e" in subject.link_keys()
    # case_34 (evaluation row): support attachment matched only by the old phone number.
    subject = ctx.identities.resolve("haruka.s1838@zoho.com", "2026-09-27T13:00:00Z")
    assert "tel:819061560005" in subject.link_keys()


def test_no_citation_after_audit_and_no_forced_links(ctx):
    captured = {r["file_id"]: r["captured_at"] for r in csv.DictReader(open(DATASET / "evidence_files.csv", encoding="utf-8"))}
    cases = read_rows("sample_deletion_requests.csv") + read_rows("deletion_requests.csv")
    results = run(ctx, cases, {c["case_id"]: True for c in cases})
    by_id = {c["case_id"]: c for c in cases}
    for cid, r in results.items():
        for s in r.steps:
            if s.evidence_id:
                assert captured[s.evidence_id] <= by_id[cid]["audit_at"], (cid, s)
            if r.status == "INCOMPLETE" and s.phase != "FINDING":
                assert s.evidence_id == "", (cid, s)
        link = ctx.accounts[cid]
        if link.method in ("ambiguous", "none"):
            assert link.resource_id is None
            assert not any(s.resource_id in link.candidates for s in r.steps if s.system_id == "account_service"), cid


def test_generation_context_is_strict(ctx):
    # Decision of 24 Oct: final generation must never use record-time visibility.
    import inspect
    import pipeline
    assert "record_time" not in inspect.signature(pipeline.build_context).parameters
    assert ctx.record_time is False
    main_src = (Path(pipeline.__file__).parent / "main.py").read_text(encoding="utf-8")
    assert "build_dev_context" not in main_src and "record_time" not in main_src


def test_generation_is_deterministic(ctx):
    cases = read_rows("sample_deletion_requests.csv")
    a = run(ctx, cases, {c["case_id"]: True for c in cases})
    b = run(build_context(), cases, {c["case_id"]: True for c in cases})
    assert {k: serialise_steps(v.steps) for k, v in a.items()} == {k: serialise_steps(v.steps) for k, v in b.items()}
