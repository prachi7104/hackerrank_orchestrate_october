"""P1: subject scoping of PDF text. Oracle: the rule stated in each test (synthetic text layers)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import evidence  # noqa: E402

REGISTER = """DISPUTED ORDERS REGISTER
1
ORD-1 | user_08 | Card dispute | Retain until 2033
2
ORD-2 | user_17 | Chargeback | support_44 attached | Retain until 2033
3
ORD-3 | user_29 | Tax record | search_91 | a.b@example.com"""
STATEMENT = """CUSTOMER STATEMENT
Customer Ingrid Dahl
user_18
ingrid.dahl@outlook.com
statement NST-1842"""


@pytest.fixture
def parse(monkeypatch):
    def run(text, aliases, scoped=True):
        monkeypatch.setattr(evidence, "pdf_text", lambda _p: text)
        monkeypatch.setattr(evidence, "PDF_SUBJECT_SCOPE", scoped)
        return {o.resource_id: set(o.content.values())
                for o in evidence.parse_pdf("evidence_x", "2026-05-29T08:00:00Z", Path("x.pdf"), "legal", aliases)}
    return run


def test_user_id_alias_does_not_name_a_record(parse):
    # a document that prints user_17 does not mention the record whose catalog alias is user_17
    assert "tombstone_17" not in parse(REGISTER, {"user_17": "tombstone_17"})
    assert "tombstone_17" in parse(REGISTER, {"user_17": "tombstone_17"}, scoped=False)   # the old defect


def test_register_rows_carry_only_their_own_identifiers(parse):
    got = parse(REGISTER, {})
    assert got["support_44"] == {"user_17"}
    assert got["search_91"] == {"user_29", "a.b@example.com"}


def test_register_row_for_another_person_never_links(parse):
    got = parse(REGISTER, {})
    assert all("user_08" not in v for v in got.values())


def test_single_person_document_keeps_all_identifiers(parse):
    got = parse(STATEMENT, {"NST-1842": "export_1842"})
    assert got["export_1842"] == {"user_18", "ingrid.dahl@outlook.com"}


def test_non_user_alias_still_names_its_record_in_a_register(parse):
    got = parse(REGISTER + "\nORD-4 | user_40 | delivery_confirmation_19.pdf", {"delivery_confirmation_19.pdf": "object_19"})
    assert got["object_19"] == {"user_40"}
