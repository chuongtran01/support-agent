.PHONY: up down db-reset migrate seed install test lint eval-retrieval eval-agent frontend-install frontend-dev frontend-build frontend-check frontend-e2e frontend-demo api-types api-types-check

up:
	docker compose up -d db

down:
	docker compose down

db-reset:
	docker compose down -v && docker compose up -d --wait db && $(MAKE) migrate

migrate:
	cd backend && .venv/bin/alembic upgrade head

seed:
	cd backend && .venv/bin/python -m app.seed

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:
	cd backend && .venv/bin/pytest

lint:
	cd backend && .venv/bin/ruff check .

eval-retrieval:
	cd backend && .venv/bin/python ../evals/retrieval_eval.py $(ARGS)

eval-agent:
	cd backend && .venv/bin/python ../evals/agent_eval.py $(ARGS)

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend-check:
	cd frontend && npm run typecheck && npm run lint && npm test

# Browser tests (Playwright) with the backend mocked. First time: cd frontend && npx playwright install chromium
frontend-e2e:
	cd frontend && npm run e2e

# The demo script in a real browser against the REAL backend and model (uses the OpenAI key and
# decides a refund: `make seed` before and after). Needs a backend on :8001 that allows :3101:
#   cd backend && CORS_ORIGINS=http://localhost:3101 .venv/bin/uvicorn app.main:app --port 8001
frontend-demo:
	cd frontend && npm run demo

# The frontend's API types come from the backend's OpenAPI spec, so they cannot drift.
api-types:
	cd backend && .venv/bin/python -c "import json; from app.main import app; print(json.dumps(app.openapi()))" > ../frontend/openapi.json
	cd frontend && npx openapi-typescript openapi.json -o src/api/schema.d.ts && rm openapi.json

api-types-check:
	cp frontend/src/api/schema.d.ts frontend/schema.committed.d.ts
	$(MAKE) api-types
	diff frontend/schema.committed.d.ts frontend/src/api/schema.d.ts; status=$$?; rm frontend/schema.committed.d.ts; exit $$status
