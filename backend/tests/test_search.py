from datetime import date

import pytest

from app.retrieval.search import SearchResult, fuse_results, reciprocal_rank_fusion, search


def result(chunk_id: int, score: float = 0.0, **scores: float) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id=f"doc-{chunk_id}",
        title="Title",
        status="current",
        updated_at=date(2026, 1, 1),
        heading_path="Title",
        content="content",
        score=score,
        **scores,
    )


def test_rrf_scores_each_list_by_reciprocal_rank():
    fused = dict(reciprocal_rank_fusion([[1, 2], [2, 3]], k=60))
    assert fused[1] == pytest.approx(1 / 61)
    assert fused[2] == pytest.approx(1 / 62 + 1 / 61)
    assert fused[3] == pytest.approx(1 / 62)


def test_rrf_ranks_an_item_in_both_lists_first():
    assert reciprocal_rank_fusion([[1, 2], [3, 2]])[0][0] == 2


def test_rrf_with_one_empty_list_keeps_the_other_order():
    assert [item for item, _ in reciprocal_rank_fusion([[5, 4, 6], []])] == [5, 4, 6]


def test_rrf_ties_keep_first_seen_order():
    assert [item for item, _ in reciprocal_rank_fusion([[1], [2]])] == [1, 2]


def test_rrf_of_nothing_is_empty():
    assert reciprocal_rank_fusion([[], []]) == []


def test_fuse_results_keeps_both_scores_for_a_shared_chunk():
    keyword = [result(1, keyword_score=0.4), result(2, keyword_score=0.1)]
    vector = [result(2, vector_score=0.9), result(3, vector_score=0.8)]
    fused = fuse_results(keyword, vector)
    assert [r.chunk_id for r in fused] == [2, 1, 3]
    assert fused[0].keyword_score == 0.1 and fused[0].vector_score == 0.9
    assert fused[1].vector_score is None
    assert fused[2].keyword_score is None


def test_fuse_results_score_is_the_rrf_score():
    fused = fuse_results([result(1, keyword_score=0.4)], [result(1, vector_score=0.9)])
    assert fused[0].score == pytest.approx(2 / 61)


async def test_vector_and_hybrid_need_an_embedder():
    for mode in ("vector", "hybrid"):
        with pytest.raises(ValueError, match="embedder"):
            await search(None, "question", mode=mode)
