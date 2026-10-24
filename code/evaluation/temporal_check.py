"""Experiment: does the post-request state rule hide records that genuinely persist?

Variants of core.PRE_REQUEST_STATE_CATEGORIES:
  rule      : no exemptions (current default)
  retained  : exempt categories expected to persist (retained, archived, tombstoned records)
  none      : no post-request rule at all
Scored on the 32 samples (oracle authorisation, so no API calls) and compared on the 518
evaluation rows against the rule variant. Strict audit_at visibility is unchanged in every variant.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import core  # noqa: E402
from pipeline import build_context, read_rows, run  # noqa: E402
from schema import serialise_steps  # noqa: E402
from scoring import rows_to_map, score  # noqa: E402

RETAINED = {"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD", "BACKUP_ARCHIVE", "ACCOUNT_TOMBSTONE"}
EVERY = RETAINED | set(core.SYSTEM_CATEGORY.values()) | {"SUPPORT_ATTACHMENT", "PRODUCT_TELEMETRY", "SECONDARY_IDENTIFIER"}


def main() -> int:
    samples, evals = read_rows("sample_deletion_requests.csv"), read_rows("deletion_requests.csv")
    gold = rows_to_map(read_rows("sample_output.csv"))
    cand = {r["case_id"]: (r["deletion_status"], r["steps_json"]) for r in
            read_rows_path(Path(__file__).resolve().parents[2] / "runs/20261024T154304Z-9ebac6/output.csv")}
    from llm import Gemini
    ctx = build_context(Gemini())   # screenshots from cache; same evidence as generation
    base = None
    for name, cats in (("rule", set()), ("retained", RETAINED), ("none", EVERY)):
        core.PRE_REQUEST_STATE_CATEGORIES = cats
        res = run(ctx, samples, {c["case_id"]: True for c in samples})
        s = score({k: (v.status, json.loads(serialise_steps(v.steps))) for k, v in res.items()}, gold)
        ev = run(ctx, evals, {c["case_id"]: cand[c["case_id"]][0] != "NOT_APPLICABLE" for c in evals})
        rows = {k: (v.status, serialise_steps(v.steps)) for k, v in ev.items()}
        base = base or rows
        changed = [k for k in rows if rows[k] != base[k]]
        flips = sum(rows[k][0] != base[k][0] for k in rows)
        m = s["summary"]
        print(f"{name:9s} samples exact {round(m['case_exact'] * 32)}/32 F1 {m['step_f1']:.3f} P {m['step_precision']:.3f} "
              f"R {m['step_recall']:.3f} | eval rows changed vs rule: {len(changed)} (status flips {flips}) e.g. {changed[:6]}")
        for cid, d in s["cases"].items():
            if name != "rule" and (d["missing"] or d["extra"]) and cid in ("case_07", "case_17"):
                print(f"   {cid}: extra {d['extra'][:2]}")
    core.PRE_REQUEST_STATE_CATEGORIES = set()
    return 0


def read_rows_path(p: Path):
    import csv
    return list(csv.DictReader(open(p, encoding="utf-8")))


if __name__ == "__main__":
    raise SystemExit(main())
