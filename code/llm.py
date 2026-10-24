"""Gemini Developer API client with an append-only ledger, admission control and a versioned cache.

One provider, one pinned model, no failover. Every HTTP attempt is written to the ledger, including
failures. Token counts are copied from the provider's usageMetadata; a missing field stays None
("unknown"), never 0. The API key is read from the environment or the repo-root .env and is never
logged, printed or stored.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "code" / ".cache"
LEDGER = CACHE_DIR / "ledger.jsonl"
API = "https://generativelanguage.googleapis.com/v1beta"

MODEL = "gemini-3.5-flash-lite"
# Project limits reported from AI Studio for the Free-tier project on 24 Oct 2026.
LIMITS = {"rpm": 15, "tpm": 250_000, "rpd": 500}
RPM_TARGET = 12            # stay below the 15 RPM limit
DAILY_RESERVE = 0.25       # kept back for final generation and recovery
MAX_ATTEMPTS = 3           # per logical request
TIMEOUT_S = 90
PACIFIC = ZoneInfo("America/Los_Angeles")   # RPD resets at midnight Pacific


def sha(obj) -> str:
    data = obj if isinstance(obj, bytes) else json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


def api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            name, _, value = line.partition("=")
            if name.strip().removeprefix("export ").strip() == "GEMINI_API_KEY":
                key = value.strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set (environment or repo-root .env)")
    return key


def image_part(path: Path) -> dict:
    """Inline image with its media type detected from magic bytes, never from the extension."""
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif data[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError(f"unsupported image bytes in {path.name}")
    return {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}


def run_summary(run_id: str) -> dict:
    """Billed attempts, cache hits and provider-reported tokens for one run, from the ledger.
    A token total is None when any successful attempt lacked that field (unknown, not zero)."""
    out = {"attempts": 0, "failed_attempts": 0, "cache_hits": 0, "input": 0, "output": 0, "thinking": 0, "total": 0,
           "unknown_token_fields": 0, "latency_s": 0.0}
    if not LEDGER.exists():
        return out
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("run_id") != run_id:
            continue
        if e["kind"] == "cache_hit":
            out["cache_hits"] += 1
        elif e["kind"] == "attempt":
            out["attempts"] += 1
            out["latency_s"] += e.get("latency_s") or 0
            if e.get("error"):
                out["failed_attempts"] += 1
            for k in ("input", "output", "thinking", "total"):
                v = (e.get("usage") or {}).get(k)
                if v is not None:
                    out[k] += v
                elif k == "thinking":
                    out["thinking_field_absent"] = out.get("thinking_field_absent", 0) + 1
                else:
                    out["unknown_token_fields"] += 1   # includes failed attempts without usage
    out["latency_s"] = round(out["latency_s"], 1)
    return out


class BudgetExceeded(RuntimeError):
    pass


class CallFailed(RuntimeError):
    def __init__(self, category: str, detail: str):
        super().__init__(f"{category}: {detail}")
        self.category = category


@dataclass
class Reply:
    data: dict                 # raw generateContent response body
    cached: bool
    origin_run: str
    usage: dict = field(default_factory=dict)

    @property
    def parts(self) -> list:
        cands = self.data.get("candidates") or [{}]
        return (cands[0].get("content") or {}).get("parts", [])

    @property
    def text(self) -> str:
        return "".join(p.get("text", "") for p in self.parts if not p.get("thought"))

    @property
    def function_calls(self) -> list:
        return [p["functionCall"] for p in self.parts if "functionCall" in p]


def usage_from(body: dict) -> dict:
    u = body.get("usageMetadata")
    if u is None:
        return {"input": None, "output": None, "thinking": None, "total": None, "raw": None}
    return {"input": u.get("promptTokenCount"), "output": u.get("candidatesTokenCount"),
            "thinking": u.get("thoughtsTokenCount"), "total": u.get("totalTokenCount"), "raw": u}


class Gemini:
    def __init__(self, run_id: str | None = None, final: bool = False, model: str = MODEL):
        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
        self.final = final          # only the final generation run may use the reserve
        self.model = model
        self.recent: list[float] = []
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / "responses").mkdir(exist_ok=True)

    # ---------------------------------------------------------------- ledger and admission
    def log(self, entry: dict) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "run_id": self.run_id, **entry}
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def attempts_today(model: str) -> int:
        """HTTP attempts sent to the provider in the current Pacific day (all runs, all purposes)."""
        if not LEDGER.exists():
            return 0
        today = datetime.now(PACIFIC).date()
        n = 0
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            if e.get("kind") == "attempt" and e.get("model") == model:
                if datetime.fromisoformat(e["ts"]).astimezone(PACIFIC).date() == today:
                    n += 1
        return n

    def admit(self, planned_requests: int) -> None:
        cap = LIMITS["rpd"] if self.final else int(LIMITS["rpd"] * (1 - DAILY_RESERVE))
        used = self.attempts_today(self.model)
        if used + planned_requests > cap:
            raise BudgetExceeded(f"{used} used today + {planned_requests} planned > {cap} allowed "
                                 f"({'final' if self.final else 'development'} cap)")

    def _pace(self) -> None:
        while True:
            now = time.monotonic()
            self.recent = [t for t in self.recent if now - t < 60]
            if len(self.recent) < RPM_TARGET:
                self.recent.append(now)
                return
            time.sleep(60 - (now - self.recent[0]) + 0.5)

    # ---------------------------------------------------------------- calls
    def generate(self, purpose: str, item_ids: list[str], contents: list, *, system: str = "",
                 prompt_version: str = "", schema: dict | None = None, tools: list | None = None,
                 tool_config: dict | None = None, config: dict | None = None, use_cache: bool = True) -> Reply:
        config = dict(config or {})
        body = {"contents": contents, "generationConfig": config}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if schema:
            body["generationConfig"] = {**config, "responseMimeType": "application/json", "responseJsonSchema": schema}
        if tools:
            body["tools"] = tools
        if tool_config:
            body["toolConfig"] = tool_config
        identity = {
            "model": self.model, "input": sha(contents), "prompt": sha([system, prompt_version]),
            "schema": sha(schema), "config": sha(config), "tools": sha([tools, tool_config]),
        }
        key = sha(identity)
        cache_file = CACHE_DIR / "responses" / f"{key}.json"
        if use_cache and cache_file.exists():
            hit = json.loads(cache_file.read_text(encoding="utf-8"))
            self.log({"kind": "cache_hit", "purpose": purpose, "items": item_ids, "model": self.model,
                      "cache_key": key, "origin_run": hit["run_id"], "identity": identity})
            return Reply(hit["response"], True, hit["run_id"], usage_from(hit["response"]))

        url = f"{API}/models/{self.model}:generateContent"
        payload = json.dumps(body).encode()
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.admit(1)
            self._pace()
            started = time.monotonic()
            status, resp, category, detail, retry_after = None, None, None, "", None
            try:
                req = urllib.request.Request(url, data=payload, method="POST", headers={
                    "Content-Type": "application/json", "x-goog-api-key": api_key()})
                with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                    status, resp = r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                status = e.code
                err = json.loads(e.read() or b"{}").get("error", {})
                detail = err.get("message", "")[:300]
                category = {429: "rate_limited", 400: "invalid_request", 401: "auth", 403: "auth",
                            404: "model_not_found"}.get(status, "server_error" if status >= 500 else "http_error")
                for d in err.get("details", []):
                    if str(d.get("@type", "")).endswith("RetryInfo"):
                        retry_after = float(str(d.get("retryDelay", "0s")).rstrip("s") or 0)
            except TimeoutError:
                category, detail = "timeout", f"> {TIMEOUT_S}s"
            except urllib.error.URLError as e:
                category, detail = "network", str(e.reason)[:200]
            latency = round(time.monotonic() - started, 3)
            usage = usage_from(resp) if resp else usage_from({})
            if resp is not None and not (resp.get("candidates") or [{}])[0].get("content"):
                category, detail = "empty_response", str((resp.get("candidates") or [{}])[0].get("finishReason"))
            self.log({"kind": "attempt", "purpose": purpose, "items": item_ids, "model": self.model,
                      "returned_model": (resp or {}).get("modelVersion"), "attempt": attempt, "status": status,
                      "error": category, "detail": detail, "latency_s": latency, "usage": usage,
                      "cache_key": key, "identity": identity})
            if category is None:
                cache_file.write_text(json.dumps({"run_id": self.run_id, "identity": identity, "response": resp},
                                                 ensure_ascii=False), encoding="utf-8")
                return Reply(resp, False, self.run_id, usage)
            last_error = CallFailed(category, detail)
            if category in ("invalid_request", "auth", "model_not_found") or attempt == MAX_ATTEMPTS:
                break
            delay = retry_after if retry_after else 2 ** attempt
            time.sleep(min(delay, 65) + random.uniform(0, 1))
        raise last_error
