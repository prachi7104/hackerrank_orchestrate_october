"""Route evaluation cases with no provable resource to the bounded investigator.

Usage: python code/evaluation/agent_blocked.py [--dry-run] [--budget 12] [--cases case_55,case_63]
Authorisation for these cases comes from the cached classifier (both authorise deletion).
No answer key exists for evaluation rows; outcomes are judged only by code verification.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from agent import MAX_REQUESTS, apply, investigate, open_questions  # noqa: E402
from core import audit_case  # noqa: E402
from grounding import audit_result  # noqa: E402
from llm import Gemini, run_summary  # noqa: E402
from pipeline import authorize, build_context, read_rows  # noqa: E402
from schema import serialise_steps  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget", type=int, default=12)
    ap.add_argument("--cases", default="case_55,case_63")
    args = ap.parse_args()
    g = Gemini()
    ctx = build_context(g)
    rows = {c["case_id"]: c for c in read_rows("deletion_requests.csv")}
    cases = [rows[c] for c in args.cases.split(",")]
    intents, failures, _ = authorize(g, cases)
    used, report = 0, []
    for case in cases:
        cid = case["case_id"]
        det = audit_case(ctx, case, intents[cid])
        qs = open_questions(ctx, case, det)
        print(f"\n== {cid}: deterministic {det.status} {serialise_steps(det.steps)}")
        for q in qs:
            print(f"   {q['id']} {q['type']}: {q['question'][:220]}")
        if args.dry_run:
            continue
        if used + MAX_REQUESTS + 2 > args.budget:
            print("   skipped: budget")
            continue
        g.admit(MAX_REQUESTS + 2)
        inv = investigate(g, ctx, case, det, qs)
        used += inv.requests
        res = apply(ctx, case, inv)
        for t in inv.tool_calls:
            print(f"   tool {t['tool']} {t.get('args')} -> {t.get('returned_units', '')}")
        for v in inv.verdicts:
            print(f"   {v['question_id']} {v['outcome']} {v['answer']!r} accepted={v['accepted']} ({v['why']})\n"
                  f"      reason: {v['reason'][:300]}\n      units: {v['supporting_units']}")
        if inv.error:
            print("   error:", inv.error)
        print(f"   after verified overrides: {res.status} {serialise_steps(res.steps)}")
        print(f"   grounding: {audit_result(ctx, case, res)}")
        report.append({"case_id": cid, "questions": qs, "tool_calls": inv.tool_calls, "verdicts": inv.verdicts,
                       "requests": inv.requests, "error": inv.error, "status_after": res.status,
                       "steps_after": serialise_steps(res.steps)})
    print("\nusage:", json.dumps(run_summary(g.run_id)))
    out = Path(__file__).resolve().parent.parent / ".cache" / f"agent_blocked_{g.run_id}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=list), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
