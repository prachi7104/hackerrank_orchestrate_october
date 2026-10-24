"""Check screenshot extraction against a hand-made reference.

Oracle: resource ids (after catalog alias resolution) and identifier links that the developer
read off each of the 11 screenshots on 24 Oct (Claude Code viewing the images, reviewed in the
transcript). Limitation: one reader, ids and identity links only, not every field on screen.

Usage: python code/evaluation/screenshot_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from llm import Gemini, run_summary  # noqa: E402
from pipeline import DATASET, read_rows, screenshot_observations  # noqa: E402

# file_id -> {resource_id: identifier expected on that record ("" when the screen shows none)}
REFERENCE = {
    "evidence_04": {"account_5762": "haruka.s1838@zoho.com"},
    "evidence_09": {"search_428": "g.silva76@gmail.com", "search_1874": "g.silva76.jr@gmail.com"},
    "evidence_14": {"data_flow_1121": "jain.aditi@gmail.com", "data_flow_9846": "lucia-torres@gmail.com"},
    "evidence_19": {"analytics_7438": "darwish.omar@icloud.com"},
    "evidence_23": {"export_1260": "ingrid.dahl@outlook.com", "export_1261": "marcus.lee@northstar.shop",
                    "export_1259": "sara.kim@northstar.shop", "export_1258": "daniel.park@northstar.shop"},
    "evidence_28": {"support_1034": "kagyeman8947@fastmail.com"},
    "evidence_32": {"backup_9287": "areed1433@gmail.com", "backup_9291": "bluepeakco@outlook.com",
                    "backup_9288": "miller.logistics@company.com", "backup_9286": "summit-retail@company.com"},
    "evidence_37": {f"audit_record_00{i}": "" for i in range(1, 7)},
    "evidence_42": {"tombstone_17": ""},
    "evidence_46": {"object_19": ""},
    "evidence_60": {"marketing_001": "sj.park@naver.com"},
}


def main() -> int:
    g = Gemini()
    obs, report = screenshot_observations(g)
    got: dict[str, dict] = {}
    for o in obs:
        got.setdefault(o.file_id, {})[o.resource_id] = o.content.get("email", "")
    tp = fp = fn = id_ok = id_bad = 0
    for fid, expected in REFERENCE.items():
        found = got.get(fid, {})
        tp += len(set(found) & set(expected))
        fp += len(set(found) - set(expected))
        fn += len(set(expected) - set(found))
        for rid in set(found) & set(expected):
            if expected[rid]:
                ok = found[rid].lower() == expected[rid]
                id_ok += ok
                id_bad += not ok
        extra, missed = sorted(set(found) - set(expected)), sorted(set(expected) - set(found))
        print(f"{fid}: found {sorted(found)}" + (f"  EXTRA {extra}" if extra else "") + (f"  MISSED {missed}" if missed else ""))
    for r in report:
        for rej in r.rejected:
            print(f"  rejected in {r.file_id}: {rej['reason']}")
        if r.status != "ok":
            print(f"  FAILED {r.file_id}: {r.error}")
    print(f"\nresource ids: {tp} correct, {fp} unexpected, {fn} missed; identifier on matched records: {id_ok} correct, {id_bad} wrong")
    print("usage:", json.dumps(run_summary(g.run_id)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
