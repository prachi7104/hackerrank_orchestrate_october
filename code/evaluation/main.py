"""Development evaluation on the 32 public samples, using the same decision function as generation.

Usage:  python code/evaluation/main.py [--intent oracle] [--no-status-compat] [--cases case_05,case_12] [--json out.json]

--intent oracle takes the authorise/not-authorise decision from the sample answers (all 32 samples
authorise deletion). It isolates the deterministic core and is reported as such, never as
end-to-end accuracy.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import build_dev_context, read_rows, run  # noqa: E402
from scoring import rows_to_map, score  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intent", choices=["oracle"], default="oracle")
    ap.add_argument("--no-status-compat", action="store_true")
    ap.add_argument("--record-time", action="store_true", help="use records captured <= audit_at inside later files (never cited)")
    ap.add_argument("--cases", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--show", action="store_true", help="print per-case differences")
    args = ap.parse_args(argv)

    samples = read_rows("sample_deletion_requests.csv")
    gold = rows_to_map(read_rows("sample_output.csv"))
    if args.cases:
        keep = set(args.cases.split(","))
        samples = [c for c in samples if c["case_id"] in keep]
        gold = {k: v for k, v in gold.items() if k in keep}
    intents = {cid: status != "NOT_APPLICABLE" for cid, (status, _) in gold.items()}

    ctx = build_dev_context(status_compat=not args.no_status_compat, record_time=args.record_time)
    results = run(ctx, samples, intents)
    pred = {cid: (r.status, [json.loads(s) for s in [_dump(r)]][0]) for cid, r in results.items()}
    report = score(pred, gold)
    print(json.dumps(report["summary"], indent=2))
    if args.show:
        for cid, d in report["cases"].items():
            if not d["exact"]:
                print(f"\n{cid} gold={d['gold_status']} pred={d['pred_status']}")
                for m in d["missing"]:
                    print("   - missing", m)
                for e in d["extra"]:
                    print("   + extra  ", e)
                acct = results[cid].trace.get("account_link")
                print("     account link:", acct)
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": report["summary"], "cases": report["cases"],
                                               "traces": {k: v.trace for k, v in results.items()}}, indent=1, default=list))
    return 0


def _dump(r):
    from schema import serialise_steps
    return serialise_steps(r.steps)


if __name__ == "__main__":
    raise SystemExit(main())
