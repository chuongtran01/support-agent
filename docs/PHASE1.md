# Phase 1: Foundation

**Status:** done
**Gate:** schema loads, `/health` ok

## Goal

Stand up the repo, the database and a minimal backend so later phases have a place to land. No agent logic yet.

## Deliverables

| Deliverable | Location | Notes |
| --- | --- | --- |
| Database container | `docker-compose.yml` | `pgvector/pgvector:pg16`, host port 5433, named volume `pgdata`, healthcheck |
| Schema | `backend/app/models.py`, `backend/migrations/` | Nine tables, created by the Alembic migration `0001` (originally `db/init/001_schema.sql`) |
| Backend project | `backend/pyproject.toml` | Python 3.12+, FastAPI, SQLAlchemy 2.0 (async), Alembic, psycopg 3, pgvector, OpenAI, Langfuse; `dev` extras for pytest and ruff |
| Settings | `backend/app/config.py` | Reads `.env`; database URL, model names, refund approval threshold |
| DB access | `backend/app/db.py` | Async SQLAlchemy engine and session factory; `get_session` dependency |
| API stub | `backend/app/main.py` | `GET /health` runs `SELECT 1` against the pool |
| Package layout | `backend/app/{agent,retrieval,tools,policy,api}/` | Empty packages, filled in later phases |
| Trap manifest | `data/traps.yaml` | Five trap entries (conflict, stale, buried, no answer, injection) with empty `documents` lists |
| Env template | `.env.example` | Database, OpenAI, model names, refund threshold, Langfuse |
| Dev commands | `Makefile` | `up`, `down`, `db-reset`, `migrate`, `install`, `test`, `lint` |
| Placeholders | `frontend/`, `evals/datasets/`, `data/kb/` | Directories only |

## Schema

| Group | Tables |
| --- | --- |
| Business data | `customers`, `orders`, `order_items`, `refunds`, `tickets` |
| Conversations | `chat_sessions`, `chat_messages` |
| Knowledge base | `documents`, `chunks` |

Design points that later phases depend on:

- `customers.role` is `customer` or `approver`, so the approval page can use a separate role.
- `order_items.is_final_sale` and `orders.delivered_at` feed the return rules.
- `refunds.status` is `pending`, `approved`, `rejected` or `completed`; `decided_by` and `decided_at` record the approver.
- `documents.status` is `current`, `stale` or `draft`, so retrieval can prefer current sources.
- `documents.content` holds the full Markdown body (without front matter) so chunks can be rebuilt and whole articles shown without the source files; `documents.content_hash` lets ingestion skip unchanged documents and avoid re-embedding them.
- `chunks.embedding` is `vector(1536)` with an HNSW cosine index; `chunks.tsv` is a generated `tsvector` column with a GIN index, built from `heading_path` and `content`.
- `chat_messages.content` is JSONB so tool calls and results can be replayed exactly.

## Run it

```sh
cp .env.example .env
make up          # starts Postgres on localhost:5433
make install     # creates backend/.venv and installs dependencies
make migrate     # applies Alembic migrations
make test
cd backend && .venv/bin/uvicorn app.main:app --reload
curl localhost:8000/health
```

`make db-reset` deletes the volume, starts a fresh database and applies the migrations.

## Decisions and incidents

- **Port 5433.** Another local container (`film-rating-db-1`) already uses 5432, so this project maps to 5433. Override with `DB_PORT` in `.env`.
- **Stale volume.** The first start found an existing `support-agent_pgdata` volume with an unrelated schema, so the init script had not run. The volume was wiped with `docker compose down -v` and recreated from the init script.
- **SQLAlchemy and Alembic.** Phase 1 started with a plain init SQL script. Phase 2 replaced it with SQLAlchemy models and an Alembic migration so the schema can change after data is loaded. The `vector` extension is created in the first migration. Hybrid search will stay raw SQL.
- **Own repository.** The project directory sat inside the home-directory git repo. It now has its own repo at `chuongtran01/support-agent`.

## Gate check

| Check | Result |
| --- | --- |
| Schema loads on an empty volume | Nine tables created by `alembic upgrade head`; `vector` extension present |
| `GET /health` | Returns `{"status": "ok"}` against the fresh database |
| `pytest` | One placeholder test passes |

## Not in this phase

- Authentication and sessions
- Seed data (customers, orders, refunds)
- Knowledge-base documents and ingestion
- Any agent, retrieval or policy code
- Frontend scaffold
