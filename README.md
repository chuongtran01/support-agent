# Northwind Support Agent

A customer support agent for a fictional outdoor-gear retailer. Hybrid retrieval over a
messy knowledge base, scoped action tools, a code-level policy engine, human approval for
risky refunds, and an eval harness that measures all of it.

## Layout

| Path | Purpose |
|---|---|
| `backend/` | FastAPI app, agent loop, retrieval, tools, policy engine |
| `frontend/` | Next.js chat UI and approval queue |
| `db/init/` | Postgres schema (pgvector + full-text) |
| `data/` | Knowledge-base documents and `traps.yaml` ground truth |
| `evals/` | Eval harness and datasets |

## Quick start

```sh
cp .env.example .env
make up
make install
```

Status: skeleton only.
