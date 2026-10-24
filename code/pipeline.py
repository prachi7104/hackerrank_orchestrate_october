"""Shared runner: evaluation and final generation call the same functions here."""
from __future__ import annotations

import csv
from pathlib import Path

from core import AuditContext, CaseResult, audit_case

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "dataset"


def read_rows(name: str) -> list[dict]:
    return list(csv.DictReader(open(DATASET / name, encoding="utf-8")))


def all_cases() -> tuple[list[dict], list[dict]]:
    """(sample requests, evaluation requests). Both share one activity stream, so account
    linkage is solved over all of them together."""
    return read_rows("sample_deletion_requests.csv"), read_rows("deletion_requests.csv")


def screenshot_observations(g) -> tuple[list, list]:
    """Validated screenshot observations (model transcription, cached) and the per-file report."""
    if g is None:
        return [], []
    import vision
    g.admit(vision.planned_requests(DATASET))
    return vision.extract(g, DATASET, read_rows("system_activity.csv"), read_rows("user_identities.csv"))


def build_context(g=None, status_compat: bool = True) -> AuditContext:
    """The only context used for generation: strict audit_at visibility (decision of 24 Oct).
    With a model client, screenshot observations are included; evaluation passes the same client.
    Record-time visibility is reachable only through build_dev_context in evaluation."""
    samples, evals = all_cases()
    shots, report = screenshot_observations(g)
    ctx = AuditContext(DATASET, samples + evals, status_compat=status_compat, record_time=False,
                       extra_observations=shots)
    ctx.screenshot_report = report
    ctx.restore_capability = restore_lines(g, shots)
    return ctx


def restore_lines(g, shots) -> dict:
    if g is None:
        return {}
    import vision
    g.admit(2)
    return vision.restore_capability(g, DATASET, shots)


def build_dev_context(g=None, status_compat: bool = True, record_time: bool = False) -> AuditContext:
    """Development comparisons only; never called by code/main.py."""
    samples, evals = all_cases()
    shots, report = screenshot_observations(g)
    ctx = AuditContext(DATASET, samples + evals, status_compat=status_compat, record_time=record_time,
                       extra_observations=shots)
    ctx.screenshot_report = report
    ctx.restore_capability = restore_lines(g, shots)
    return ctx


def authorize(g, cases: list[dict]) -> tuple[dict[str, bool], dict[str, str], dict]:
    """Model-based authorisation for every case: (intents, failures, authorisation records).
    A case whose text could not be classified appears only in failures."""
    from intent import classify, planned_requests
    texts = [c["request_text"] for c in cases]
    g.admit(planned_requests(texts))
    auth = classify(g, texts)
    intents, failures, records = {}, {}, {}
    for c in cases:
        a = auth[c["request_text"]]
        records[c["case_id"]] = a.__dict__
        if a.status == "ok":
            intents[c["case_id"]] = a.authorizes_deletion
        else:
            failures[c["case_id"]] = a.error
    return intents, failures, records


def run(ctx: AuditContext, cases: list[dict], intents: dict[str, bool]) -> dict[str, CaseResult]:
    """intents: case_id -> True if the request authorises deletion. A case without an intent
    decision is an operational gap and is not audited (the caller decides how to report it)."""
    return {c["case_id"]: audit_case(ctx, c, intents[c["case_id"]]) for c in cases if c["case_id"] in intents}
