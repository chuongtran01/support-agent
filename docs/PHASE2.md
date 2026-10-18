# Phase 2: Knowledge base and retrieval

**Status:** in progress (steps 1 to 3 of 7 done)
**Gate:** recall@5 baseline set

## Goal

Build the knowledge base (including the deliberate traps), ingest it into Postgres, and make hybrid retrieval measurable. By the end, a retrieval eval reports recall@5 and MRR for keyword-only, vector-only and hybrid search, so later changes (chunking, reranker) can be judged against numbers.

The evals are the spec: the retrieval dataset is written before the retrieval code it measures.

## Deliverables

- [x] **Policy fact sheet**: `data/POLICY_FACTS.md`. Single source of truth for return window, final-sale rules, refund limits, membership tiers and shipping prices; every document and later seed data must agree with it
- [x] **Trap documents**: `data/kb/`. Eight hand-written articles: five carry the conflict, stale, buried-exception and injection traps, and three are supporting articles
- [x] **Filler articles**: `data/kb/`. 43 ordinary articles on returns, shipping, warranty, membership, billing, support and product care, written directly as Markdown (no generation script or API call) and checked against the fact sheet
- [x] **Trap manifest**: `data/traps.yaml`. `documents` lists filled in; each trap points at real document ids (the no-answer trap lists none on purpose)
- [ ] **Retrieval eval dataset**: `evals/datasets/retrieval.jsonl`. Questions with the chunks or documents that should be returned
- [ ] **Ingestion script**: `backend/app/retrieval/ingest.py`. Parses documents, chunks by heading, embeds, upserts into `documents` and `chunks`
- [ ] **Retrieval module**: `backend/app/retrieval/search.py`. Keyword, vector and hybrid search behind one interface
- [ ] **Retrieval eval runner**: `evals/retrieval_eval.py`. Computes recall@5 and MRR per search mode and writes a results file
- [ ] **Tests**: `backend/tests/`. Chunking and fusion unit tests that need no database or API key

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

1. Read each Markdown file, split off its front matter, and hash the file. Skip any document whose `content_hash` already matches the database.
2. Split by heading. Each chunk stores `heading_path` (for example `Returns Policy > Exceptions > Final sale`) and `content`.
3. Keep chunks small. A section that runs long is split on paragraph boundaries, and the heading path is repeated on every piece.
4. Embed `heading_path` plus `content` with `text-embedding-3-small`, in batches.
5. Upsert the document row (including the full `content` and `content_hash`), then replace its chunks, so re-running ingestion is idempotent.

Embedding the whole corpus needs `OPENAI_API_KEY` and costs very little at this size.

## Retrieval

| Mode | How it works |
| --- | --- |
| Keyword | `websearch_to_tsquery` against `chunks.tsv`, ranked with `ts_rank_cd` |
| Vector | Cosine distance on `chunks.embedding` using the HNSW index |
| Hybrid | Reciprocal rank fusion of the keyword and vector rankings |

Every result carries `document_id`, `title`, `status`, `updated_at`, `heading_path`, `content` and its scores. The search function takes a `mode` argument so the eval can compare modes with the same code the agent will use later.

The reranker is not part of this phase. `search.py` leaves a clear seam after fusion where it can be added in phase 6.

## Retrieval eval

- **Dataset.** `evals/datasets/retrieval.jsonl`, one JSON object per line: `question`, `relevant_document_ids`, and `relevant_heading_paths` where a specific section matters. Cover ordinary questions for each topic plus one question per trap.
- **Metrics.** recall@5 (is a relevant chunk in the top 5) and MRR (how high the first relevant chunk ranks).
- **Report.** One row per mode (keyword, vector, hybrid), written to `evals/results/` (git-ignored) and summarized in the README once numbers are stable.
- **No-answer threshold.** For the no-answer questions, record the top score per mode. The distribution against answerable questions suggests the threshold the agent uses to say "I don't know"; the agent phase applies it.

## Build order

- [x] 1. Write `POLICY_FACTS.md` and review it.
- [x] 2. Write the trap documents by hand and fill in `traps.yaml`.
- [x] 3. Write the filler articles and check them against the fact sheet.
- [ ] 4. Write the retrieval eval dataset.
- [ ] 5. Implement chunking and ingestion, with unit tests for chunking.
- [ ] 6. Implement keyword, vector and hybrid search.
- [ ] 7. Run the eval, tune chunk size once if the buried-exception case fails, and record the baseline.

## Gate check

- [ ] **Ingestion**: `documents` and `chunks` populated; re-running changes nothing
- [x] **Trap coverage**: every `traps.yaml` entry except the no-answer trap lists real document ids
- [ ] **Retrieval eval**: runs for all three modes and writes recall@5 and MRR
- [ ] **Baseline**: numbers recorded in this file or the README
- [ ] **Tests**: `pytest` passes without a database or API key

## Open questions

- **Policy numbers.** Settled: approved in `POLICY_FACTS.md` (step 1).
- **Chunk size.** Start with heading-based chunks capped at a few hundred tokens and adjust only if the eval shows a problem.
- **Fusion constant.** Reciprocal rank fusion starts with the common default (`k = 60`); tune only with eval evidence.

## Not in this phase

- Cross-encoder reranker (phase 6)
- Agent loop, escalation behavior and the "I don't know" threshold in use (phase 4)
- Prompt-injection handling (phase 4 and the phase 6 injection suite)
- Seed data for customers, orders and refunds (phase 3)
