.PHONY: up down db-reset migrate install test lint eval-retrieval

up:
	docker compose up -d db

down:
	docker compose down

db-reset:
	docker compose down -v && docker compose up -d --wait db && $(MAKE) migrate

migrate:
	cd backend && .venv/bin/alembic upgrade head

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:
	cd backend && .venv/bin/pytest

lint:
	cd backend && .venv/bin/ruff check .

eval-retrieval:
	cd backend && .venv/bin/python ../evals/retrieval_eval.py $(ARGS)
