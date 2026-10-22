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
make migrate
```

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

The agent model is `AGENT_MODEL` (default `gpt-6-luna`). That model only accepts function tools on Chat Completions with reasoning turned off, so `AGENT_REASONING_EFFORT` defaults to `none`; change both together if you switch models.

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
```

Each case is played through the real agent and model on freshly seeded data, and the result is checked in code: tools called, refunds made, text in the answer, citations, and that no other customer's data was touched. It calls the OpenAI API (a dev run is about 160k tokens) and **resets the business tables of the database in `DATABASE_URL`**, as `make seed` does. The report gives pass rates overall, per category and per trap, plus tokens per case, and a JSON file with every run (numbered per case) goes to `evals/results/` (git-ignored). The cases and the recorded baseline are described in `docs/PHASE4.md`.

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
