"""Semantic request authorisation: does request_text authorise deletion?

Unique texts are classified in batches with strict id correspondence. Each returned item is
validated (exact id set, enum, verbatim evidence quote, type/flag consistency). Items that fail
validation, or are missing, are retried once on their own; a text that still fails is reported as an
operational failure, never guessed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from llm import CallFailed, Gemini

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "request_authorization.md"
PROMPT_VERSION = "authz-v1"
BATCH_SIZE = 10
CONFIG = {"thinkingConfig": {"thinkingLevel": "minimal"}}
TYPES = ["DELETION", "ACCESS", "EXPORT", "CORRECTION", "MARKETING_OPT_OUT", "ACCOUNT_SUPPORT", "OTHER"]
SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "request_type": {"type": "string", "enum": TYPES},
            "authorizes_deletion": {"type": "boolean"},
            "evidence_quote": {"type": "string"},
        },
        "required": ["id", "request_type", "authorizes_deletion", "evidence_quote"]}}},
    "required": ["items"],
}


@dataclass
class Authorization:
    text_id: str
    status: str                       # ok | failed
    authorizes_deletion: bool | None
    request_type: str | None = None
    evidence_quote: str = ""
    error: str = ""


def text_id(text: str) -> str:
    return "r" + hashlib.sha256(text.encode()).hexdigest()[:12]


def squash(s: str) -> str:
    return " ".join(s.split())


def validate(item: dict, texts: dict[str, str]) -> str:
    if item.get("id") not in texts:
        return "unknown id"
    if item.get("request_type") not in TYPES:
        return "invalid request_type"
    if not isinstance(item.get("authorizes_deletion"), bool):
        return "authorizes_deletion not boolean"
    if item["authorizes_deletion"] != (item["request_type"] == "DELETION"):
        return "request_type and authorizes_deletion disagree"
    quote = squash(item.get("evidence_quote", ""))
    if not quote or quote not in squash(texts[item["id"]]):
        return "evidence_quote not verbatim"
    return ""


def _ask(g: Gemini, batch: dict[str, str]) -> tuple[dict[str, dict], dict[str, str]]:
    body = "\n".join(f'<request id="{i}">{t}</request>' for i, t in batch.items())
    reply = g.generate("intent", list(batch), [{"role": "user", "parts": [{"text": body}]}],
                       system=PROMPT_FILE.read_text(encoding="utf-8"), prompt_version=PROMPT_VERSION,
                       schema=SCHEMA, config=CONFIG)
    try:
        items = json.loads(reply.text)["items"]
    except (ValueError, KeyError, TypeError) as e:
        g.log({"kind": "parse_error", "purpose": "intent", "items": list(batch), "detail": str(e)[:200]})
        return {}, {i: "unparseable response" for i in batch}
    ok, bad, seen = {}, {}, {}
    for it in items:
        seen[it.get("id")] = seen.get(it.get("id"), 0) + 1
    for it in items:
        err = validate(it, batch) or ("duplicate id" if seen.get(it.get("id"), 0) > 1 else "")
        if err:
            bad[it.get("id")] = err
        else:
            ok[it["id"]] = it
    for i in batch:
        if i not in ok and i not in bad:
            bad[i] = "missing from response"
    if bad:
        g.log({"kind": "validation_error", "purpose": "intent", "items": sorted(bad), "detail": bad})
    return ok, {i: e for i, e in bad.items() if i in batch}


def classify(g: Gemini, texts: list[str]) -> dict[str, Authorization]:
    """Returns text -> Authorization for every distinct text."""
    unique = {text_id(t): t for t in texts}
    order = sorted(unique)           # stable batching keeps cache identities stable across runs
    results: dict[str, Authorization] = {}
    retry: dict[str, str] = {}
    for start in range(0, len(order), BATCH_SIZE):
        batch = {i: unique[i] for i in order[start:start + BATCH_SIZE]}
        try:
            ok, bad = _ask(g, batch)
        except CallFailed as e:
            ok, bad = {}, {i: f"call failed: {e.category}" for i in batch}
        for i, it in ok.items():
            results[i] = Authorization(i, "ok", it["authorizes_deletion"], it["request_type"], it["evidence_quote"])
        retry.update(bad)
    for i, first_error in retry.items():          # isolated single-item retry
        try:
            ok, bad = _ask(g, {i: unique[i]})
        except CallFailed as e:
            ok, bad = {}, {i: f"call failed: {e.category}"}
        if i in ok:
            it = ok[i]
            results[i] = Authorization(i, "ok", it["authorizes_deletion"], it["request_type"], it["evidence_quote"])
        else:
            results[i] = Authorization(i, "failed", None, error=f"{first_error}; retry: {bad.get(i)}")
    return {unique[i]: a for i, a in results.items()}


def planned_requests(texts: list[str]) -> int:
    n = len({text_id(t) for t in texts})
    return -(-n // BATCH_SIZE)
