"""Compare primary-account ownership policies A-D (see core.ACCOUNT_POLICY) on every judged field.

Samples use the answer-key authorisation (all 32 authorise deletion) so the comparison isolates
ownership; screenshots come from the cache; no new model requests are made. Evaluation rows use the
authorisation of the committed candidate (runs/20261024T154304Z-9ebac6).
Usage: python code/evaluation/ownership_variants.py
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
from grounding import audit_result  # noqa: E402
from llm import Gemini  # noqa: E402
from pipeline import ROOT, build_context, read_rows  # noqa: E402
from schema import serialise_steps  # noqa: E402
from scoring import rows_to_map, score  # noqa: E402

HAND = {f"case_{i:02d}" for i in range(1, 11)}
CANDIDATE = ROOT / "runs/20261024T154304Z-9ebac6/output.csv"


def accounts(steps) -> set:
    return {s["resource_id"] for s in steps if s["system_id"] == "account_service"}


def main() -> int:
    ctx = build_context(Gemini())
    samples, evals = read_rows("sample_deletion_requests.csv"), read_rows("deletion_requests.csv")
    gold = rows_to_map(read_rows("sample_output.csv"))
    na = {r["case_id"] for r in csv.DictReader(open(CANDIDATE, encoding="utf-8")) if r["deletion_status"] == "NOT_APPLICABLE"}
    eval_rows = {}
    for policy in "ABCD":
        core.ACCOUNT_POLICY = policy
        pred, ground = {}, collections.Counter()
        for c in samples:
            r = core.audit_case(ctx, c, True)
            pred[c["case_id"]] = (r.status, json.loads(serialise_steps(r.steps)))
            for a in audit_result(ctx, c, r):
                ground[a["supported"]] += 1
        rows, eground = {}, collections.Counter()
        for c in evals:
            r = core.audit_case(ctx, c, c["case_id"] not in na)
            rows[c["case_id"]] = serialise_steps(r.steps) + r.status
            if r.status != "NOT_APPLICABLE":
                for a in audit_result(ctx, c, r):
                    eground[a["supported"]] += 1
        eval_rows[policy] = rows
        acc = collections.Counter()
        missing_acct = 0
        for cid, (_, g) in gold.items():
            want, got = accounts(g), accounts(pred[cid][1])
            missing_acct += len(want - got)
            acc["correct" if got and got <= want else "incorrect" if got else "unresolved"] += 1
        print(f"\n=== policy {policy}")
        for label, ids in (("all", set(gold)), ("handcrafted", HAND), ("generated", set(gold) - HAND)):
            s = score({c: pred[c] for c in ids}, {c: gold[c] for c in ids})
            m = s["summary"]
            ex = sum(d["exact"] for d in s["cases"].values())
            print(f"  {label:11s} exact {ex}/{len(ids)}  P {m['step_precision']:.3f} R {m['step_recall']:.3f} "
                  f"F1 {m['step_f1']:.3f}  resource recall {m['resource_recall']:.3f}")
        print(f"  primary-account verifications missing: {missing_acct}; account assignments {dict(acc)}")
        print(f"  grounding: samples supported {ground[True]} unsupported {ground[False]}; "
              f"evaluation supported {eground[True]} unsupported {eground[False]}")
        if policy != "A":
            changed = [k for k in rows if rows[k] != eval_rows["A"][k]]
            print(f"  evaluation rows changed vs A: {len(changed)} of {len(rows) - len(na)} deletion cases")
    core.ACCOUNT_POLICY = "D"
    tiers = collections.Counter(ctx.ownership[c["case_id"]].tier for c in evals if c["case_id"] not in na)
    why = collections.Counter(ctx.ownership[c["case_id"]].basis for c in evals
                              if c["case_id"] not in na and ctx.ownership[c["case_id"]].tier == "UNRESOLVED")
    print("\nD tiers (evaluation deletion cases):", dict(tiers), "| unresolved because:", dict(why))
    print("D tiers (samples):", dict(collections.Counter(ctx.ownership[c].tier for c in gold)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
