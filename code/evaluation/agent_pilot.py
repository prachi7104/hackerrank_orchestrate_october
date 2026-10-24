"""Investigator pilot on labelled cases, compared with the identical deterministic decision path.

Cases: ambiguous ownership (case_21, 22, 25, 26), category interpretation (case_10, 12),
multimodal (case_07) and one unlabelled genuine conflict (case_63, marketing_001).
Usage: python code/evaluation/agent_pilot.py [--dry-run] [--budget 30]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from agent import MAX_REQUESTS, apply, investigate, open_questions  # noqa: E402
from grounding import audit_result  # noqa: E402
from llm import Gemini, run_summary  # noqa: E402
from pipeline import build_context, read_rows  # noqa: E402
from core import audit_case  # noqa: E402
from schema import serialise_steps  # noqa: E402
from scoring import rows_to_map, score  # noqa: E402

PILOT = ["case_63", "case_10", "case_12", "case_25", "case_26", "case_21", "case_22", "case_07"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget", type=int, default=30)
    args = ap.parse_args()
    g = Gemini()
    ctx = build_context(g)
    cases = {c["case_id"]: c for c in read_rows("sample_deletion_requests.csv") + read_rows("deletion_requests.csv")}
    gold = rows_to_map(read_rows("sample_output.csv"))
    used, report, det_pred, agent_pred = 0, [], {}, {}
    for cid in PILOT:
        case = cases[cid]
        det = audit_case(ctx, case, True)
        qs = open_questions(ctx, case, det)
        print(f"\n== {cid}: deterministic {det.status}, {len(det.steps)} steps; questions: {[(q['id'], q['type'], q.get('resource_id') or q.get('candidates')) for q in qs]}")
        det_pred[cid] = (det.status, json.loads(serialise_steps(det.steps)))
        agent_pred[cid] = det_pred[cid]
        if args.dry_run or not qs:
            continue
        if used + MAX_REQUESTS > args.budget:
            print("   skipped: pilot budget reached")
            continue
        g.admit(MAX_REQUESTS)
        inv = investigate(g, ctx, case, det, qs)
        used += inv.requests
        res = apply(ctx, case, inv)
        agent_pred[cid] = (res.status, json.loads(serialise_steps(res.steps)))
        grounding = audit_result(ctx, case, res)
        for t in inv.tool_calls:
            print(f"   tool {t['tool']} {t.get('args')} -> {t.get('returned_units', '')}")
        for v in inv.verdicts:
            print(f"   {v['question_id']} {v['outcome']} {v['answer']!r} accepted={v['accepted']} ({v['why']}) | {v['reason'][:160]}")
        if inv.error:
            print("   error:", inv.error)
        print(f"   requests {inv.requests}; changed output: {agent_pred[cid] != det_pred[cid]}; "
              f"cited steps supported {sum(x['supported'] for x in grounding)}/{len(grounding)}")
        report.append({"case_id": cid, "questions": qs, "tool_calls": inv.tool_calls, "verdicts": inv.verdicts,
                       "requests": inv.requests, "error": inv.error, "grounding": grounding,
                       "changed": agent_pred[cid] != det_pred[cid]})
    labelled = [c for c in PILOT if c in gold]
    for name, pred in (("deterministic", det_pred), ("with investigator", agent_pred)):
        s = score({c: pred[c] for c in labelled}, {c: gold[c] for c in labelled})
        m = s["summary"]
        print(f"\n{name}: exact {sum(d['exact'] for d in s['cases'].values())}/{len(labelled)} "
              f"step P {m['step_precision']:.3f} R {m['step_recall']:.3f} F1 {m['step_f1']:.3f}")
        for c, d in s["cases"].items():
            print(f"   {c}: exact={d['exact']} missing={len(d['missing'])} extra={len(d['extra'])}")
    print("\nusage:", json.dumps(run_summary(g.run_id)))
    out = Path(__file__).resolve().parent.parent / ".cache" / f"agent_pilot_{g.run_id}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=list), encoding="utf-8")
    print("report:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
