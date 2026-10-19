"""Load the knowledge base into Postgres in two stages.

Stage 1 syncs the Markdown files into ``documents``: new files are added, changed
files (by content hash) are updated and lose their chunks, files that no longer
exist are removed. Stage 2 builds ``chunks`` from the stored documents, embedding
every document that has none. ``rebuild_all`` redoes stage 2 for every document,
for example after a chunking change.

Run: ``python -m app.retrieval.ingest [--stage documents|chunks|all] [--rebuild-all]``
"""

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.db import SessionLocal, engine
from app.retrieval.chunking import chunk_markdown
from app.retrieval.documents import Document as SourceDocument
from app.retrieval.documents import load_documents
from app.retrieval.embeddings import Embedder, OpenAIEmbedder

KB_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "kb"


@dataclass(frozen=True)
class SyncPlan:
    add: list[SourceDocument]
    update: list[SourceDocument]
    unchanged: list[str]
    remove: list[str]


def plan_sync(files: list[SourceDocument], stored_hashes: dict[str, str]) -> SyncPlan:
    """Compare the files on disk with the stored content hashes."""
    add = [f for f in files if f.id not in stored_hashes]
    update = [f for f in files if f.id in stored_hashes and stored_hashes[f.id] != f.content_hash]
    changed = {f.id for f in add + update}
    unchanged = [f.id for f in files if f.id not in changed]
    on_disk = {f.id for f in files}
    remove = sorted(stored_hashes.keys() - on_disk)
    return SyncPlan(add, update, unchanged, remove)


def _document_values(file: SourceDocument) -> dict:
    return {
        "title": file.title,
        "status": file.status,
        "updated_at": file.updated_at,
        "source": file.source,
        "content": file.body,
        "content_hash": file.content_hash,
    }


async def sync_documents(session: AsyncSession, files: list[SourceDocument]) -> SyncPlan:
    """Stage 1: make ``documents`` match the files."""
    rows = await session.execute(select(models.Document.id, models.Document.content_hash))
    plan = plan_sync(files, dict(rows.all()))

    for file in plan.add:
        session.add(models.Document(id=file.id, **_document_values(file)))
    for file in plan.update:
        stored = await session.get(models.Document, file.id)
        for name, value in _document_values(file).items():
            setattr(stored, name, value)
        await session.execute(delete(models.Chunk).where(models.Chunk.document_id == file.id))
    if plan.remove:
        # chunks go with their document (ON DELETE CASCADE)
        await session.execute(delete(models.Document).where(models.Document.id.in_(plan.remove)))
    await session.flush()
    return plan


async def build_chunks(
    session: AsyncSession, embedder: Embedder, rebuild_all: bool = False
) -> tuple[int, int]:
    """Stage 2: chunk and embed documents. Returns (documents, chunks) written."""
    query = select(models.Document).order_by(models.Document.id)
    if not rebuild_all:
        has_chunks = exists().where(models.Chunk.document_id == models.Document.id)
        query = query.where(~has_chunks)
    pending = (await session.scalars(query)).all()

    chunked = [(doc, chunk_markdown(doc.content, doc.title)) for doc in pending]
    texts = [chunk.embedding_text for _, chunks in chunked for chunk in chunks]
    vectors = iter(await embedder.embed(texts))

    written = 0
    for doc, chunks in chunked:
        await session.execute(delete(models.Chunk).where(models.Chunk.document_id == doc.id))
        for chunk in chunks:
            session.add(
                models.Chunk(
                    document_id=doc.id,
                    heading_path=chunk.heading_path,
                    content=chunk.content,
                    embedding=next(vectors),
                )
            )
            written += 1
    await session.flush()
    return len(pending), written


async def main(stage: str, rebuild_all: bool, directory: Path) -> None:
    try:
        if stage in ("documents", "all"):
            files = load_documents(directory)
            async with SessionLocal.begin() as session:
                plan = await sync_documents(session, files)
            print(
                f"documents: {len(plan.add)} added, {len(plan.update)} updated, "
                f"{len(plan.unchanged)} unchanged, {len(plan.remove)} removed"
            )
        if stage in ("chunks", "all"):
            embedder = OpenAIEmbedder()
            async with SessionLocal.begin() as session:
                documents, chunks = await build_chunks(session, embedder, rebuild_all)
            print(f"chunks: {chunks} written for {documents} documents")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stage", choices=["documents", "chunks", "all"], default="all")
    parser.add_argument("--rebuild-all", action="store_true", help="re-chunk every document")
    parser.add_argument("--directory", type=Path, default=KB_DIRECTORY)
    args = parser.parse_args()
    asyncio.run(main(args.stage, args.rebuild_all, args.directory))
