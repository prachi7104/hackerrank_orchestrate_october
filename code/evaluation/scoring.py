"""Score predicted rows against reference rows on every judged field, including the full step sequence.

Metrics (all micro-averaged over cases unless stated):
  status_accuracy      deletion_status exact match
  case_exact           status and the whole steps array identical (order included)
  step P/R/F1          multiset match on the full step tuple (phase, system, resource, operation, state, evidence)
  step_core P/R/F1     same, ignoring evidence_id (separates discovery from citation errors)
  field accuracy       over steps aligned on (phase, system_id, resource_id, operation): state and evidence_id
  resource P/R         over (system_id, resource_id) pairs touched by the case
  sequence             exact order match of the aligned core tuples, and normalised edit similarity
"""
from __future__ import annotations

import json
from collections import Counter


def full(s):
    return (s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"], s["evidence_id"])


def core(s):
    return (s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"])


def edit_similarity(a: list, b: list) -> float:
    if not a and not b:
        return 1.0
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return 1 - prev[-1] / max(len(a), len(b))


def prf(tp, n_pred, n_gold):
    p = tp / n_pred if n_pred else 1.0
    r = tp / n_gold if n_gold else 1.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def score(pred: dict, gold: dict) -> dict:
    """pred/gold: case_id -> (status, steps list of dicts)."""
    agg = Counter()
    per_case = {}
    for cid, (g_status, g_steps) in gold.items():
        p_status, p_steps = pred.get(cid, ("MISSING", []))
        cf = Counter(map(full, p_steps)), Counter(map(full, g_steps))
        cc = Counter(map(core, p_steps)), Counter(map(core, g_steps))
        tp_full = sum((cf[0] & cf[1]).values())
        tp_core = sum((cc[0] & cc[1]).values())
        g_by_key = {s_[:4]: s for s, s_ in ((s, core(s)) for s in g_steps)}
        aligned = [(s, g_by_key[core(s)[:4]]) for s in p_steps if core(s)[:4] in g_by_key]
        pr = {(s["system_id"], s["resource_id"]) for s in p_steps}
        gr = {(s["system_id"], s["resource_id"]) for s in g_steps}
        seq_p = [core(s) for s in p_steps if core(s) in cc[1]]
        seq_g = [core(s) for s in g_steps if core(s) in cc[0]]
        exact = p_status == g_status and list(map(full, p_steps)) == list(map(full, g_steps))
        agg.update({
            "cases": 1, "status_ok": p_status == g_status, "exact": exact,
            "tp_full": tp_full, "tp_core": tp_core, "n_pred": len(p_steps), "n_gold": len(g_steps),
            "aligned": len(aligned), "state_ok": sum(a["state"] == b["state"] for a, b in aligned),
            "evid_ok": sum(a["evidence_id"] == b["evidence_id"] for a, b in aligned),
            "res_tp": len(pr & gr), "res_pred": len(pr), "res_gold": len(gr),
            "order_ok": seq_p == seq_g,
        })
        agg["edit_sim"] += edit_similarity(list(map(full, p_steps)), list(map(full, g_steps)))
        per_case[cid] = {
            "gold_status": g_status, "pred_status": p_status, "exact": exact,
            "missing": [list(t) for t in (cf[1] - cf[0]).elements()],
            "extra": [list(t) for t in (cf[0] - cf[1]).elements()],
        }
    n = agg["cases"] or 1
    p, r, f = prf(agg["tp_full"], agg["n_pred"], agg["n_gold"])
    pc, rc, fc = prf(agg["tp_core"], agg["n_pred"], agg["n_gold"])
    rp, rr, rf = prf(agg["res_tp"], agg["res_pred"], agg["res_gold"])
    summary = {
        "cases": agg["cases"],
        "status_accuracy": agg["status_ok"] / n,
        "case_exact": agg["exact"] / n,
        "step_precision": p, "step_recall": r, "step_f1": f,
        "step_core_precision": pc, "step_core_recall": rc, "step_core_f1": fc,
        "field_state_accuracy": agg["state_ok"] / agg["aligned"] if agg["aligned"] else 0.0,
        "field_evidence_accuracy": agg["evid_ok"] / agg["aligned"] if agg["aligned"] else 0.0,
        "resource_precision": rp, "resource_recall": rr, "resource_f1": rf,
        "sequence_order_accuracy": agg["order_ok"] / n,
        "sequence_edit_similarity": agg["edit_sim"] / n,
    }
    return {"summary": summary, "cases": per_case}


def rows_to_map(rows) -> dict:
    return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in rows}
