"""Loss decomposition on the 32 labelled samples, plus oracle-substitution ceilings.

DIAGNOSTIC ONLY. Gold identifiers are used here to *attribute* errors and to measure what a perfect
subsystem would gain; they never feed a prediction path used for evaluation or generation.

Usage: python code/evaluation/diagnose.py [--json out.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from core import audit_case  # noqa: E402
from llm import Gemini  # noqa: E402
from pipeline import build_context, investigate_all, read_rows, run  # noqa: E402
from schema import CHAINS, serialise_steps  # noqa: E402
from scoring import rows_to_map, score  # noqa: E402

HAND = {f"case_{i:02d}" for i in range(1, 11)}
OP_ACTION = {"DELETE_RECORD": "DELETE", "REDACT_RECORD": "REDACT", "ANONYMIZE_RECORD": "ANONYMIZE",
             "VERIFY_RECORD_ABSENT": "DELETE", "VERIFY_RECORD_REDACTED": "REDACT",
             "VERIFY_RECORD_ANONYMIZED": "ANONYMIZE", "VERIFY_RECORD_PRESENT": "RETAIN"}


def where(ctx, case, rid) -> str:
    """Admissibility class of a gold resource the engine did not link."""
    keys = ctx.subjects[case["case_id"]].link_keys()
    obs = ctx.by_resource.get(rid, [])
    vis = [o for o in obs if ctx.visible(o, case["audit_at"])]
    if any(o.keys() & keys for o in vis):
        return "LINKABLE_BUT_MISSED (engine bug)"
    if vis:
        return "VISIBLE_WITHOUT_SUBJECT_IDENTIFIER"
    if obs:
        return "ONLY_AFTER_AUDIT (strict time rule)"
    if any(a["resource_id"] == rid for a in ctx.activities):
        return "ACTIVITY_ONLY (workflow timing)"
    return "NOWHERE (unrecoverable)"


def classify(ctx, case, gold_steps, pred_steps, trace) -> list[tuple]:
    out = []
    pred_by_rid = collections.defaultdict(list)
    for s in pred_steps:
        pred_by_rid[s["resource_id"]].append(s)
    traced = {r["resource_id"]: r for r in trace.get("resources", [])}
    key = lambda s: (s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"], s["evidence_id"])
    pred_keys = {key(s) for s in pred_steps}
    for g in gold_steps:
        if key(g) in pred_keys:
            continue
        rid, sysid = g["resource_id"], g["system_id"]
        mine = pred_by_rid.get(rid, [])
        same_op = [s for s in mine if s["operation"] == g["operation"] and s["phase"] == g["phase"]]
        primary = sysid == "account_service"
        if same_op:
            cause = "CITATION (evidence_id differs)"
        elif mine:
            gold_act = OP_ACTION.get(g["operation"])
            mine_acts = {OP_ACTION.get(s["operation"]) for s in mine} - {None}
            cause = "POLICY/CATEGORY (different treatment)" if gold_act and mine_acts and gold_act not in mine_acts \
                else "STATE (same treatment, different phase/state)"
        elif rid in traced:
            r = traced[rid]
            cause = f"LINKED_NO_STEP (state {r['state']})"
        else:
            cause = ("PRIMARY_ACCOUNT: " if primary else "RESOURCE: ") + where(ctx, case, rid)
        out.append(("missing", cause, g["operation"], sysid, rid))
    gold_keys = {key(s) for s in gold_steps}
    gold_rids = {s["resource_id"] for s in gold_steps}
    for s in pred_steps:
        if key(s) not in gold_keys:
            cause = "EXTRA_RESOURCE" if s["resource_id"] not in gold_rids else "EXTRA_STEP_ON_GOLD_RESOURCE"
            out.append(("extra", cause, s["operation"], s["system_id"], s["resource_id"]))
    return out


def summarise(pred, gold, label):
    s = score(pred, gold)
    m = s["summary"]
    ex = {c for c, d in s["cases"].items() if d["exact"]}
    print(f"{label:38s} exact {len(ex):2d}/32 (hand {len(ex & HAND)}, gen {len(ex - HAND)})  "
          f"status {m['status_accuracy']:.3f}  P {m['step_precision']:.3f} R {m['step_recall']:.3f} "
          f"F1 {m['step_f1']:.3f}  res-recall {m['resource_recall']:.3f}")
    return ex


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    g = Gemini()
    ctx = build_context(g)
    samples = read_rows("sample_deletion_requests.csv")
    by_id = {c["case_id"]: c for c in samples}
    gold = rows_to_map(read_rows("sample_output.csv"))
    results = run(ctx, samples, {c["case_id"]: True for c in samples})
    investigate_all(g, ctx, samples, results)
    pred = {c: (r.status, json.loads(serialise_steps(r.steps))) for c, r in results.items()}

    rows, causes, per_case = [], collections.Counter(), {}
    for cid, (gst, gsteps) in gold.items():
        items = classify(ctx, by_id[cid], gsteps, pred[cid][1], results[cid].trace)
        per_case[cid] = {"gold_status": gst, "pred_status": pred[cid][0], "items": items}
        for kind, cause, *_ in items:
            causes[(kind, cause)] += 1
    print("== Ranked step-level losses (32 samples)")
    for (kind, cause), n in causes.most_common():
        print(f"  {n:3d}  {kind:7s} {cause}")
    print("\n== Per case (non-exact)")
    for cid, d in per_case.items():
        if d["items"] or d["gold_status"] != d["pred_status"]:
            print(f"  {cid} {'H' if cid in HAND else 'G'} gold {d['gold_status']:10s} pred {d['pred_status']:19s} "
                  + "; ".join(f"{k[0]}:{c.split(' (')[0]}:{rid}" for k, c, op, s, rid in d["items"]))

    print("\n== Oracle-substitution ceilings (diagnostic only)")
    base = summarise(pred, gold, "current engine")
    acct, allres = {}, {}
    for cid, (_, gsteps) in gold.items():
        gacc = [s["resource_id"] for s in gsteps if s["system_id"] == "account_service"]
        ov = {"add_links": {r: "ORACLE" for r in gacc}, "primary": gacc[0] if gacc else None}
        r = audit_case(ctx, by_id[cid], True, ov)
        acct[cid] = (r.status, json.loads(serialise_steps(r.steps)))
        ov2 = {"add_links": {s["resource_id"]: "ORACLE" for s in gsteps}, "primary": gacc[0] if gacc else None}
        r2 = audit_case(ctx, by_id[cid], True, ov2)
        allres[cid] = (r2.status, json.loads(serialise_steps(r2.steps)))
    a = summarise(acct, gold, "oracle primary account")
    b = summarise(allres, gold, "oracle full resource set")
    print(f"  exact wins with oracle account: {sorted(a - base)}; losses {sorted(base - a)}")
    print(f"  exact wins with oracle resources: {sorted(b - base)}; losses {sorted(base - b)}")
    if args.json:
        Path(args.json).write_text(json.dumps(per_case, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
