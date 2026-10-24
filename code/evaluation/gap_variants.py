"""Experiments X1 (lifecycle-unique inference for cases without own operation history) and X2
(cited absence of an inferred account in evidence-gap cases), against policy D as it stands.
Samples use answer-key authorisation (all authorise deletion); screenshots from cache; no API calls.
Usage: python code/evaluation/gap_variants.py
"""
from __future__ import annotations

import collections
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import core  # noqa: E402
import linking  # noqa: E402
from grounding import audit_result  # noqa: E402
from llm import Gemini  # noqa: E402
from pipeline import ROOT, build_context, read_rows  # noqa: E402
from schema import serialise_steps  # noqa: E402
from scoring import rows_to_map, score  # noqa: E402

CANDIDATE = ROOT / "runs/20261024T154304Z-9ebac6/output.csv"


def main() -> int:
    g = Gemini()
    samples, evals = read_rows("sample_deletion_requests.csv"), read_rows("deletion_requests.csv")
    gold = rows_to_map(read_rows("sample_output.csv"))
    na = {r["case_id"] for r in csv.DictReader(open(CANDIDATE, encoding="utf-8")) if r["deletion_status"] == "NOT_APPLICABLE"}
    base_rows = None
    for x1, x2 in ((False, False), (True, False), (False, True), (True, True)):
        linking.LIFECYCLE_FALLBACK, core.GAP_ACCOUNT_ABSENCE = x1, x2
        ctx = build_context(g)
        pred, unsupported, inferred = {}, collections.Counter(), collections.Counter()
        for c in samples:
            r = core.audit_case(ctx, c, True)
            pred[c["case_id"]] = (r.status, json.loads(serialise_steps(r.steps)))
            unsupported["samples"] += sum(not a["supported"] for a in audit_result(ctx, c, r))
            o = ctx.ownership[c["case_id"]]
            if o.tier == "CONDITIONALLY_INFERRED":
                want = {s["resource_id"] for s in gold[c["case_id"]][1] if s["system_id"] == "account_service"}
                inferred["correct" if o.resource_id in want else "wrong"] += 1
        rows, unresolved_eval = {}, []
        for c in evals:
            r = core.audit_case(ctx, c, c["case_id"] not in na)
            rows[c["case_id"]] = (r.status, serialise_steps(r.steps))
            if r.status == "UNRESOLVED_EVIDENCE":
                unresolved_eval.append(c["case_id"])
            if r.status not in ("NOT_APPLICABLE", "UNRESOLVED_EVIDENCE"):
                unsupported["eval"] += sum(not a["supported"] for a in audit_result(ctx, c, r))
        base_rows = base_rows or rows
        s = score(pred, gold)
        m = s["summary"]
        unresolved = sorted(c for c, (st, _) in pred.items() if st == "UNRESOLVED_EVIDENCE")
        tiers = collections.Counter(ctx.ownership[c["case_id"]].tier for c in evals if c["case_id"] not in na)
        print(f"\nX1={x1} X2={x2}: status {m['status_accuracy']:.3f} exact {round(m['case_exact'] * 32)}/32 "
              f"P {m['step_precision']:.3f} R {m['step_recall']:.3f} F1 {m['step_f1']:.3f} resource recall {m['resource_recall']:.3f}")
        print(f"   sample unresolved {unresolved}; sample inferred accounts {dict(inferred)}; "
              f"unsupported citations samples {unsupported['samples']} eval {unsupported['eval']}")
        print(f"   eval tiers {dict(tiers)}; eval unresolved {unresolved_eval}; "
              f"eval rows changed vs X1=X2=off: {sum(rows[k] != base_rows[k] for k in rows)}")
    linking.LIFECYCLE_FALLBACK, core.GAP_ACCOUNT_ABSENCE = False, False
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
