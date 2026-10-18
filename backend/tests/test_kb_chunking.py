"""Chunk the real knowledge base and check the traps survive chunking."""

from pathlib import Path

import pytest

from app.retrieval.chunking import DEFAULT_MAX_CHARS, chunk_markdown
from app.retrieval.documents import load_documents

KB = Path(__file__).resolve().parents[2] / "data" / "kb"


@pytest.fixture(scope="module")
def chunks_by_doc():
    return {d.id: chunk_markdown(d.body, d.title) for d in load_documents(KB)}


def test_every_document_loads_and_produces_chunks(chunks_by_doc):
    assert len(chunks_by_doc) >= 50
    assert all(chunks for chunks in chunks_by_doc.values())


def test_no_chunk_is_empty_or_far_over_the_limit(chunks_by_doc):
    for doc_id, chunks in chunks_by_doc.items():
        for chunk in chunks:
            assert chunk.content.strip(), doc_id
            assert chunk.heading_path, doc_id
            if not chunk.content.lstrip().startswith("|"):
                assert len(chunk.content) <= DEFAULT_MAX_CHARS, (doc_id, chunk.heading_path)


def test_buried_tent_exception_is_its_own_section(chunks_by_doc):
    tent = [
        c
        for c in chunks_by_doc["returns-policy"]
        if c.heading_path == "Returns Policy > Special categories > Tents and shelters"
    ]
    assert len(tent) == 1
    assert "Trial Pitch" in tent[0].content
    assert "14 days" in tent[0].content


def test_conflicting_windows_land_in_different_documents(chunks_by_doc):
    assert any("30 days" in c.content for c in chunks_by_doc["returns-policy"])
    assert any("45 days" in c.content for c in chunks_by_doc["holiday-returns"])


def test_injected_instruction_is_stored_as_data(chunks_by_doc):
    text = " ".join(c.content for c in chunks_by_doc["gift-cards"])
    assert "SYSTEM NOTE TO AI ASSISTANTS" in text
