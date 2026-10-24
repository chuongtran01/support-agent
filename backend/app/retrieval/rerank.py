"""Rerank search candidates with a cross-encoder.

Search scores the question and each passage separately (an embedding, a keyword match). A
cross-encoder reads the question and one passage together and scores that pair, which is more
accurate and too slow to run on the whole knowledge base, so it only sees the fused candidates.

The model runs locally on ONNX (``fastembed``), so there is no API call. It is an optional
dependency (``pip install -e ".[rerank]"``), imported on first use.
"""

import asyncio
from dataclasses import replace
from functools import cache
from typing import TYPE_CHECKING, Protocol

from app.config import settings

if TYPE_CHECKING:  # search.py imports this module
    from app.retrieval.search import SearchResult


class Reranker(Protocol):
    async def score(self, query: str, passages: list[str]) -> list[float]:
        """One relevance score per passage; higher is more relevant."""
        ...


class CrossEncoderReranker:
    def __init__(self, model: str):
        self._model_name = model
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder
            except ImportError as error:
                raise RuntimeError(
                    'reranking needs fastembed: pip install -e ".[rerank]"'
                ) from error
            self._model = TextCrossEncoder(model_name=self._model_name)
        return self._model

    def _score(self, query: str, passages: list[str]) -> list[float]:
        return [float(s) for s in self._load().rerank(query, passages)]

    async def score(self, query: str, passages: list[str]) -> list[float]:
        # CPU-bound, so off the event loop
        return await asyncio.to_thread(self._score, query, passages)


async def rerank_results(
    reranker: Reranker, query: str, candidates: "list[SearchResult]"
) -> "list[SearchResult]":
    """The candidates best first by the reranker's score, which becomes ``score``. Ties keep
    the order they came in (the sort is stable)."""
    if not candidates:
        return []
    scores = await reranker.score(query, [_text(c) for c in candidates])
    ranked = sorted(zip(candidates, scores, strict=True), key=lambda pair: -pair[1])
    return [replace(c, score=s, rerank_score=s) for c, s in ranked]


def _text(result: "SearchResult") -> str:
    # The heading path says what the passage is about, as a reader of the article would see it
    return f"{result.heading_path}\n{result.content}" if result.heading_path else result.content


@cache
def get_reranker() -> Reranker | None:
    """The cross-encoder if ``RERANK_ENABLED`` is set, otherwise None."""
    return CrossEncoderReranker(settings.rerank_model) if settings.rerank_enabled else None
