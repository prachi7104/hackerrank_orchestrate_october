"""Validate an output.csv against the released contract and the evidence time boundary.

Usage: python code/validate.py path/to/output.csv     (exit code 0 only when every check passes)
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from schema import OP_INDEX, OUTPUT_COLUMNS, STATUSES, parse_steps  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "dataset"


def case_number(cid: str) -> int:
    return int(cid.split("_")[1])


def evidence_times() -> dict[str, str]:
    """Every citable id -> its capture/event time."""
    t = {r["file_id"]: r["captured_at"] for r in csv.DictReader(open(DATASET / "evidence_files.csv", encoding="utf-8"))}
    t.update({r["activity_id"]: r["event_time"] for r in csv.DictReader(open(DATASET / "system_activity.csv", encoding="utf-8"))})
    t.update({r["audit_id"]: r["event_time"] for r in csv.DictReader(open(DATASET / "user_audit_log.csv", encoding="utf-8"))})
    return t


def validate(path: Path) -> dict[str, list[str]]:
    """Two separate reports.
    structural: the file can be parsed and graded (header, ids, order, schema, enums, numbering,
                evidence id existence, audit_at boundary, citation rules for future steps).
    semantic:   the row is a complete audit answer under the contract (COMPLETE carries cited
                verifications, INCOMPLETE carries findings with their remediation and checks).
    Grounding of each citation needs the evidence table and is checked in code/main.py."""
    structural, semantic = [], []
    requests = {r["case_id"]: r for r in csv.DictReader(open(DATASET / "deletion_requests.csv", encoding="utf-8"))}
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    if header != OUTPUT_COLUMNS:
        structural.append(f"header {header} != {OUTPUT_COLUMNS}")
    ids = [r[0] for r in rows]
    if sorted(ids) != sorted(requests) or len(ids) != len(set(ids)):
        structural.append(f"case ids differ from deletion_requests.csv ({len(ids)} rows, {len(set(ids))} unique)")
    if ids != sorted(ids, key=case_number):
        structural.append("rows are not sorted by case number")
    times = evidence_times()
    for row in rows:
        if len(row) != 3:
            structural.append(f"{row[:1]}: {len(row)} columns")
            continue
        cid, status, raw = row
        audit_at = requests.get(cid, {}).get("audit_at", "")
        try:
            steps = parse_steps(raw)
        except ValueError as e:
            structural.append(f"{cid}: {e}")
            continue
        if status not in STATUSES:
            structural.append(f"{cid}: status {status}")
        phases = [s["phase"] for s in steps]
        keys = [(OP_INDEX[s["operation"]], s["system_id"], s["resource_id"]) for s in steps]
        if keys != sorted(keys):
            structural.append(f"{cid}: steps not in phase/operation/system/resource order")
        if status == "NOT_APPLICABLE" and steps:
            structural.append(f"{cid}: NOT_APPLICABLE with steps")
        for s in steps:
            cited = s["evidence_id"]
            needs = s["phase"] == "FINDING" or status == "COMPLETE"
            if needs and not cited:
                semantic.append(f"{cid}: step {s['step_no']} lacks evidence")
            if not needs and cited:
                structural.append(f"{cid}: step {s['step_no']} is future work but cites {cited}")
            if cited and cited not in times:
                structural.append(f"{cid}: unknown evidence id {cited}")
            elif cited and times[cited] > audit_at:
                structural.append(f"{cid}: {cited} captured {times[cited]} after audit_at {audit_at}")
        if status == "COMPLETE" and (not steps or set(phases) != {"VERIFICATION"}):
            semantic.append(f"{cid}: COMPLETE must contain cited verification steps only")
        if status == "INCOMPLETE" and "FINDING" not in phases:
            semantic.append(f"{cid}: INCOMPLETE without findings")
        if status == "INCOMPLETE":   # every finding needs its remediation and post-remediation check
            found = {(s["system_id"], s["resource_id"]) for s in steps if s["phase"] == "FINDING"}
            fixed = {(s["system_id"], s["resource_id"]) for s in steps if s["phase"] == "REMEDIATION"}
            checked = {(s["system_id"], s["resource_id"]) for s in steps if s["phase"] == "VERIFICATION"}
            if not found <= fixed or not fixed <= checked:
                semantic.append(f"{cid}: finding without remediation or verification")
    return {"structural": structural, "semantic": semantic}


if __name__ == "__main__":
    rep = validate(Path(sys.argv[1]))
    for kind in ("structural", "semantic"):
        print(f"{kind}: {len(rep[kind])} error(s)")
        for e in rep[kind][:30]:
            print("  " + e)
    raise SystemExit(1 if rep["structural"] else 0)
