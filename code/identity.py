"""Resolve a request email to a subject: stable user_id plus every current and historical identifier."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from evidence import norm

IDENTIFIER_FIELDS = ("email", "phone", "email_hash", "name")


@dataclass
class Subject:
    request_email: str
    user_ids: set = field(default_factory=set)
    identifiers: dict = field(default_factory=dict)   # normalised value -> provenance (identity row or audit_id)
    names: set = field(default_factory=set)

    @property
    def resolved(self) -> bool:
        return len(self.user_ids) == 1

    def link_keys(self) -> set[str]:
        """Exact-match keys used to connect evidence: user_ids, emails, phones and email hashes.

        Names are excluded: they are not unique ("Jonas Berg" vs "Jonas Berglund"), so they count
        as exposed PII but never establish membership.
        """
        return {norm(u) for u in self.user_ids} | set(self.identifiers)


class IdentityIndex:
    def __init__(self, dataset: Path):
        self.current = list(csv.DictReader(open(dataset / "user_identities.csv", encoding="utf-8")))
        self.history = list(csv.DictReader(open(dataset / "user_audit_log.csv", encoding="utf-8")))

    def resolve(self, email: str, audit_at: str) -> Subject:
        """Match the request email against current and historical emails, then collect every identifier.

        Identity changes after audit_at are ignored: they were not known at the audit time.
        """
        key = norm(email)
        subject = Subject(email)
        for u in self.current:
            if norm(u["email"]) == key:
                subject.user_ids.add(u["user_id"])
        for a in self.history:
            if a["field_name"] == "email" and a["event_time"] <= audit_at and key in (norm(a["old_value"]), norm(a["new_value"])):
                subject.user_ids.add(a["user_id"])
        for u in self.current:
            if u["user_id"] in subject.user_ids:
                for f in ("email", "phone", "email_hash"):
                    if u[f]:
                        subject.identifiers[norm(u[f])] = f"user_identities:{u['user_id']}.{f}"
                subject.names.add(u["name"])
        for a in self.history:
            if a["user_id"] in subject.user_ids and a["field_name"] in ("email", "phone", "email_hash"):
                for v in (a["old_value"], a["new_value"]):
                    if v:
                        subject.identifiers.setdefault(norm(v), a["audit_id"])
            if a["user_id"] in subject.user_ids and a["field_name"] == "name":
                subject.names.update(v for v in (a["old_value"], a["new_value"]) if v)
        return subject

    def owner_index(self) -> dict[str, str]:
        """Normalised email / phone / email hash / user_id -> user_id, current and historical."""
        out = {}
        for u in self.current:
            out[norm(u["user_id"])] = u["user_id"]
            for f in ("email", "phone", "email_hash"):
                if u[f]:
                    out[norm(u[f])] = u["user_id"]
        for a in self.history:
            if a["field_name"] in ("email", "phone", "email_hash"):
                for v in (a["old_value"], a["new_value"]):
                    if v:
                        out.setdefault(norm(v), a["user_id"])
        return out
