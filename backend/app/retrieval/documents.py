"""Load knowledge-base Markdown files: front matter plus body."""

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

STATUSES = {"current", "stale", "draft"}
REQUIRED = ("id", "title", "status", "updated_at", "source")


class InvalidDocument(ValueError):
    """A knowledge-base file has bad or missing front matter."""


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    status: str
    updated_at: date
    source: str
    body: str
    content_hash: str


def parse_document(text: str) -> Document:
    """Parse one Markdown file. The hash covers the whole file, front matter included."""
    if not text.startswith("---\n"):
        raise InvalidDocument("missing front matter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise InvalidDocument("unterminated front matter")
    meta = yaml.safe_load(text[4:end])
    if not isinstance(meta, dict):
        raise InvalidDocument("front matter is not a mapping")
    missing = [key for key in REQUIRED if key not in meta]
    if missing:
        raise InvalidDocument(f"front matter missing: {', '.join(missing)}")
    if meta["status"] not in STATUSES:
        raise InvalidDocument(f"unknown status: {meta['status']!r}")
    if not isinstance(meta["updated_at"], date):
        raise InvalidDocument("updated_at must be a YYYY-MM-DD date")
    return Document(
        id=str(meta["id"]),
        title=str(meta["title"]),
        status=meta["status"],
        updated_at=meta["updated_at"],
        source=str(meta["source"]),
        body=text[end + 5 :].strip(),
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def load_documents(directory: Path) -> list[Document]:
    """Load every ``*.md`` file in a directory; each id must match its filename."""
    documents = []
    for path in sorted(directory.glob("*.md")):
        try:
            document = parse_document(path.read_text())
        except InvalidDocument as error:
            raise InvalidDocument(f"{path.name}: {error}") from error
        if document.id != path.stem:
            raise InvalidDocument(f"{path.name}: id {document.id!r} does not match the filename")
        documents.append(document)
    return documents
