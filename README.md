# Northwind Support Agent

A customer support agent for a fictional outdoor-gear retailer. Hybrid retrieval over a
messy knowledge base, scoped action tools, a code-level policy engine, human approval for
risky refunds, and an eval harness that measures all of it.

## Layout

| Path | Purpose |
|---|---|
| `backend/` | FastAPI app, agent loop, retrieval, tools, policy engine |
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
