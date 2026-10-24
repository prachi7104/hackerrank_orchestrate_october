"""Generate usage_report.md for one run from the append-only ledger.

Usage: python code/evaluation/usage.py <run_id> [--requests 518] [--out code/evaluation/usage_report.md]

The run's own provider requests are reported separately from responses it reused from the cache;
reused responses are attributed to the run that originally paid for them, with those tokens.
Missing provider token fields are reported as unknown, never as zero.
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import LEDGER, MODEL  # noqa: E402

# Paid-tier list prices for comparison only (ai.google.dev/gemini-api/docs/pricing, read 24 Oct 2026).
PRICE_PER_M = {"gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50}}   # output price includes thinking
FIELDS = ("input", "output", "thinking", "total")


def load() -> list[dict]:
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines()]


def tally(entries) -> dict:
    t = {"requests": 0, "failed": 0, "unknown": collections.Counter(), **{f: 0 for f in FIELDS}}
    for e in entries:
        t["requests"] += 1
        t["failed"] += bool(e.get("error"))
        for f in FIELDS:
            v = (e.get("usage") or {}).get(f)
            if v is None:
                t["unknown"][f] += 1
            else:
                t[f] += v
    return t


def cost(t: dict, model: str) -> float:
    p = PRICE_PER_M.get(model, {"input": 0, "output": 0})
    return (t["input"] * p["input"] + (t["output"] + t["thinking"]) * p["output"]) / 1e6


def row(name, t, model, n):
    unk = ", ".join(f"{k} {v}" for k, v in t["unknown"].items() if k != "thinking") or "none"
    return (f"| {name} | {t['requests']} | {t['failed']} | {t['input']:,} | {t['output']:,} | {t['thinking']:,} | "
            f"{t['total']:,} | {t['total'] / n:,.1f} | ${cost(t, model):.4f} | {unk} |")


def report(run_id: str, n_requests: int) -> str:
    entries = load()
    mine = [e for e in entries if e.get("run_id") == run_id]
    attempts = [e for e in mine if e["kind"] == "attempt"]
    hits = [e for e in mine if e["kind"] == "cache_hit"]
    first_success = {}
    for e in entries:
        if e["kind"] == "attempt" and not e.get("error"):
            first_success.setdefault(e["cache_key"], e)
    reused = [first_success[h["cache_key"]] for h in hits if h["cache_key"] in first_success]
    missing = [h for h in hits if h["cache_key"] not in first_success]
    models = sorted({e.get("model") for e in attempts + reused} - {None}) or [MODEL]
    returned = sorted({e.get("returned_model") for e in attempts + reused} - {None})
    head = "| Scope | Requests | Failed | Input tokens | Output tokens | Thinking tokens | Total tokens | Total per request | List-price equivalent | Unknown token fields |\n|---|---|---|---|---|---|---|---|---|---|"
    lines = [
        "# Usage report",
        "",
        f"Generated from `code/.cache/ledger.jsonl` for run `{run_id}`, the run that produced the submitted "
        f"`output.csv` ({n_requests} requests in `dataset/deletion_requests.csv`).",
        "",
        "- Provider: Google Gemini Developer API, free tier (no billing account, no paid features).",
        f"- Model requested: {', '.join(models)}; model versions returned: {', '.join(returned) or 'n/a'}.",
        "- Actual cost: **₹0**. The list-price column is a hypothetical paid-tier equivalent "
        "(input $0.30 / 1M, output and thinking $2.50 / 1M tokens) for transparency only.",
        "- Token counts are the provider's `usageMetadata` (prompt, candidates, thoughts, total). "
        "The provider's total already includes thinking tokens.",
        "",
        "## Requests sent to the provider by this run",
        "",
        head,
    ]
    by_purpose = collections.defaultdict(list)
    for e in attempts:
        by_purpose[(e.get("model"), e["purpose"])].append(e)
    for (model, purpose), es in sorted(by_purpose.items()):
        lines.append(row(f"{model} · {purpose}", tally(es), model, n_requests))
    lines.append(row("**all models, this run**", tally(attempts), models[0], n_requests))
    lines += [
        "",
        "## Responses reused from the cache",
        "",
        f"This run reused {len(hits)} cached responses (identical model, prompt, schema, configuration and input). "
        "They cost nothing in this run; the table attributes them to the runs that originally made the requests.",
        "",
        head,
    ]
    by_origin = collections.defaultdict(list)
    for e in reused:
        by_origin[(e.get("model"), e["purpose"], e["run_id"])].append(e)
    for (model, purpose, origin), es in sorted(by_origin.items()):
        lines.append(row(f"{model} · {purpose} · from run {origin}", tally(es), model, n_requests))
    lines.append(row("**all reused responses**", tally(reused), models[0], n_requests))
    if missing:
        lines.append(f"\n{len(missing)} cache hits have no originating request in the ledger (reported, not counted).")
    both = tally(attempts + reused)
    lines += [
        "",
        "## Overall: tokens behind the submitted output",
        "",
        head,
        row("**this run + reused responses**", both, models[0], n_requests),
        "",
        f"Average per evaluation request: {both['total'] / n_requests:,.1f} tokens, "
        f"{(both['requests']) / n_requests:.3f} provider requests; list-price equivalent "
        f"${cost(both, models[0]) / n_requests:.6f} per request, actual ₹0.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--requests", type=int, default=518)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "usage_report.md"))
    args = ap.parse_args()
    Path(args.out).write_text(report(args.run_id, args.requests), encoding="utf-8")
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
