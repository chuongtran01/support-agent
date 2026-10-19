from datetime import date

from app.retrieval.documents import Document
from app.retrieval.ingest import plan_sync


def make(id: str, content_hash: str) -> Document:
    return Document(
        id=id,
        title=id,
        status="current",
        updated_at=date(2026, 1, 1),
        source="test",
        body="body",
        content_hash=content_hash,
    )


def test_new_file_is_added():
    plan = plan_sync([make("a", "1")], {})
    assert [f.id for f in plan.add] == ["a"]
    assert not plan.update and not plan.unchanged and not plan.remove


def test_changed_hash_is_updated():
    plan = plan_sync([make("a", "2")], {"a": "1"})
    assert [f.id for f in plan.update] == ["a"]
    assert not plan.add and not plan.unchanged


def test_same_hash_is_unchanged():
    plan = plan_sync([make("a", "1")], {"a": "1"})
    assert plan.unchanged == ["a"]
    assert not plan.add and not plan.update and not plan.remove


def test_stored_document_without_a_file_is_removed():
    plan = plan_sync([make("a", "1")], {"a": "1", "gone": "9"})
    assert plan.remove == ["gone"]
    assert plan.unchanged == ["a"]


def test_rerun_on_identical_state_changes_nothing():
    files = [make("a", "1"), make("b", "2")]
    plan = plan_sync(files, {"a": "1", "b": "2"})
    assert not plan.add and not plan.update and not plan.remove
    assert plan.unchanged == ["a", "b"]
