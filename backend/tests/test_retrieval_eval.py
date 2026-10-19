import json
from datetime import date

import pytest
from retrieval_eval import (
    EvalRow,
    InvalidDataset,
    evaluate_row,
    load_dataset,
    summarize,
)

from app.retrieval.search import SearchResult

KNOWN = {"returns-policy", "shipping-rates", "shipping-rates-legacy", "gift-cards"}


def row(**overrides) -> EvalRow:
    fields = {
        "id": "r1",
        "category": "returns",
        "question": "Q?",
        "relevant_document_ids": ["returns-policy"],
        "relevant_heading_paths": [],
        "distractor_document_ids": [],
        "expect_no_answer": False,
        "trap_id": None,
        "note": None,
    }
    return EvalRow(**(fields | overrides))


def hit(document_id: str, heading_path: str = "Title", score: float = 0.5) -> SearchResult:
    return SearchResult(
        chunk_id=0,
        document_id=document_id,
        title="Title",
        status="current",
        updated_at=date(2026, 1, 1),
        heading_path=heading_path,
        content="content",
        score=score,
    )


def test_first_rank_is_the_position_of_the_first_relevant_chunk():
    results = [hit("a"), hit("b"), hit("returns-policy")]
    assert evaluate_row(row(), results).first_rank == 3


def test_no_relevant_chunk_means_no_rank():
    assert evaluate_row(row(), [hit("a"), hit("b")]).first_rank is None


def test_heading_path_must_match_when_the_row_names_one():
    wanted = "Returns Policy > Special categories > Tents and shelters"
    r = row(relevant_heading_paths=[wanted])
    wrong_section = hit("returns-policy", "Returns Policy > Overview")
    right_section = hit("returns-policy", wanted)
    assert evaluate_row(r, [wrong_section, right_section]).first_rank == 2


def test_all_documents_found_needs_every_relevant_document_in_the_top_five():
    r = row(relevant_document_ids=["returns-policy", "shipping-rates"])
    both = [hit("returns-policy"), hit("x"), hit("shipping-rates")]
    only_one = [
        hit("returns-policy"),
        hit("x"),
        hit("y"),
        hit("z"),
        hit("w"),
        hit("shipping-rates"),
    ]
    assert evaluate_row(r, both).all_documents_found
    assert not evaluate_row(r, only_one).all_documents_found


def test_distractor_ahead_of_the_relevant_chunk_is_flagged():
    r = row(
        relevant_document_ids=["shipping-rates"], distractor_document_ids=["shipping-rates-legacy"]
    )
    assert evaluate_row(r, [hit("shipping-rates-legacy"), hit("shipping-rates")]).distractor_first
    assert not evaluate_row(
        r, [hit("shipping-rates"), hit("shipping-rates-legacy")]
    ).distractor_first


def test_distractor_with_no_relevant_result_counts_as_first():
    r = row(
        relevant_document_ids=["shipping-rates"], distractor_document_ids=["shipping-rates-legacy"]
    )
    assert evaluate_row(r, [hit("shipping-rates-legacy")]).distractor_first


def test_distractor_is_not_applicable_without_distractors():
    assert evaluate_row(row(), [hit("returns-policy")]).distractor_first is None


def test_top_score_is_zero_without_results():
    assert evaluate_row(row(), []).top_score == 0.0


def test_summary_computes_recall_and_mrr_over_answerable_rows_only():
    outcomes = [
        evaluate_row(row(id="a"), [hit("returns-policy")]),  # rank 1
        evaluate_row(row(id="b"), [hit("x"), hit("returns-policy")]),  # rank 2
        evaluate_row(row(id="c"), [hit("x")]),  # miss
        evaluate_row(
            row(id="d", relevant_document_ids=[], expect_no_answer=True), [hit("x", score=0.3)]
        ),
    ]
    summary = summarize(outcomes)
    assert summary["questions"] == 3
    assert summary["recall@5"] == pytest.approx(2 / 3)
    assert summary["mrr"] == pytest.approx((1 + 0.5 + 0) / 3)
    assert summary["top_score"]["no_answer"]["median"] == 0.3


def test_summary_splits_results_by_category():
    outcomes = [
        evaluate_row(row(id="a", category="returns"), [hit("returns-policy")]),
        evaluate_row(row(id="b", category="shipping"), [hit("x")]),
    ]
    by_category = summarize(outcomes)["by_category"]
    assert by_category["returns"]["recall@5"] == 1.0
    assert by_category["shipping"]["recall@5"] == 0.0


def write_split(directory, name, rows):
    path = directory / f"retrieval.{name}.jsonl"
    path.write_text("\n".join(json.dumps(r.model_dump()) for r in rows) + "\n")


def test_real_dataset_loads_and_matches_the_knowledge_base():
    assert len(load_dataset("dev")) == 59
    assert len(load_dataset("test")) == 23


def test_valid_files_load(tmp_path):
    write_split(tmp_path, "dev", [row(id="r1", question="One?")])
    write_split(tmp_path, "test", [row(id="r2", question="Two?")])
    assert [r.id for r in load_dataset("dev", KNOWN, tmp_path)] == ["r1"]
    assert [r.id for r in load_dataset("test", KNOWN, tmp_path)] == ["r2"]


def test_id_or_question_repeated_across_splits_is_rejected(tmp_path):
    write_split(tmp_path, "dev", [row(id="r1", question="One?")])
    write_split(tmp_path, "test", [row(id="r1", question="one? ")])
    with pytest.raises(InvalidDataset) as error:
        load_dataset("dev", KNOWN, tmp_path)
    assert "duplicate id" in str(error.value) and "duplicate question" in str(error.value)


def test_unknown_document_id_is_rejected(tmp_path):
    write_split(tmp_path, "dev", [row(relevant_document_ids=["no-such-article"])])
    write_split(tmp_path, "test", [])
    with pytest.raises(InvalidDataset, match="unknown document id"):
        load_dataset("dev", KNOWN, tmp_path)


def test_unknown_or_missing_field_is_rejected(tmp_path):
    good = row().model_dump()
    write_split(tmp_path, "test", [])
    for bad in (good | {"extra": 1}, {k: v for k, v in good.items() if k != "note"}):
        (tmp_path / "retrieval.dev.jsonl").write_text(json.dumps(bad) + "\n")
        with pytest.raises(InvalidDataset, match="retrieval.dev.jsonl:1"):
            load_dataset("dev", KNOWN, tmp_path)


def test_no_answer_flag_must_match_the_relevant_documents(tmp_path):
    write_split(tmp_path, "dev", [row(expect_no_answer=True)])
    write_split(tmp_path, "test", [])
    with pytest.raises(InvalidDataset, match="expect_no_answer"):
        load_dataset("dev", KNOWN, tmp_path)
