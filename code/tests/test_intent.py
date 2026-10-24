"""Model-boundary tests for the request classifier, with a scripted fake provider (no API calls)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intent import classify, text_id, validate  # noqa: E402
from llm import Reply  # noqa: E402


class FakeGemini:
    """Returns scripted item lists; records which ids each request carried."""

    def __init__(self, script):
        self.script, self.requests = script, []

    def generate(self, purpose, ids, contents, **kw):
        self.requests.append(list(ids))
        items = self.script(ids)
        return Reply({"candidates": [{"content": {"parts": [{"text": json.dumps({"items": items})}]}}]}, False, "fake")

    def log(self, entry):
        pass


def item(i, deletion=True, quote="delete"):
    return {"id": i, "request_type": "DELETION" if deletion else "ACCESS", "authorizes_deletion": deletion,
            "evidence_quote": quote}


def test_validate_rejects_inconsistent_or_ungrounded_items():
    texts = {"a": "Please delete my account"}
    assert validate(item("a"), texts) == ""
    assert validate({**item("a"), "authorizes_deletion": False}, texts) != ""
    assert validate(item("a", quote="erase everything"), texts) == "evidence_quote not verbatim"
    assert validate(item("zz"), texts) == "unknown id"


def test_missing_item_is_retried_alone_then_reported_as_failure():
    texts = ["Please delete my account", "Please delete my data too"]
    missing = text_id(texts[1])
    fake = FakeGemini(lambda ids: [item(i) for i in ids if i != missing])   # never answers the second text
    out = classify(fake, texts)
    assert out[texts[0]].status == "ok" and out[texts[0]].authorizes_deletion is True
    assert out[texts[1]].status == "failed" and out[texts[1]].authorizes_deletion is None
    assert fake.requests[-1] == [missing]          # isolated retry carried only the failed id


def test_duplicate_ids_are_not_accepted():
    texts = ["Please delete my account"]
    fake = FakeGemini(lambda ids: [item(ids[0]), item(ids[0], deletion=False, quote="delete")])
    assert classify(fake, texts)[texts[0]].status == "failed"
