"""Keyword, vector and hybrid search over chunks, behind one function.

Keyword search ranks with Postgres full-text (``websearch_to_tsquery``, any term may
match, ranked by ``ts_rank_cd``). Vector search ranks by cosine similarity on the embeddings. Hybrid
runs both and fuses the two rankings with reciprocal rank fusion (RRF).
"""

from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from sqlalchemy import Text, cast, func, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.retrieval.embeddings import Embedder

Mode = Literal["keyword", "vector", "hybrid"]

RRF_K = 60
CANDIDATES = 20

_COLUMNS = (
    models.Chunk.id,
    models.Chunk.document_id,
    models.Document.title,
    models.Document.status,
    models.Document.updated_at,
    models.Chunk.heading_path,
    models.Chunk.content,
)


@dataclass(frozen=True)
class SearchResult:
    chunk_id: int
    document_id: str
    title: str
    status: str
    updated_at: date
    heading_path: str
    content: str
    score: float
    keyword_score: float | None = None
    vector_score: float | None = None


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Fuse ranked id lists. An id scores ``1 / (k + rank)`` in each list that has it.

    Returns ``(id, score)`` best first. Ties keep the order in which ids first appear.
    """
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: -pair[1])


def fuse_results(
    keyword: list[SearchResult], vector: list[SearchResult], k: int = RRF_K
) -> list[SearchResult]:
    """Merge keyword and vector results into one list ordered by RRF score."""
    by_id = {r.chunk_id: r for r in vector} | {r.chunk_id: r for r in keyword}
    keyword_scores = {r.chunk_id: r.keyword_score for r in keyword}
    vector_scores = {r.chunk_id: r.vector_score for r in vector}
    fused = reciprocal_rank_fusion([[r.chunk_id for r in keyword], [r.chunk_id for r in vector]], k)
    return [
        replace(
            by_id[chunk_id],
            score=score,
            keyword_score=keyword_scores.get(chunk_id),
            vector_score=vector_scores.get(chunk_id),
        )
        for chunk_id, score in fused
    ]


def _result(row, score: float, **scores: float) -> SearchResult:
    return SearchResult(
        chunk_id=row.id,
        document_id=row.document_id,
        title=row.title,
        status=row.status,
        updated_at=row.updated_at,
        heading_path=row.heading_path,
        content=row.content,
        score=score,
        **scores,
    )


async def keyword_search(
    session: AsyncSession, query: str, limit: int = CANDIDATES
) -> list[SearchResult]:
    # websearch_to_tsquery ANDs every term, so a natural-language question matches no
    # chunk. Swap the ANDs for ORs and let ts_rank_cd rank chunks that match more terms.
    all_terms = func.websearch_to_tsquery("english", query)
    tsquery = cast(func.replace(cast(all_terms, Text), " & ", " | "), TSQUERY)
    rank = func.ts_rank_cd(models.Chunk.tsv, tsquery).label("score")
    statement = (
        select(*_COLUMNS, rank)
        .join(models.Document, models.Document.id == models.Chunk.document_id)
        .where(models.Chunk.tsv.op("@@")(tsquery))
        .order_by(rank.desc(), models.Chunk.id)
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()
    return [_result(row, row.score, keyword_score=row.score) for row in rows]


async def vector_search(
    session: AsyncSession, embedding: list[float], limit: int = CANDIDATES
) -> list[SearchResult]:
    distance = models.Chunk.embedding.cosine_distance(embedding).label("distance")
    statement = (
        select(*_COLUMNS, distance)
        .join(models.Document, models.Document.id == models.Chunk.document_id)
        .where(models.Chunk.embedding.is_not(None))
        .order_by(distance, models.Chunk.id)
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()
    return [_result(row, 1 - row.distance, vector_score=1 - row.distance) for row in rows]


async def search(
    session: AsyncSession,
    query: str,
    mode: Mode = "hybrid",
    limit: int = 5,
    embedder: Embedder | None = None,
) -> list[SearchResult]:
    """Return the top ``limit`` chunks for a query. Vector and hybrid modes need an embedder."""
    if mode != "keyword" and embedder is None:
        raise ValueError(f"{mode} search needs an embedder")

    if mode == "keyword":
        return await keyword_search(session, query, limit)

    assert embedder is not None
    embedding = (await embedder.embed([query]))[0]
    if mode == "vector":
        return await vector_search(session, embedding, limit)

    keyword = await keyword_search(session, query)
    vector = await vector_search(session, embedding)
    # Phase 6: rerank the fused candidates here, before cutting to ``limit``.
    return fuse_results(keyword, vector)[:limit]
