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
