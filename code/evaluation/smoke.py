"""Minimal provider capability checks (run once, results recorded in the ledger and printed).

  1. structured classification of synthetic multilingual / negated requests (expected labels known by construction)
  2. vision extraction from one real screenshot (account_admin_console_20260926.png)
  3. function calling with two read-only evidence tools over the real local index

Usage: python code/evaluation/smoke.py [--only 1,2,3]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from llm import API, MODEL, Gemini, api_key, image_part  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
NO_CACHE = dict(use_cache=False)   # smoke tests must reach the provider


def check_model(g: Gemini) -> None:
    started = time.monotonic()
    req = urllib.request.Request(f"{API}/models/{MODEL}", headers={"x-goog-api-key": api_key()})
    with urllib.request.urlopen(req, timeout=30) as r:
        meta = json.loads(r.read())
    g.log({"kind": "metadata_request", "purpose": "smoke:model_get", "model": MODEL, "status": 200,
           "latency_s": round(time.monotonic() - started, 3)})
    print("model metadata:", {k: meta.get(k) for k in ("name", "version", "inputTokenLimit", "outputTokenLimit",
                                                        "supportedGenerationMethods", "thinking")})


SMOKE_TEXTS = {  # synthetic, written for this test; expected labels by construction
    "t1": ("このアカウントと関連する個人データをすべて削除してください。", True),
    "t2": ("Please do NOT delete my account. I only want a copy of my data exported to me.", False),
    "t3": ("Quiero dejar de recibir correos promocionales, pero mantengan mi cuenta activa.", False),
}
SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "request_type": {"type": "string", "enum": ["DELETION", "ACCESS", "EXPORT", "CORRECTION",
                                                        "MARKETING_OPT_OUT", "ACCOUNT_SUPPORT", "OTHER"]},
            "authorizes_deletion": {"type": "boolean"},
            "evidence_quote": {"type": "string"},
        },
        "required": ["id", "request_type", "authorizes_deletion", "evidence_quote"]}}},
    "required": ["items"],
}


def smoke_classification(g: Gemini) -> None:
    prompt = "Classify each request. evidence_quote must be copied verbatim from the request text.\n" + \
        "\n".join(f"[{i}] {t}" for i, (t, _) in SMOKE_TEXTS.items())
    r = g.generate("smoke:classification", list(SMOKE_TEXTS), [{"role": "user", "parts": [{"text": prompt}]}],
                   system="You decide whether a customer request authorises deletion of their account data.",
                   prompt_version="smoke-1", schema=SCHEMA, config={"thinkingConfig": {"thinkingLevel": "minimal"}},
                   **NO_CACHE)
    out = json.loads(r.text)["items"]
    for o in out:
        text, expected = SMOKE_TEXTS[o["id"]]
        print(f"  {o['id']}: type={o['request_type']} authorises={o['authorizes_deletion']} expected={expected} "
              f"quote_verbatim={o['evidence_quote'] in text}")
    print("  ids exact:", sorted(o["id"] for o in out) == sorted(SMOKE_TEXTS), "usage:", {k: v for k, v in r.usage.items() if k != "raw"})


VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "screen": {"type": "string"},
        "records": {"type": "array", "items": {"type": "object", "properties": {
            "resource_id": {"type": "string"}, "user_id": {"type": "string"},
            "status_shown": {"type": "string"}, "name": {"type": "string"},
            "email": {"type": "string"}, "phone": {"type": "string"},
            "related_resource_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["resource_id"]}},
        "captured_or_shown_dates": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["screen", "records"],
}


def smoke_vision(g: Gemini) -> None:
    img = ROOT / "dataset/evidence/images/account_admin_console_20260926.png"
    started = time.monotonic()
    r = g.generate("smoke:vision", ["evidence_04"], [{"role": "user", "parts": [
        image_part(img),
        {"text": "Extract every record identifier and the personal identifiers visible on this internal screen. "
                 "Copy identifiers exactly as shown; use an empty string when a field is not visible."}]}],
        prompt_version="smoke-1", schema=VISION_SCHEMA, config={"thinkingConfig": {"thinkingLevel": "minimal"}},
        **NO_CACHE)
    out = json.loads(r.text)
    print("  extracted:", json.dumps(out, ensure_ascii=False)[:600])
    flat = json.dumps(out)
    for expected in ("account_5762", "user_11", "haruka.s1838@zoho.com", "support_9538"):
        print(f"  contains {expected}: {expected in flat}")
    print(f"  latency {time.monotonic() - started:.1f}s usage:", {k: v for k, v in r.usage.items() if k != "raw"})


def smoke_tools(g: Gemini) -> None:
    from pipeline import build_context
    ctx = build_context()
    from evidence import norm

    def find_evidence(identifier: str) -> dict:
        hits = ctx.by_key.get(norm(identifier), [])
        return {"observations": [{"file_id": o.file_id, "locator": o.locator, "resource_id": o.resource_id,
                                  "observed_at": o.observed_at, "file_captured_at": o.captured_at} for o in hits[:10]]}

    def read_unit(file_id: str, resource_id: str) -> dict:
        obs = [o for o in ctx.by_resource.get(resource_id, []) if o.file_id == file_id]
        return {"units": [{"observed_at": o.observed_at, "category": o.category, "exists": o.exists,
                           "fields": sorted(o.content), "event": o.collection} for o in obs[:5]]}

    impl = {"find_evidence": find_evidence, "read_unit": read_unit}
    tools = [{"functionDeclarations": [
        {"name": "find_evidence", "description": "List evidence observations whose record or lookup carries this exact identifier.",
         "parameters": {"type": "object", "properties": {"identifier": {"type": "string"}}, "required": ["identifier"]}},
        {"name": "read_unit", "description": "Read what one evidence file says about one resource.",
         "parameters": {"type": "object", "properties": {"file_id": {"type": "string"}, "resource_id": {"type": "string"}},
                        "required": ["file_id", "resource_id"]}},
    ]}]
    question = ("A former customer used the phone number +81 90 6156 0005. Using the tools, find which support "
                "record still holds that number and whether it is an attachment or a message. Answer with the "
                "resource id, its category, and the file_id that shows it. Do not guess.")
    contents = [{"role": "user", "parts": [{"text": question}]}]
    retrieved, requests = set(), 0
    for step in range(4):
        r = g.generate("smoke:tools", [f"step{step}"], contents, prompt_version="smoke-1", tools=tools,
                       config={"thinkingConfig": {"thinkingLevel": "low"}}, **NO_CACHE)
        requests += 1
        calls = r.function_calls
        cand = r.data["candidates"][0]["content"]
        contents.append(cand)   # keeps thought signatures intact
        if not calls:
            print("  final answer:", r.text[:400])
            break
        responses = []
        for c in calls:
            result = impl[c["name"]](**c.get("args", {}))
            for o in result.get("observations", []):
                retrieved.add(o["file_id"])
            if c["name"] == "read_unit":
                retrieved.add(c["args"]["file_id"])
            print(f"  step {step}: model called {c['name']}({c.get('args')}) -> {json.dumps(result)[:200]}")
            responses.append({"functionResponse": {"name": c["name"], "response": result}})
        contents.append({"role": "user", "parts": responses})
    cited = set(__import__("re").findall(r"evidence_\d+", r.text))
    print(f"  model requests: {requests}; cited {sorted(cited)}; all cited retrieved: {cited <= retrieved and bool(cited)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="1,2,3")
    args = ap.parse_args()
    g = Gemini(run_id=None)
    print("run_id", g.run_id, "attempts today before:", g.attempts_today(MODEL))
    g.admit(8)
    check_model(g)
    for n, fn in (("1", smoke_classification), ("2", smoke_vision), ("3", smoke_tools)):
        if n in args.only.split(","):
            print(f"\n== smoke {n}: {fn.__name__}")
            try:
                fn(g)
            except Exception as e:  # report and continue; every attempt is already in the ledger
                print(f"  FAILED: {type(e).__name__}: {e}")
    print("\nattempts today after:", g.attempts_today(MODEL))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
