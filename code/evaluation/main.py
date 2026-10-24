"""Development evaluation on the 32 public samples, using the same decision functions as generation.

Usage:  python code/evaluation/main.py [--intent model|oracle] [--no-status-compat] [--record-time]
                                       [--cases case_05,case_12] [--show] [--json out.json]

--intent model (default) classifies every request_text with the model, exactly as generation does.
--intent oracle takes the authorisation decision from the sample answers; it isolates the
deterministic core and is never reported as end-to-end accuracy.
--record-time is a development comparison only (strict audit_at visibility is final).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from pipeline import authorize, build_dev_context, investigate_all, read_rows, run  # noqa: E402
from schema import serialise_steps  # noqa: E402
from scoring import rows_to_map, score  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intent", choices=["model", "oracle"], default="model")
    ap.add_argument("--no-status-compat", action="store_true")
    ap.add_argument("--record-time", action="store_true", help="use records captured <= audit_at inside later files (never cited)")
    ap.add_argument("--cases", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--show", action="store_true", help="print per-case differences")
    ap.add_argument("--no-investigator", action="store_true", help="ablation: deterministic path only")
    args = ap.parse_args(argv)

    samples = read_rows("sample_deletion_requests.csv")
    gold = rows_to_map(read_rows("sample_output.csv"))
    if args.cases:
        keep = set(args.cases.split(","))
        samples = [c for c in samples if c["case_id"] in keep]
        gold = {k: v for k, v in gold.items() if k in keep}

    usage, failures, auth, g = None, {}, {}, None
    if args.intent == "oracle":
        intents = {cid: status != "NOT_APPLICABLE" for cid, (status, _) in gold.items()}
    else:
        from llm import Gemini
        g = Gemini()
        intents, failures, auth = authorize(g, samples)

    ctx = build_dev_context(g, status_compat=not args.no_status_compat, record_time=args.record_time)
    results = run(ctx, samples, intents)
    investigations = investigate_all(g, ctx, samples, results) if g is not None and not args.no_investigator else {}
    if g is not None:
        from llm import run_summary
        usage = {"run_id": g.run_id, **run_summary(g.run_id)}
    pred = {cid: (r.status, json.loads(serialise_steps(r.steps))) for cid, r in results.items()}
    report = score(pred, gold)
    print(json.dumps({"intent": args.intent, **report["summary"]}, indent=2))
    gaps = sorted(c for c, r in results.items() if r.status == "UNRESOLVED_EVIDENCE")
    print(f"unresolved evidence (scored as wrong status, no fallback applied): {len(gaps)} {gaps}")
    for cid, inv in investigations.items():
        print(f"investigator {cid}: requests {inv.get('requests')} changed {inv.get('changed_output')} "
              f"verdicts {[(v['question_id'], v['outcome'], v['answer'], v['accepted']) for v in inv.get('verdicts', [])]}")
    if failures:
        print("operational failures (not audited):", failures)
    if usage:
        print("api usage:", json.dumps(usage))
    if args.show:
        for cid, d in report["cases"].items():
            if not d["exact"]:
                print(f"\n{cid} gold={d['gold_status']} pred={d['pred_status']}")
                for m in d["missing"]:
                    print("   - missing", m)
                for e in d["extra"]:
                    print("   + extra  ", e)
                if cid in results:
                    print("     account link:", results[cid].trace.get("account_link"))
                if cid in auth:
                    print("     authorisation:", auth[cid])
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": report["summary"], "usage": usage, "failures": failures,
                                               "authorisation": auth, "cases": report["cases"],
                                               "traces": {k: v.trace for k, v in results.items()}},
                                              indent=1, default=list, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
