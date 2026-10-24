import pytest

from app.config import settings
from app.retrieval import rerank
from app.retrieval.rerank import rerank_results
from app.retrieval.search import search
from tests.test_search import result


class FakeReranker:
    def __init__(self, scores: list[float]):
        self._scores = scores
        self.seen: tuple[str, list[str]] | None = None

    async def score(self, query, passages):
        self.seen = (query, passages)
        return self._scores


async def test_candidates_come_back_best_score_first():
    candidates = [result(1), result(2), result(3)]
    ranked = await rerank_results(FakeReranker([0.1, 5.0, -2.0]), "q", candidates)
    assert [r.chunk_id for r in ranked] == [2, 1, 3]


async def test_the_reranker_score_becomes_the_score_and_the_other_scores_stay():
    candidates = [result(1, vector_score=0.8, keyword_score=0.3)]
    [ranked] = await rerank_results(FakeReranker([3.5]), "q", candidates)
    assert ranked.score == 3.5 and ranked.rerank_score == 3.5
    assert ranked.vector_score == 0.8 and ranked.keyword_score == 0.3


async def test_equal_scores_keep_the_order_they_came_in():
    ranked = await rerank_results(
        FakeReranker([1.0, 1.0, 1.0]), "q", [result(3), result(1), result(2)]
    )
    assert [r.chunk_id for r in ranked] == [3, 1, 2]


async def test_nothing_to_rank_is_not_scored():
    reranker = FakeReranker([])
    assert await rerank_results(reranker, "q", []) == []
    assert reranker.seen is None


async def test_the_reranker_reads_the_heading_with_the_text():
    reranker = FakeReranker([0.0])
    await rerank_results(reranker, "how long?", [result(1)])
    assert reranker.seen == ("how long?", ["Title\ncontent"])


async def test_rerank_search_needs_a_reranker_and_an_embedder():
    with pytest.raises(ValueError, match="reranker"):
        await search(None, "q", mode="rerank", embedder=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="embedder"):
        await search(None, "q", mode="rerank", reranker=FakeReranker([]))  # type: ignore[arg-type]


@pytest.fixture
def fresh_cache():
    rerank.get_reranker.cache_clear()
    yield
    rerank.get_reranker.cache_clear()


def test_the_reranker_is_off_unless_enabled(fresh_cache, monkeypatch):
    monkeypatch.setattr(settings, "rerank_enabled", False)
    assert rerank.get_reranker() is None


def test_enabled_gives_the_cross_encoder_without_loading_the_model(fresh_cache, monkeypatch):
    monkeypatch.setattr(settings, "rerank_enabled", True)
    reranker = rerank.get_reranker()
    assert isinstance(reranker, rerank.CrossEncoderReranker)
    assert reranker._model is None
