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


def build_context(status_compat: bool = True) -> AuditContext:
    """The only context used for generation: strict audit_at visibility (decision of 24 Oct).
    Record-time visibility is reachable only through build_dev_context in evaluation."""
    samples, evals = all_cases()
    return AuditContext(DATASET, samples + evals, status_compat=status_compat, record_time=False)


def build_dev_context(status_compat: bool = True, record_time: bool = False) -> AuditContext:
    """Development comparisons only; never called by code/main.py."""
    samples, evals = all_cases()
    return AuditContext(DATASET, samples + evals, status_compat=status_compat, record_time=record_time)


def run(ctx: AuditContext, cases: list[dict], intents: dict[str, bool]) -> dict[str, CaseResult]:
    """intents: case_id -> True if the request authorises deletion. A case without an intent
    decision is an operational gap and is not audited (the caller decides how to report it)."""
    return {c["case_id"]: audit_case(ctx, c, intents[c["case_id"]]) for c in cases if c["case_id"] in intents}
