.PHONY: up down db-reset migrate seed install test lint eval-retrieval eval-agent frontend-install frontend-dev frontend-build frontend-check

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
