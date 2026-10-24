"""Generate output.csv for every request in dataset/deletion_requests.csv.

Usage:
  python code/main.py                 write a validated candidate to runs/<run_id>/ (repo root output.csv untouched)
  python code/main.py --promote       also copy the validated candidate to <repo>/output.csv
  python code/main.py --final         final generation run: may use the 25% daily reserve

Only evaluation requests are written; samples are read solely as linkage context. If any request
cannot be classified, nothing is written: an unclassified row is an operational failure, not a guess.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")

from llm import MODEL, Gemini, run_summary  # noqa: E402
from pipeline import DATASET, ROOT, authorize, build_context, read_rows, run  # noqa: E402
from schema import OUTPUT_COLUMNS, serialise_steps  # noqa: E402
from validate import case_number, validate  # noqa: E402


def file_hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def git_state() -> dict:
    def git(*a):
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", "--", "code"))}


def write_atomic(path: Path, rows: list[list[str]]) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(OUTPUT_COLUMNS)
        w.writerows(rows)
    os.replace(tmp, path)


def diagnostics(results, cases, ctx, auth, usage, errors) -> str:
    status = collections.Counter(r.status for r in results.values())
    steps = [len(r.steps) for r in results.values()]
    ops = collections.Counter(s.operation for r in results.values() for s in r.steps)
    deletion = [c["case_id"] for c in cases if results[c["case_id"]].status != "NOT_APPLICABLE"]
    links = collections.Counter(ctx.accounts[c].method for c in deletion)
    unknown = collections.Counter()
    no_account_step = []
    for cid in deletion:
        r = results[cid]
        for a in r.trace["resources"]:
            if a["state"] == "UNKNOWN":
                unknown[a["system_id"]] += 1
        if not any(s.system_id == "account_service" for s in r.steps):
            no_account_step.append(cid)
    cited = collections.Counter(s.evidence_id for r in results.values() for s in r.steps if s.evidence_id)
    files = {e["file_id"] for e in read_rows("evidence_files.csv")}
    na_types = collections.Counter(auth[c["case_id"]]["request_type"] for c in cases if results[c["case_id"]].status == "NOT_APPLICABLE")
    shot = ctx.screenshot_report
    lines = [
        "# Diagnostic report (candidate output, not the final submission)",
        "",
        "Development diagnostics only. Nothing here estimates hidden-test accuracy.",
        "",
        f"- rows: {len(results)}; validator errors: {len(errors)}",
        f"- status: {dict(status)}",
        f"- NOT_APPLICABLE request types: {dict(na_types)}",
        f"- steps per row: min {min(steps)}, median {sorted(steps)[len(steps) // 2]}, max {max(steps)}, total {sum(steps)}",
        f"- operations: {dict(ops)}",
        "",
        "## Primary-account linkage (deletion cases)",
        f"- {dict(links)}",
        f"- deletion cases with no account_service step: {len(no_account_step)} (first 20: {no_account_step[:20]})",
        "",
        "## Linked resources whose state could not be established (no post-request observation)",
        f"- by system: {dict(unknown)}",
        "",
        "## Evidence coverage",
        f"- distinct evidence ids cited: {len(cited)}; manifest files never cited: {len(files - set(cited))}",
        f"- most cited: {cited.most_common(10)}",
        "",
        "## Screenshots",
        f"- files: {len(shot)}; extraction ok: {sum(r.status == 'ok' for r in shot)}; "
        f"accepted records: {sum(len(r.accepted) for r in shot)}; rejected: {sum(len(r.rejected) for r in shot)}",
        "",
        "## Operational failures",
        "- request classification: 0 (generation refuses to write otherwise)",
        f"- screenshot extraction failures: {[r.file_id for r in shot if r.status != 'ok']}",
        "",
        "## API usage (this run, from the ledger)",
        f"- model: {MODEL}; {json.dumps(usage)}",
        "- actual cost: 0 (Free tier); list-price equivalent is computed in usage_report.md from the final run",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--final", action="store_true")
    args = ap.parse_args(argv)

    cases = read_rows("deletion_requests.csv")
    g = Gemini(final=args.final)
    intents, failures, auth = authorize(g, cases)
    if failures:
        print(f"{len(failures)} request(s) could not be classified; nothing written:", failures)
        return 2
    ctx = build_context(g)
    results = run(ctx, cases, intents)
    ordered = sorted(results, key=case_number)
    rows = [[cid, results[cid].status, serialise_steps(results[cid].steps)] for cid in ordered]

    out_dir = ROOT / "runs" / g.run_id
    out_dir.mkdir(parents=True)
    out = out_dir / "output.csv"
    write_atomic(out, rows)
    errors = validate(out)
    usage = run_summary(g.run_id)
    (out_dir / "diagnostics.md").write_text(diagnostics(results, cases, ctx, auth, usage, errors), encoding="utf-8")
    (out_dir / "traces.jsonl").write_text("".join(
        json.dumps({"case_id": c, "status": results[c].status, "authorisation": auth[c], **results[c].trace},
                   ensure_ascii=False, default=list) + "\n" for c in ordered), encoding="utf-8")
    manifest = {
        "run_id": g.run_id, "final": args.final, "model": MODEL, "git": git_state(),
        "inputs": {p.name: file_hash(p) for p in sorted(DATASET.glob("*.csv"))},
        "output_sha256": file_hash(out), "rows": len(rows), "validator_errors": len(errors), "usage": usage,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"candidate: {out}  rows={len(rows)}  validator_errors={len(errors)}")
    if errors:
        print("\n".join(errors[:20]))
        return 1
    if args.promote:
        tmp = ROOT / "output.csv.tmp"
        shutil.copyfile(out, tmp)
        os.replace(tmp, ROOT / "output.csv")
        print("promoted to", ROOT / "output.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
