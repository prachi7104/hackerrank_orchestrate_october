"""Investigator verification: a treatment must be a deletion_rules.csv action for its system."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import allowed_actions  # noqa: E402
from pipeline import build_context  # noqa: E402


def test_treatment_actions_follow_rules_for_the_system():
    ctx = build_context()
    assert allowed_actions(ctx, "tombstone_17") == {"REDACT"}          # rule_15: tombstones are redacted, never deleted
    assert {"DELETE", "REDACT"} <= allowed_actions(ctx, "support_002")  # message (REDACT) or attachment / identifier (DELETE)
    assert "RETAIN" not in allowed_actions(ctx, "analytics_9914")


def test_case_46_regression_tombstone_delete_is_rejected():
    # Evaluation case_46: the model answered DELETE for tombstone_17; rule_15 requires REDACT.
    from agent import Toolbox, verify
    ctx = build_context()
    case = next(c for c in __import__("pipeline").read_rows("deletion_requests.csv") if c["case_id"] == "case_46")
    box = Toolbox(ctx, case)
    unit = box.read_resource("tombstone_17")["units"][0]["unit_id"]
    q = {"id": "Q1", "type": "TREATMENT", "resource_id": "tombstone_17"}
    keys = ctx.subjects["case_46"].link_keys()
    ok, why = verify({"outcome": "RESOLVED", "answer": "DELETE", "supporting_units": [unit]}, q, box, keys)
    assert not ok and "not a deletion_rules.csv action" in why
    ok, _ = verify({"outcome": "RESOLVED", "answer": "REDACT", "supporting_units": [unit]}, q, box, keys)
    assert ok
