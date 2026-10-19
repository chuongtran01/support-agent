"""Retrieval eval: recall@5, MRR and distractor ranking for keyword, vector and hybrid search.

Run from ``backend/`` (``make eval-retrieval``):

    python ../evals/retrieval_eval.py [--split dev|test] [--modes keyword vector hybrid]

The dev split is the default and is the one to tune against. The test split is held out:
read its numbers, do not tune on them. Vector and hybrid modes embed each question once
(a few cents at most); keyword mode needs no API key.
"""

import argparse
import asyncio
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import get_args

from pydantic import BaseModel, ConfigDict, ValidationError

from app.db import SessionLocal, engine
from app.retrieval.embeddings import OpenAIEmbedder
from app.retrieval.search import Mode, SearchResult, search

ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"
RESULTS = ROOT / "results"
KB_DIRECTORY = ROOT.parent / "data" / "kb"

SPLITS = ("dev", "test")
MODES: tuple[str, ...] = get_args(Mode)
RECALL_K = 5
DEPTH = 10  # results retrieved per question; recall uses the first RECALL_K, MRR all of them


class InvalidDataset(ValueError):
    """The dataset files are malformed or inconsistent."""


class EvalRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    question: str
    relevant_document_ids: list[str]
    relevant_heading_paths: list[str]
    distractor_document_ids: list[str]
    expect_no_answer: bool
    trap_id: str | None
    note: str | None


def known_document_ids(directory: Path = KB_DIRECTORY) -> set[str]:
    return {path.stem for path in directory.glob("*.md")}


def _read(path: Path) -> list[EvalRow]:
    rows = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(EvalRow.model_validate_json(line))
        except ValidationError as error:
            raise InvalidDataset(f"{path.name}:{number}: {error}") from error
    return rows


def _problems(rows: list[EvalRow], known: set[str]) -> list[str]:
    problems = []
    for label, values in (
        ("id", [r.id for r in rows]),
        ("question", [r.question.strip().casefold() for r in rows]),
    ):
        seen: set[str] = set()
        for value in values:
            if value in seen:
                problems.append(f"duplicate {label}: {value!r}")
            seen.add(value)
    for row in rows:
        for document_id in row.relevant_document_ids + row.distractor_document_ids:
            if document_id not in known:
                problems.append(f"{row.id}: unknown document id {document_id!r}")
        if row.relevant_heading_paths and not row.relevant_document_ids:
            problems.append(f"{row.id}: heading paths without relevant documents")
        if row.expect_no_answer == bool(row.relevant_document_ids):
            problems.append(f"{row.id}: expect_no_answer must match having no relevant documents")
    return problems


def load_dataset(
    split: str, known: set[str] | None = None, directory: Path = DATASETS
) -> list[EvalRow]:
    """Load one split. Both splits are read so ids and questions are unique across them."""
    by_split = {name: _read(directory / f"retrieval.{name}.jsonl") for name in SPLITS}
    everything = [row for rows in by_split.values() for row in rows]
    problems = _problems(everything, known_document_ids() if known is None else known)
    if problems:
        raise InvalidDataset("\n".join(problems))
    return by_split[split]


def is_relevant(row: EvalRow, result: SearchResult) -> bool:
    """A chunk is relevant if it is in a relevant document and, when the row names heading
    paths (the buried exception), under one of them."""
    if result.document_id not in row.relevant_document_ids:
        return False
    return not row.relevant_heading_paths or result.heading_path in row.relevant_heading_paths


@dataclass(frozen=True)
class RowOutcome:
    id: str
    category: str
    expect_no_answer: bool
    first_rank: int | None  # 1-based rank of the first relevant chunk
    all_documents_found: bool  # every relevant document has a relevant chunk in the top K
    distractor_first: bool | None  # None when the row has no distractors
    top_score: float
    ranked: list[dict]


def evaluate_row(row: EvalRow, results: list[SearchResult]) -> RowOutcome:
    first_rank = next(
        (rank for rank, result in enumerate(results, start=1) if is_relevant(row, result)), None
    )
    top = results[:RECALL_K]
    found = {r.document_id for r in top if is_relevant(row, r)}
    distractor_first = None
    if row.distractor_document_ids:
        first_distractor = next(
            (
                rank
                for rank, result in enumerate(results, start=1)
                if result.document_id in row.distractor_document_ids
            ),
            None,
        )
        distractor_first = first_distractor is not None and (
            first_rank is None or first_distractor < first_rank
        )
    return RowOutcome(
        id=row.id,
        category=row.category,
        expect_no_answer=row.expect_no_answer,
        first_rank=first_rank,
        all_documents_found=found >= set(row.relevant_document_ids),
        distractor_first=distractor_first,
        top_score=results[0].score if results else 0.0,
        ranked=[
            {"document_id": r.document_id, "heading_path": r.heading_path, "score": r.score}
            for r in results
        ],
    )


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _spread(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "median": 0.0, "max": 0.0}
    return {"min": min(values), "median": statistics.median(values), "max": max(values)}


def _quality(outcomes: list[RowOutcome]) -> dict:
    return {
        "questions": len(outcomes),
        f"recall@{RECALL_K}": _mean(
            [o.first_rank is not None and o.first_rank <= RECALL_K for o in outcomes]
        ),
        "mrr": _mean([1 / o.first_rank if o.first_rank else 0.0 for o in outcomes]),
        f"all_documents@{RECALL_K}": _mean([o.all_documents_found for o in outcomes]),
    }


def summarize(outcomes: list[RowOutcome]) -> dict:
    answerable = [o for o in outcomes if not o.expect_no_answer]
    no_answer = [o for o in outcomes if o.expect_no_answer]
    with_distractors = [o for o in answerable if o.distractor_first is not None]
    categories = sorted({o.category for o in answerable})
    return {
        **_quality(answerable),
        "distractor_first": {
            "rows": len(with_distractors),
            "rate": _mean([bool(o.distractor_first) for o in with_distractors]),
        },
        "top_score": {
            "answerable": _spread([o.top_score for o in answerable]),
            "no_answer": _spread([o.top_score for o in no_answer]),
        },
        "by_category": {
            category: _quality([o for o in answerable if o.category == category])
            for category in categories
        },
    }


class QuestionEmbedder:
    """Serves vectors computed up front, so each question is embedded once for all modes."""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[text] for text in texts]


async def run(rows: list[EvalRow], modes: list[str]) -> dict[str, list[RowOutcome]]:
    embedder = None
    if any(mode != "keyword" for mode in modes):
        questions = [row.question for row in rows]
        embedder = QuestionEmbedder(dict(zip(questions, await OpenAIEmbedder().embed(questions))))
    outcomes: dict[str, list[RowOutcome]] = {}
    async with SessionLocal() as session:
        for mode in modes:
            outcomes[mode] = [
                evaluate_row(row, await search(session, row.question, mode, DEPTH, embedder))
                for row in rows
            ]
    return outcomes


def print_report(summaries: dict[str, dict]) -> None:
    k = RECALL_K
    print(
        f"\n{'mode':<9} {'recall@' + str(k):>9} {'MRR':>7} {'all docs':>9} {'distractor first':>17}"
    )
    for mode, s in summaries.items():
        d = s["distractor_first"]
        print(
            f"{mode:<9} {s[f'recall@{k}']:>9.3f} {s['mrr']:>7.3f} "
            f"{s[f'all_documents@{k}']:>9.3f} {d['rate']:>11.2f} of {d['rows']:<2}"
        )
    print("\nrecall@5 by category")
    categories = next(iter(summaries.values()))["by_category"]
    print(f"{'category':<13}{'n':>4} " + " ".join(f"{mode:>8}" for mode in summaries))
    for category, q in categories.items():
        scores = " ".join(
            f"{s['by_category'][category][f'recall@{k}']:>8.2f}" for s in summaries.values()
        )
        print(f"{category:<13}{q['questions']:>4} {scores}")
    print("\ntop score, median (min to max)")
    for mode, s in summaries.items():
        a, n = s["top_score"]["answerable"], s["top_score"]["no_answer"]
        print(
            f"{mode:<9} answerable {a['median']:.3f} ({a['min']:.3f} to {a['max']:.3f})"
            f"   no-answer {n['median']:.3f} ({n['min']:.3f} to {n['max']:.3f})"
        )


async def main(split: str, modes: list[str]) -> None:
    rows = load_dataset(split)
    print(f"{split} split: {len(rows)} questions, modes: {', '.join(modes)}")
    try:
        outcomes = await run(rows, modes)
    finally:
        await engine.dispose()
    summaries = {mode: summarize(o) for mode, o in outcomes.items()}
    print_report(summaries)

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS / f"retrieval-{split}-{stamp}.json"
    report = {
        "split": split,
        "created_at": stamp,
        "depth": DEPTH,
        "summary": summaries,
        "rows": {mode: [asdict(o) for o in rows_] for mode, rows_ in outcomes.items()},
    }
    path.write_text(json.dumps(report, indent=2))
    print(f"\nwrote {path.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retrieval eval")
    parser.add_argument("--split", choices=SPLITS, default="dev")
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    args = parser.parse_args()
    asyncio.run(main(args.split, args.modes))
