# Northwind Support Agent

A customer support agent for a fictional outdoor-gear retailer. Hybrid retrieval over a
messy knowledge base, scoped action tools, a code-level policy engine, human approval for
risky refunds, and an eval harness that measures all of it.

## Layout

| Path | Purpose |
|---|---|
| `backend/app/api/` | The HTTP layer: routes, dependencies (login, database, model), server-sent events. The only code that imports FastAPI |
| `backend/app/agent/` | The agent loop, model client, prompt, chat storage, citation checks and the turn runner |
| `backend/app/tools/` | The tools the model can call, their argument models and results |
| `backend/app/policy/` | The refund policy engine: one pure function |
| `backend/app/retrieval/` | Chunking, ingestion and hybrid search |
| `backend/app/orders.py` | A customer's orders with items and refunds, read for both the `lookup_order` tool and `GET /orders` |
| `backend/app/approvals.py` | The approver's side of refunds: the pending list and deciding (not a tool) |
| `frontend/` | Next.js chat UI and approval queue |
| `backend/migrations/` | Alembic migrations for the Postgres schema (pgvector + full-text) |
| `data/` | Knowledge-base documents and `traps.yaml` ground truth |
| `evals/` | Eval harness and datasets |

## Quick start

```sh
cp .env.example .env
make up
make install
make hooks      # git hooks: quick checks before a commit, the tests before a push
make migrate
```

### Git hooks

`make hooks` points git at `.githooks/` (git does not do this on its own after a clone). Each hook only checks the side of the repo that changed:

| Hook | Runs | Takes |
| --- | --- | --- |
| `pre-commit` | backend: `ruff check` and `ruff format --check`. Frontend: type-check and ESLint | a few seconds |
| `pre-push` | backend: `ruff check` and the tests (the database must be running: `make up`). Frontend: type-check, ESLint, unit tests, and the browser tests. Either side: the generated API types still match the backend | about 30 seconds when both changed |

`pre-push` looks at the commits being pushed, so a docs-only push skips it. Skip a hook once with `--no-verify`. The hooks run against your working tree, not the staged snapshot, so stash changes you are not committing. Nothing enforces them (there is no CI yet), so they are a convenience, not a gate.

## Seed the demo data

```sh
make seed    # customers, orders and refunds; safe to re-run, leaves the knowledge base alone
```

Every demo customer logs in with the password in `SEED_PASSWORD` (`demo` by default), for example `alex@example.com` (a customer) or `approver@example.com` (can decide pending refunds).

## Run the API

```sh
cd backend
.venv/bin/uvicorn app.main:app --reload    # http://localhost:8000/docs
```

Log in with `POST /auth/login` (for example `alex@example.com` and the seed password), then send the token as `Authorization: Bearer ...`. A message is `POST /sessions/{id}/messages` and the answer streams back as server-sent events (`curl -N` shows them). The agent needs `OPENAI_API_KEY` and the knowledge base loaded (next section).

The agent calls the OpenAI Responses API with the model in `AGENT_MODEL` (default `gpt-6-luna`) and the reasoning level in `AGENT_REASONING_EFFORT` (`none`, `low`, `medium` or `high`; default `medium`).

## Run the web app

```sh
make frontend-install
make frontend-dev       # http://localhost:3000, with the API above running
```

Open it and click a demo account (the seed password is `demo`). Each account on the login page says what it is set up to show: Alex's boots are refunded at once, Priya's tent waits for approval, and the approver decides it on the Approvals page. The Orders page lists a customer's orders and opens the chat with a question in the box.

```sh
make frontend-check     # type-check, lint and unit tests
make frontend-e2e       # browser tests with the API mocked (first: cd frontend && npx playwright install chromium)
make frontend-demo      # the demo script against the real backend and model (see docs/PHASE5.md)
```

## Tracing

Every turn can be sent to [Langfuse](https://langfuse.com) as one trace: a span for each model call (with tokens, model and time to first token) and each tool call. Put your project's keys and region in `.env` (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, for example `https://us.cloud.langfuse.com`); with the keys empty nothing is sent and nothing changes. The API traces its turns, and `make eval-agent` traces every case with its id, run number and result-file name as metadata. Traces hold the customers' messages, which is fine for the seeded demo data and not for real customers. Tests never trace.

## Load the knowledge base

Set `OPENAI_API_KEY` in `.env` (embeddings use `text-embedding-3-small`), then:

```sh
cd backend
.venv/bin/python -m app.retrieval.ingest                      # both stages
.venv/bin/python -m app.retrieval.ingest --stage documents    # files into documents, no API key
.venv/bin/python -m app.retrieval.ingest --stage chunks       # chunk and embed documents without chunks
.venv/bin/python -m app.retrieval.ingest --rebuild-all        # re-chunk and re-embed everything
```

Re-running is safe: unchanged files are skipped, and only documents without chunks are embedded.

## Run the retrieval eval

```sh
make eval-retrieval                              # dev split, keyword, vector and hybrid
make eval-retrieval ARGS="--modes keyword"       # one mode; keyword needs no API key
make eval-retrieval ARGS="--split test"          # held-out split; do not tune on it
```

It prints recall@5, MRR and the other metrics per mode and writes a JSON file with every question's ranked results to `evals/results/` (git-ignored). Details and the recorded baseline are in `docs/PHASE2.md`.

## Run the agent eval

```sh
make eval-agent                                  # dev split, one run of each case
make eval-agent ARGS="--only a001 a002"          # just these cases
make eval-agent ARGS="--repeat 3"                # three runs of each case, to see which are flaky
make eval-agent ARGS="--split test"              # held-out split; do not tune on it
make eval-agent ARGS="--judge"                   # also have a judge model read the knowledge and trap answers
```

Each case is played through the real agent and model on freshly seeded data, and the result is checked in code: tools called, refunds made, text in the answer, citations, and that no other customer's data was touched. It calls the OpenAI API (a dev run is about 160k tokens) and **resets the business tables of the database in `DATABASE_URL`**, as `make seed` does. The report gives pass rates overall, per category and per trap, plus tokens per case, seconds per message (median and 95th percentile, for the whole turn and until the answer begins) and the cost at the price in `backend/app/pricing.py`. With `--judge` it adds how many answers a judge model found backed by the retrieved sources and responsive to the question; the judge does not change any pass or fail. A JSON file with every run (numbered per case) goes to `evals/results/` (git-ignored). The cases and the recorded baseline are described in `docs/PHASE4.md`.

## Changing the schema

Edit the models in `backend/app/models.py`, then generate and apply a migration:

```sh
cd backend
.venv/bin/alembic revision --autogenerate -m "describe the change"
# read the new file in migrations/versions/ before applying it
make -C .. migrate
.venv/bin/alembic check    # reports any drift between the models and the database
```

Autogenerate compares the models with the live database, so run it against a database that is up to date with the existing migrations. It cannot see extensions or tell a rename from a drop and an add, so edit the generated file when needed.
