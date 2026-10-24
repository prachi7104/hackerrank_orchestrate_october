"""Controlled experiments on the deterministic path (no model requests): every variant is scored on
all judged fields against the same baseline, with identity, grounding and evaluation-set impact.

Usage: python code/evaluation/experiment.py
"""
from __future__ import annotations

import collections
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import core  # noqa: E402
import evidence  # noqa: E402
import linking  # noqa: E402
from grounding import audit_result  # noqa: E402
from llm import Gemini  # noqa: E402
from pipeline import ROOT, build_context, read_rows  # noqa: E402
from schema import serialise_steps  # noqa: E402
from scoring import rows_to_map, score  # noqa: E402

HAND = {f"case_{i:02d}" for i in range(1, 11)}
FINAL = ROOT.parent / "hackerrank-orchestrate-october26" / "runs/20261024T175408Z-427c28/output.csv"  # frozen release (main checkout)
VARIANTS = {   # first entry is the comparison baseline
    "current (C1 + P1)": {"SECONDARY_ID_RULE": True, "PDF_SUBJECT_SCOPE": True},
    "release 2 (C1, P1 off)": {"SECONDARY_ID_RULE": True, "PDF_SUBJECT_SCOPE": False},
}
LINK_FLAGS = {"PROPAGATE", "STATUS_FILTER", "ORDINAL", "ORDINAL_SAME_STREAM"}


def set_flags(flags):
    linking.PROPAGATE = flags.get("PROPAGATE", False)
    linking.STATUS_FILTER = flags.get("STATUS_FILTER", False)
    linking.ORDINAL = flags.get("ORDINAL", False)
    linking.ORDINAL_SAME_STREAM = flags.get("ORDINAL_SAME_STREAM", False)
    core.CITE_INFERRED = flags.get("CITE_INFERRED", False)
    core.SECONDARY_ID_RULE = flags.get("SECONDARY_ID_RULE", False)
    core.RETAINED_FUTURE_CHECK = flags.get("RETAINED_FUTURE_CHECK", False)
    core.ACCOUNT_FUTURE_CHECK = flags.get("ACCOUNT_FUTURE_CHECK", False)
    core.BATCH_ATTRIBUTION = flags.get("BATCH_ATTRIBUTION", "")
    evidence.PDF_SUBJECT_SCOPE = flags.get("PDF_SUBJECT_SCOPE", True)


def identity(ctx, gold):
    c = collections.Counter()
    for cid, (_, steps) in gold.items():
        want = {s["resource_id"] for s in steps if s["system_id"] == "account_service"}
        o = ctx.ownership[cid]
        if not o.resource_id:
            c["unresolved"] += 1
        else:
            kind = "" if o.tier == "DIRECT" else "_ordinal" if o.basis.startswith("ordinal") else "_inferred"
            c[("correct" if o.resource_id in want else "wrong") + kind] += 1
    return dict(c)


def main() -> int:
    g = Gemini()
    samples, evals = read_rows("sample_deletion_requests.csv"), read_rows("deletion_requests.csv")
    gold = rows_to_map(read_rows("sample_output.csv"))
    na = {r["case_id"] for r in csv.DictReader(open(FINAL, encoding="utf-8")) if r["deletion_status"] == "NOT_APPLICABLE"}
    base_exact, base_eval, ctx = None, None, None
    for name, flags in VARIANTS.items():
        set_flags(flags)
        ctx = build_context(g)          # rebuilt each time: ownership is computed at context build
        pred, unsupported = {}, collections.Counter()
        for c in samples:
            r = core.audit_case(ctx, c, True)
            pred[c["case_id"]] = (r.status, json.loads(serialise_steps(r.steps)))
            unsupported["samples"] += sum(not a["supported"] for a in audit_result(ctx, c, r))
        ev = {}
        for c in evals:
            r = core.audit_case(ctx, c, c["case_id"] not in na)
            ev[c["case_id"]] = r.status + serialise_steps(r.steps)
            if r.status in ("COMPLETE", "INCOMPLETE"):
                unsupported["eval"] += sum(not a["supported"] for a in audit_result(ctx, c, r))
        s = score(pred, gold)
        m = s["summary"]
        exact = {c for c, d in s["cases"].items() if d["exact"]}
        base_exact = base_exact if base_exact is not None else exact
        base_eval = base_eval or ev
        tiers = collections.Counter(ctx.ownership[c["case_id"]].tier for c in evals if c["case_id"] not in na)
        print(f"\n== {name}")
        print(f"   exact {len(exact)}/32 (hand {len(exact & HAND)}, gen {len(exact - HAND)})  status {m['status_accuracy']:.3f}  "
              f"P {m['step_precision']:.3f} R {m['step_recall']:.3f} F1 {m['step_f1']:.3f}  res-recall {m['resource_recall']:.3f}")
        print(f"   exact wins {sorted(exact - base_exact)} losses {sorted(base_exact - exact)}; identity {identity(ctx, gold)}")
        print(f"   unsupported citations samples {unsupported['samples']} eval {unsupported['eval']}; "
              f"eval tiers {dict(tiers)}; eval rows changed vs baseline {sum(ev[k] != base_eval[k] for k in ev)}")
        if flags.keys() & LINK_FLAGS:   # metamorphic: ownership must not change when statuses are shuffled
            rng = random.Random(11)
            st = [c["reported_status"] for c in samples + evals]
            rng.shuffle(st)
            shuffled = [{**c, "reported_status": x} for c, x in zip(samples + evals, st)]
            direct = {c["case_id"]: {r for r in ctx.linked(c, ctx.subjects[c["case_id"]], since=c["requested_at"])
                                     if r.startswith("account_")} for c in shuffled}
            o2 = linking.conditional_ownership(shuffled, direct, ctx.activities, ctx.owned_by_other,
                                               lambda c: set(ctx.linked(c, ctx.subjects[c["case_id"]])))
            diff = sum((o2[k].tier, o2[k].resource_id) != (ctx.ownership[k].tier, ctx.ownership[k].resource_id) for k in o2)
            print(f"   metamorphic (shuffled reported_status): ownership changed for {diff} cases")
    set_flags({})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
