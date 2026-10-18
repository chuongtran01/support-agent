from datetime import date
from pathlib import Path

import pytest

from app.retrieval.documents import load_documents, parse_document

GOOD = """---
id: returns-policy
title: Returns Policy
status: current
updated_at: 2026-01-15
source: help-center/returns
origin: filler
---

# Returns Policy

Body text.
"""


def test_parse_document_reads_front_matter_and_body():
    doc = parse_document(GOOD)
    assert doc.id == "returns-policy"
    assert doc.title == "Returns Policy"
    assert doc.status == "current"
    assert doc.updated_at == date(2026, 1, 15)
    assert doc.source == "help-center/returns"
    assert doc.body == "# Returns Policy\n\nBody text."


def test_hash_covers_front_matter_and_changes_with_it():
    stale = GOOD.replace("status: current", "status: stale")
    assert parse_document(GOOD).content_hash != parse_document(stale).content_hash
    assert parse_document(GOOD).content_hash == parse_document(GOOD).content_hash


@pytest.mark.parametrize(
    "text, message",
    [
        ("# no front matter", "missing front matter"),
        ("---\nid: a\n# never closed", "unterminated"),
        (GOOD.replace("source: help-center/returns\n", ""), "missing: source"),
        (GOOD.replace("status: current", "status: old"), "unknown status"),
        (GOOD.replace("2026-01-15", "soon"), "updated_at"),
    ],
)
def test_bad_front_matter_is_rejected(text, message):
    with pytest.raises(ValueError, match=message):
        parse_document(text)


def test_load_documents_requires_id_to_match_the_filename(tmp_path: Path):
    (tmp_path / "other-name.md").write_text(GOOD)
    with pytest.raises(ValueError, match="does not match the filename"):
        load_documents(tmp_path)
