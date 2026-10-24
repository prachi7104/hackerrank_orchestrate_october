"""Bounded investigator: the model chooses which evidence to read for the core's open questions.

Flow per case: the deterministic core produces open questions -> the model calls read-only tools
(at most MAX_REQUESTS model requests, the last one forced to `submit`) -> code verifies every
resolution against the units the tools actually returned -> verified resolutions are applied as
overrides to the same audit_case decision path; rejected ones and abstentions change nothing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from core import AuditContext, audit_case
from evidence import Observation, load_manifest, norm, system_for
from llm import CallFailed, Gemini, image_part

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "investigator.md"
PROMPT_VERSION = "inv-v2"
MAX_REQUESTS = 4           # per case, including the forced submit
CONFIG = {"thinkingConfig": {"thinkingLevel": "low"}}
ACTIONS = ["DELETE", "REDACT", "ANONYMIZE", "RETAIN"]
STATES = ["PRESENT", "ABSENT", "RESTORE_ALLOWED", "RESTORE_BLOCKED", "FLOW_ACTIVE", "FLOW_DISABLED"]
SCREEN_STATE = {"PRESENT": (True, None), "ABSENT": (False, None), "RESTORE_ALLOWED": (True, True),
                "RESTORE_BLOCKED": (True, False), "FLOW_ACTIVE": (True, True), "FLOW_DISABLED": (True, False)}
SCREEN_SCHEMA = {"type": "object", "properties": {
    "resource_id": {"type": "string"}, "state_shown": {"type": "string", "enum": STATES + ["NOT_SHOWN"]},
    "visible_text": {"type": "array", "items": {"type": "string"}}, "answer": {"type": "string"}},
    "required": ["resource_id", "state_shown", "visible_text", "answer"]}

TOOLS = [{"functionDeclarations": [
    {"name": "search_evidence", "description": "Find evidence units whose record or lookup carries this exact identifier (email, phone, email hash, user id or resource id).",
     "parameters": {"type": "object", "properties": {"identifier": {"type": "string"}}, "required": ["identifier"]}},
    {"name": "read_resource", "description": "Read every evidence unit about one resource: time, existence, activity, identifiers present, lookup keys.",
     "parameters": {"type": "object", "properties": {"resource_id": {"type": "string"}}, "required": ["resource_id"]}},
    {"name": "list_activity", "description": "List workflow activity rows for a resource (claims, not proof).",
     "parameters": {"type": "object", "properties": {"resource_id": {"type": "string"}}, "required": ["resource_id"]}},
    {"name": "ask_screenshot", "description": "Ask one question about a screenshot evidence file (png) captured before the audit time; returns what the screen shows about one resource.",
     "parameters": {"type": "object", "properties": {"file_id": {"type": "string"}, "resource_id": {"type": "string"},
                                                     "question": {"type": "string"}}, "required": ["file_id", "resource_id", "question"]}},
    {"name": "submit", "description": "Submit one resolution per open question. Call exactly once.",
     "parameters": {"type": "object", "properties": {"resolutions": {"type": "array", "items": {
         "type": "object", "properties": {
             "question_id": {"type": "string"}, "outcome": {"type": "string", "enum": ["RESOLVED", "ABSTAIN"]},
             "answer": {"type": "string"}, "supporting_units": {"type": "array", "items": {"type": "string"}},
             "reason": {"type": "string"}},
         "required": ["question_id", "outcome", "answer", "supporting_units", "reason"]}}},
         "required": ["resolutions"]}},
]}]


@dataclass
class Investigation:
    case_id: str
    questions: list
    tool_calls: list = field(default_factory=list)
    resolutions: list = field(default_factory=list)      # as submitted
    verdicts: list = field(default_factory=list)          # code verification per resolution
    requests: int = 0
    error: str = ""


# ---------------------------------------------------------------- open questions from the core

def open_questions(ctx: AuditContext, case: dict, result) -> list[dict]:
    qs = []
    acct = ctx.accounts.get(case["case_id"])
    has_account = any(r["resource_id"].startswith("account_") and r["state"] != "UNKNOWN"
                      for r in result.trace["resources"])
    if not has_account:
        cands = sorted(set(acct.candidates) | ({acct.resource_id} if acct and acct.resource_id else set())) if acct else []
        qs.append({"id": "Q1", "type": "ACCOUNT", "candidates": cands,
                   "question": "Which account resource is this subject's primary account? Candidates come from "
                               "deletion-worker timing only and are unproven: " + (", ".join(cands) or "none")})
    for r in result.trace["resources"]:
        obs = [o for o in ctx.by_resource.get(r["resource_id"], []) if o.file_id == r["evidence_id"]]
        silent = obs and all(not o.content and not o.result for o in obs)
        if r["action"] in ("REDACT", "ANONYMIZE") and r["state"].startswith("PRESENT") and silent:
            qs.append({"id": f"Q{len(qs) + 1}", "type": "TREATMENT", "resource_id": r["resource_id"],
                       "question": f"{r['resource_id']} ({r['category']}) was found for this subject but its evidence "
                                   f"shows no field contents. Which treatment does deletion_rules.csv require?"})
        hashed = [o for o in obs if set(o.content) == {"correlation_key"}]
        if hashed and r["action"] == "ANONYMIZE":
            qs.append({"id": f"Q{len(qs) + 1}", "type": "TREATMENT", "resource_id": r["resource_id"],
                       "question": f"{r['resource_id']} is keyed only by a hashed identifier of the subject. Which "
                                   f"treatment does deletion_rules.csv require?"})
    for rid, why in result.trace.get("excluded", {}).items():
        qs.append({"id": f"Q{len(qs) + 1}", "type": "CONFLICT", "resource_id": rid,
                   "question": f"{rid} was excluded because {why}, while a data-catalog claim links it to the subject. "
                               f"Is it the subject's record?"})
    if result.status in ("COMPLETE", "UNRESOLVED_EVIDENCE") and not result.steps:
        unknown = [r["resource_id"] for r in result.trace["resources"] if r["state"] == "UNKNOWN"]
        qs.append({"id": f"Q{len(qs) + 1}", "type": "NO_EVIDENCE", "resources": unknown,
                   "question": "No linked resource has an admissible state at the audit time, so the case cannot be "
                               "shown complete or incomplete. Linked resources without state: "
                               f"{', '.join(unknown) or 'none'}. Find admissible evidence (captured after the request "
                               "and before the audit) of one resource's state, answering 'resource_id|STATE' with "
                               f"STATE in {STATES}, or abstain."})
    subject = ctx.subjects[case["case_id"]]
    keys = subject.link_keys()
    for r in result.trace["resources"]:
        owners = set()
        for o in ctx.by_resource.get(r["resource_id"], []):
            if ctx.visible(o, case["audit_at"]):
                for f in ("email", "requester_email", "customer_email", "owner_email"):
                    if f in o.content:
                        owners.add("subject" if norm(o.content[f]) in keys else norm(o.content[f]))
        if "subject" in owners and len(owners) > 1 or (r["link"].startswith("activity_") and owners - {"subject"}):
            qs.append({"id": f"Q{len(qs) + 1}", "type": "CONFLICT", "resource_id": r["resource_id"],
                       "question": f"Evidence about {r['resource_id']} names different people. Is it the subject's record?"})
    return qs


# ---------------------------------------------------------------- tools (strict audit_at visibility)

class Toolbox:
    def __init__(self, ctx: AuditContext, case: dict, g: Gemini | None = None):
        self.ctx, self.case, self.units, self.g = ctx, case, {}, g
        self.shots = {f.file_id: f for f in load_manifest(ctx.dataset) if f.kind == "png"}

    def _unit(self, o) -> dict:
        uid = f"{o.file_id}#{o.resource_id}#{o.locator}"
        self.units[uid] = o
        return {"unit_id": uid, "file_id": o.file_id, "resource_id": o.resource_id, "system": o.system_id,
                "observed_at": o.observed_at, "file_captured_at": o.captured_at, "source": o.collection,
                "exists": o.exists, "active": o.active, "result": o.result, "category": o.category,
                "identifiers_in_record": o.content, "lookup_keys": sorted(o.lookups)}

    def _visible(self, obs):
        return [o for o in obs if self.ctx.visible(o, self.case["audit_at"])]

    def search_evidence(self, identifier: str) -> dict:
        hits = self._visible(self.ctx.by_key.get(norm(identifier), []) + self.ctx.by_resource.get(identifier, []))
        return {"units": [self._unit(o) for o in hits[:15]], "truncated": len(hits) > 15}

    def read_resource(self, resource_id: str) -> dict:
        obs = sorted(self._visible(self.ctx.by_resource.get(resource_id, [])), key=lambda o: o.observed_at)
        return {"units": [self._unit(o) for o in obs[-12:]]}

    def ask_screenshot(self, file_id: str, resource_id: str, question: str) -> dict:
        f = self.shots.get(file_id)
        if f is None or f.captured_at > self.case["audit_at"]:
            return {"error": "not a screenshot captured before the audit time"}
        reply = self.g.generate("investigator:screen", [self.case["case_id"], file_id], [{"role": "user", "parts": [
            image_part(f.path), {"text": f"Resource: {resource_id}. Question: {question} Report only what is visibly "
                                         f"printed; quote it in visible_text; use NOT_SHOWN if the screen does not show it."}]}],
            prompt_version="screen-q-v1", schema=SCREEN_SCHEMA, config={"thinkingConfig": {"thinkingLevel": "low"}})
        data = json.loads(reply.text)
        exists, active = SCREEN_STATE.get(data.get("state_shown"), (None, None))
        obs = Observation(file_id, f.captured_at, f.captured_at, data.get("resource_id") or resource_id,
                          system_for(resource_id), exists=exists, active=active, collection="screen_question",
                          locator=f"question {len(self.units)}")
        obs.screen_state = data.get("state_shown")
        unit = self._unit(obs)
        return {**unit, "state_shown": data.get("state_shown"), "visible_text": data.get("visible_text", []),
                "answer": data.get("answer", "")}

    def list_activity(self, resource_id: str) -> dict:
        rows = [a for a in self.ctx.activities if a["resource_id"] == resource_id and a["event_time"] <= self.case["audit_at"]]
        return {"note": "activity rows are workflow claims", "rows": [
            {k: a[k] for k in ("activity_id", "event_time", "event_type", "source_system", "reported_result", "details")}
            for a in rows[:12]]}


# ---------------------------------------------------------------- verification of submitted answers

# Categories each system can hold. The investigator may reinterpret a record's category only within
# its system (a support record can be a message, an attachment or a contact-index identifier; an
# identity-service record is always a tombstone), so a treatment must come from deletion_rules.csv
# for one of these categories.
SYSTEM_CATEGORIES = {
    "support_platform": ("SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT", "SECONDARY_IDENTIFIER"),
    "analytics_platform": ("ANALYTICS_PROFILE", "PRODUCT_TELEMETRY", "SECONDARY_IDENTIFIER"),
    "identity_service": ("ACCOUNT_TOMBSTONE",),
    "search_service": ("SEARCH_INDEX_ENTRY",),
    "account_service": ("USER_PROFILE",),
    "export_service": ("USER_EXPORT",),
    "marketing_platform": ("MARKETING_PROFILE",),
    "security_service": ("SECURITY_AUDIT_LOG",),
    "legal_service": ("LEGAL_HOLD_RECORD",),
    "backup_service": ("BACKUP_ARCHIVE",),
    "email_delivery_service": ("EMAIL_DELIVERY_LOG",),
    "cache_service": ("CACHE_ENTRY",),
    "object_storage": ("SUPPORT_ATTACHMENT",),
}


def allowed_actions(ctx: AuditContext, rid: str) -> set:
    return {ctx.rules[c]["required_action"] for c in SYSTEM_CATEGORIES.get(system_for(rid), ()) if c in ctx.rules}

def verify(res: dict, q: dict, box: Toolbox, keys: set) -> tuple[bool, str]:
    if res.get("outcome") != "RESOLVED":
        return False, "abstained"
    units = [box.units[u] for u in res.get("supporting_units", []) if u in box.units]
    if not units:
        return False, "no supporting unit was returned by a tool in this investigation"
    ans = res.get("answer", "")
    if q["type"] == "ACCOUNT":
        if ans not in q["candidates"]:
            return False, f"{ans} is not a candidate"
        owned = [u for u in units if u.resource_id == ans and u.keys() & keys]
        return (True, "unit ties the account to a subject identifier") if owned else \
            (False, "no supporting unit carries both the account id and a subject identifier")
    if q["type"] == "TREATMENT":
        if ans not in ACTIONS:
            return False, f"invalid action {ans}"
        about = [u for u in units if u.resource_id == q["resource_id"]]
        if not about:
            return False, "units do not concern the resource"
        allowed = allowed_actions(box.ctx, q["resource_id"])
        if ans not in allowed:
            return False, f"{ans} is not a deletion_rules.csv action for any category {q['resource_id']}'s system holds ({sorted(allowed)})"
        return True, "supporting unit concerns the resource; action allowed by deletion_rules.csv for its system"
    if q["type"] == "NO_EVIDENCE":
        rid, _, state = ans.partition("|")
        if state not in STATES:
            return False, f"invalid state {state!r}"
        allowed = (["RESTORE_ALLOWED", "RESTORE_BLOCKED"] if rid.startswith("backup_") else
                   ["FLOW_ACTIVE", "FLOW_DISABLED"] if rid.startswith("data_flow_") else ["PRESENT", "ABSENT"])
        if state not in allowed:
            return False, f"{state} does not decide {rid}; needs one of {allowed}"
        ctx, case = box.ctx, box.case
        if rid not in ctx.linked(case, ctx.subjects[case["case_id"]]) or ctx.contradicted(case, rid):
            return False, f"{rid} is not tied to the subject without contradiction"
        exists, active = SCREEN_STATE[state]
        fits = [u for u in units if u.resource_id == rid and u.captured_at <= case["audit_at"]
                and u.observed_at >= case["requested_at"]
                and (u.exists == exists if active is None else u.active == active)]
        return (True, f"{fits[0].file_id} shows {state} inside the admissible time window") if fits else \
            (False, "no supporting unit shows that state inside the admissible time window")
    if q["type"] == "CONFLICT":
        about = [u for u in units if u.resource_id == q["resource_id"]]
        if ans == "SUBJECT":
            ok = [u for u in about if u.keys() & keys and u.collection != "data_catalog"]
            return (True, "record content carries a subject identifier") if ok else \
                (False, "only catalog claims or no subject identifier in the record")
        if ans == "OTHER":
            other = [u for u in about if u.content and not (u.keys() & keys)]
            return (True, "record content names someone else") if other else (False, "no unit shows another owner")
    return False, "unsupported answer"


# ---------------------------------------------------------------- loop

def investigate(g: Gemini, ctx: AuditContext, case: dict, result, questions: list) -> Investigation:
    inv = Investigation(case["case_id"], questions)
    box = Toolbox(ctx, case, g)
    subject = ctx.subjects[case["case_id"]]
    brief = {"case_id": case["case_id"], "requested_at": case["requested_at"], "audit_at": case["audit_at"],
             "subject": {"user_ids": sorted(subject.user_ids), "identifiers": sorted(subject.identifiers)},
             "deterministic_result": {"status": result.status, "resources": [
                 {k: r[k] for k in ("resource_id", "category", "state", "evidence_id")} for r in result.trace["resources"]]},
             "open_questions": questions}
    contents = [{"role": "user", "parts": [{"text": json.dumps(brief, ensure_ascii=False)}]}]
    impl = {"search_evidence": box.search_evidence, "read_resource": box.read_resource,
            "list_activity": box.list_activity, "ask_screenshot": box.ask_screenshot}
    for step in range(MAX_REQUESTS):
        last = step == MAX_REQUESTS - 1
        mode = {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["submit"]}} if last else \
            {"functionCallingConfig": {"mode": "ANY"}}
        try:
            reply = g.generate("investigator", [case["case_id"]], contents, system=PROMPT_FILE.read_text(encoding="utf-8"),
                               prompt_version=PROMPT_VERSION, tools=TOOLS, tool_config=mode, config=CONFIG)
        except CallFailed as e:
            inv.error = f"call failed: {e.category}"
            break
        inv.requests += 1
        contents.append(reply.data["candidates"][0]["content"])
        calls = reply.function_calls
        if not calls:
            inv.error = "model returned no tool call"
            break
        responses, submitted = [], None
        for c in calls:
            name, args = c["name"], c.get("args", {})
            if name == "submit":
                submitted = args.get("resolutions", [])
                inv.tool_calls.append({"tool": "submit", "args": {"n": len(submitted)}})
                continue
            out = impl[name](**args) if name in impl else {"error": "unknown tool"}
            inv.tool_calls.append({"tool": name, "args": args, "returned_units": len(out.get("units", out.get("rows", [])))})
            responses.append({"functionResponse": {"name": name, "response": out}})
        if submitted is not None:
            inv.resolutions = submitted
            break
        contents.append({"role": "user", "parts": responses})
    qmap = {q["id"]: q for q in questions}
    keys = subject.link_keys()
    for res in inv.resolutions:
        q = qmap.get(res.get("question_id"))
        ok, why = verify(res, q, box, keys) if q else (False, "unknown question id")
        inv.verdicts.append({"question_id": res.get("question_id"), "outcome": res.get("outcome"),
                             "answer": res.get("answer"), "accepted": ok, "why": why,
                             "supporting_units": res.get("supporting_units", []), "reason": res.get("reason", "")})
    return inv


def apply(ctx: AuditContext, case: dict, inv: Investigation):
    """Re-run the identical decision path with only the verified resolutions as overrides."""
    overrides = {"add_links": {}, "drop": set(), "action": {}}
    qmap = {q["id"]: q for q in inv.questions}
    for v in inv.verdicts:
        if not v["accepted"]:
            continue
        q = qmap[v["question_id"]]
        if q["type"] == "ACCOUNT":
            overrides["add_links"][v["answer"]] = f"investigator:{','.join(v['supporting_units'][:2])}"
            overrides["primary"] = v["answer"]
        elif q["type"] == "TREATMENT":
            overrides["action"][q["resource_id"]] = v["answer"]
        elif q["type"] == "CONFLICT" and v["answer"] == "OTHER":
            overrides["drop"].add(q["resource_id"])
        elif q["type"] == "CONFLICT" and v["answer"] == "SUBJECT":
            overrides["add_links"][q["resource_id"]] = f"investigator:{','.join(v['supporting_units'][:2])}"
        elif q["type"] == "NO_EVIDENCE":
            rid, _, state = v["answer"].partition("|")
            fid = v["why"].split(" ")[0]
            overrides.setdefault("state", {})[rid] = (state, fid)
    return audit_case(ctx, case, True, overrides)
