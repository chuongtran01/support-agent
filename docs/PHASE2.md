# Phase 2: Knowledge base and retrieval

**Status:** in progress (steps 1 to 6 of 7 done; step 7 started: dev results below, test split and baseline not yet recorded)
**Gate:** recall@5 baseline set

## Goal

Build the knowledge base (including the deliberate traps), ingest it into Postgres, and make hybrid retrieval measurable. By the end, a retrieval eval reports recall@5 and MRR for keyword-only, vector-only and hybrid search, so later changes (chunking, reranker) can be judged against numbers.

The evals are the spec: the retrieval dataset is written before the retrieval code it measures.

## Deliverables

- [x] **Policy fact sheet**: `data/POLICY_FACTS.md`. Single source of truth for return window, final-sale rules, refund limits, membership tiers and shipping prices; every document and later seed data must agree with it
- [x] **Trap documents**: `data/kb/`. Eight hand-written articles: five carry the conflict, stale, buried-exception and injection traps, and three are supporting articles
- [x] **Filler articles**: `data/kb/`. 43 ordinary articles on returns, shipping, warranty, membership, billing, support and product care, written directly as Markdown (no generation script or API call) and checked against the fact sheet
- [x] **Trap manifest**: `data/traps.yaml`. `documents` lists filled in; each trap points at real document ids (the no-answer trap lists none on purpose)
- [x] **Retrieval eval dataset**: `evals/datasets/retrieval.dev.jsonl` (59 rows) and `retrieval.test.jsonl` (23 rows). 82 questions with the documents that should be returned
- [x] **Chunking and document loading**: `backend/app/retrieval/chunking.py` and `documents.py`. Heading-path chunking with paragraph, line and sentence fallbacks, and a front-matter parser that validates every knowledge-base file
- [x] **SQLAlchemy and Alembic**: `backend/app/models.py`, `backend/migrations/`. Async SQLAlchemy 2.0 models for all nine tables and an Alembic migration replace `db/init/001_schema.sql`; `make migrate` builds the schema
- [x] **Ingestion script**: `backend/app/retrieval/ingest.py` and `embeddings.py`. Two stages: files into `documents`, then documents into embedded `chunks`. All 51 documents and 174 chunks are loaded; a second run writes nothing
- [x] **Retrieval module**: `backend/app/retrieval/search.py`. Keyword, vector and hybrid search behind one `search()` function, with reciprocal rank fusion and a marked seam for the phase 6 reranker
- [x] **Retrieval eval runner**: `evals/retrieval_eval.py` (`make eval-retrieval`). Validates the dataset, scores each mode on the chosen split and writes a results file
- [x] **Tests**: `backend/tests/`. 63 passing without a database or API key: chunking (including a pass over the real knowledge base), document loading, ingestion sync planning, models, reciprocal rank fusion, the health endpoint, and the eval runner's metrics and dataset validation

## Knowledge base

### Document format

Each document is a Markdown file in `data/kb/`. The front matter maps onto the `documents` columns `id`, `title`, `status`, `updated_at` and `source`; the body becomes `content`, and ingestion computes `content_hash` from the file:

```markdown
---
id: returns-policy
title: Returns Policy
status: current        # current | stale | draft
updated_at: 2026-01-15
source: help-center/returns
---
```

Front matter is YAML, so a value containing a colon must be quoted (`title: "Plus Returns: 60 Days"`); the loader rejects a file that does not parse.

Filler articles are marked `origin: filler` in their front matter (not stored in the database) so they can be told apart from the trap documents.

### Traps

Each trap maps to one eval case in `traps.yaml`. Document contents are settled after the fact sheet exists.

| Trap | Documents to write | What retrieval must support |
| --- | --- | --- |
| Conflicting articles | Two `current` articles that state different return windows | Both surface for the question, with metadata, so the agent can see the conflict |
| Stale page | A `stale` shipping page with an old price next to the `current` page | The current page ranks above the stale one, or status is visible |
| Buried exception | A long article with an exception several sections down | Chunking keeps the exception retrievable on its own |
| No answer | Nothing (the absence is the trap) | Top scores stay low enough to detect "no relevant source" |
| Document injection | An article containing hidden instructions | Ingestion stores it as data; the injection is handled in the agent phase |

## Ingestion

Two stages, run with `python -m app.retrieval.ingest [--stage documents|chunks|all] [--rebuild-all]` from `backend/`.

**Stage 1: files into `documents`** (no API key needed)

1. Read each Markdown file, split off its front matter, and hash the file.
2. Compare with the stored `content_hash`. New files are added; files whose hash changed are updated and lose their chunks; stored documents with no file are removed (their chunks go with them).

**Stage 2: documents into `chunks`**

1. Take every document that has no chunks (all documents with `--rebuild-all`, for example after a chunking change).
2. Split each stored `content` by heading. Each chunk stores `heading_path` (for example `Returns Policy > Exceptions > Final sale`) and `content`; a section that runs long is split on paragraph boundaries and the heading path is repeated on every piece.
3. Embed `heading_path` plus `content` with `text-embedding-3-small`, in batches of 100.
4. Replace the document's chunks, so re-running ingestion changes nothing.

Stage 2 builds chunks from the database, not the files, so the files are only read in stage 1. Embedding the whole corpus (174 chunks) needs `OPENAI_API_KEY` and costs a fraction of a cent.

## Retrieval

| Mode | How it works |
| --- | --- |
| Keyword | `websearch_to_tsquery` against `chunks.tsv`, with the AND between terms replaced by OR, ranked with `ts_rank_cd` |
| Vector | Cosine distance on `chunks.embedding` using the HNSW index |
| Hybrid | Reciprocal rank fusion of the keyword and vector rankings |

With `websearch_to_tsquery` alone every term must appear in the chunk, and 41 of the 59 dev questions returned nothing, so the keyword query turns the ANDs into ORs and lets `ts_rank_cd` order the chunks by how many terms they match.

Every result carries `document_id`, `title`, `status`, `updated_at`, `heading_path`, `content` and its scores. The search function takes a `mode` argument so the eval can compare modes with the same code the agent will use later.

The reranker is not part of this phase. `search.py` leaves a clear seam after fusion where it can be added in phase 6.

## Retrieval eval

- **Dataset.** Two files, one JSON object per line: `evals/datasets/retrieval.dev.jsonl` (59 rows) and `evals/datasets/retrieval.test.jsonl` (23 rows). Every row has the same nine fields, with an empty value where one does not apply: `id`, `category`, `question`, `relevant_document_ids`, `relevant_heading_paths` (used for the buried exception), `distractor_document_ids` (the stale page must not outrank the current one), `expect_no_answer` (false by default), `trap_id` (links to `traps.yaml`, null for ordinary questions) and `note` (null by default). Ids are unique across both files. The eval runner validates each row against one Pydantic model and fails on a missing or unknown field, an unknown document id, or an id or question that appears in both files.
- **Coverage.** 48 ordinary questions across returns, shipping, warranty, membership, billing, support and product care; 8 messy questions (typos, fragments); 5 questions that need two articles; 17 trap questions (the 5 originals plus 3 variants each for the conflict, buried-exception, stale-page and injection traps); 4 off-topic questions. The 5 rows flagged `expect_no_answer` are the price-match trap plus the 4 off-topic ones.
- **Dev and test.** The dev file is for tuning chunking and fusion, and it is the only file the runner reads by default. The test file is held out and is read only with an explicit `--split test` flag; its scores are the headline numbers. The four traps with variants each have one test row, the price-match trap is dev only (one off-topic no-answer row is in test instead), and every third row of the other categories is held out. `r001` to `r057` were written first; `r058` to `r082` were added before any baseline was run. If someone writes extra questions without reading the articles, they go in the test file.
- **Metrics.** Each question retrieves 10 chunks. A chunk is relevant if it is in a relevant document and, when the row names heading paths, under one of them. Computed over answerable questions:
  - **recall@5**: the share of questions with a relevant chunk in the top 5.
  - **MRR**: the mean of 1 / rank of the first relevant chunk (0 when none is in the top 10).
  - **all docs @5**: the share of questions where every relevant document has a relevant chunk in the top 5; it differs from recall@5 only on the questions that need two articles.
  - **distractor first**: among rows with a distractor, the share where a distractor chunk ranks above the first relevant one (lower is better).
- **Report.** One row per mode (keyword, vector, hybrid) plus recall by category, printed and written to `evals/results/` (git-ignored) with every question's ranked results. Run `make eval-retrieval`; pass `ARGS="--split test"` for the held-out split or `ARGS="--modes keyword"` to run without an API key. Numbers go in the README once they are stable.
- **No-answer threshold.** For the no-answer questions, record the top score per mode. The distribution against answerable questions suggests the threshold the agent uses to say "I don't know"; the agent phase applies it.

### Dev results so far

First run on the dev split (59 questions, 54 answerable), before any tuning. The test split has not been run. Differences of one or two questions are within noise at this size.

| Mode | recall@5 | MRR | all docs @5 | Distractor first |
| --- | --- | --- | --- | --- |
| Keyword | 0.891 | 0.791 | 0.727 | 0 of 3 |
| Vector | 0.964 | 0.890 | 0.855 | 2 of 3 |
| Hybrid | 0.945 | 0.895 | 0.818 | 0 of 3 |

- Vector search beats keyword mainly on the messy and support questions (messy: 0.50 keyword, 0.83 vector and hybrid).
- Hybrid does not beat vector on recall@5; it has a similar MRR and keeps the stale shipping page below the current one, which vector does not.
- No-answer top scores for vector have a median of 0.375 against 0.633 for answerable questions, but the ranges overlap (no-answer maximum 0.514, answerable minimum 0.396), so no threshold separates them cleanly. Hybrid's scores are rank-based and say nothing about relevance.

## Build order

- [x] 1. Write `POLICY_FACTS.md` and review it.
- [x] 2. Write the trap documents by hand and fill in `traps.yaml`.
- [x] 3. Write the filler articles and check them against the fact sheet.
- [x] 4. Write the retrieval eval dataset.
- [x] 5. Implement chunking and ingestion, with unit tests for chunking.
- [x] 6. Implement keyword, vector and hybrid search.
- [ ] 7. Run the eval, tune chunk size once if the buried-exception case fails, and record the baseline. The dev eval has run for all three modes with no tuning; the baseline still needs a decision on tuning and a run of the test split.

## Gate check

- [x] **Ingestion**: `documents` and `chunks` populated; re-running changes nothing
- [x] **Trap coverage**: every `traps.yaml` entry except the no-answer trap lists real document ids
- [x] **Retrieval eval**: runs for all three modes and writes recall@5 and MRR
- [ ] **Baseline**: numbers recorded in this file or the README
- [x] **Tests**: `pytest` passes without a database or API key

## Open questions

- **Policy numbers.** Settled: approved in `POLICY_FACTS.md` (step 1).
- **Chunk size.** Start with heading-based chunks capped at a few hundred tokens and adjust only if the eval shows a problem.
- **Fusion constant.** Reciprocal rank fusion starts with the common default (`k = 60`); tune only with eval evidence.

## Not in this phase

- Cross-encoder reranker (phase 6)
- Agent loop, escalation behavior and the "I don't know" threshold in use (phase 4)
- Prompt-injection handling (phase 4 and the phase 6 injection suite)
- Seed data for customers, orders and refunds (phase 3)
