"""The ``idempotency_keys`` table: the rule that one key means one call, per customer."""

import os
import subprocess
import sys

import psycopg
import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app import models
from app.config import settings
from tests.conftest import BACKEND

OTHER_DATABASE = "support_migration_test"


def row(customer_id: int, key: str = "k1", **fields) -> models.IdempotencyKey:
    values = {
        "tool": "create_ticket",
        "fingerprint": "f",
        "output": "{}",
        "status": "ok",
    } | fields
    return models.IdempotencyKey(customer_id=customer_id, key=key, **values)


async def test_a_second_row_for_the_same_customer_and_key_is_refused(session, world):
    alex = world.data.customers["alex"].id
    async with session.begin():
        session.add(row(alex))

    with pytest.raises(IntegrityError, match="idempotency_keys_customer_id_key_key"):
        async with session.begin():
            session.add(row(alex, tool="issue_refund"))


async def test_the_same_key_is_allowed_for_another_customer(session, world):
    alex = world.data.customers["alex"].id
    priya = world.data.customers["priya"].id

    async with session.begin():
        session.add_all([row(alex), row(priya)])

    assert await world.count(models.IdempotencyKey) == 2


async def test_another_key_for_the_same_customer_is_allowed(session, world):
    alex = world.data.customers["alex"].id

    async with session.begin():
        session.add_all([row(alex, "k1"), row(alex, "k2")])

    assert await world.count(models.IdempotencyKey) == 2


async def test_a_receipt_is_optional_and_comes_back_as_given(session, world):
    alex = world.data.customers["alex"].id
    async with session.begin():
        session.add_all(
            [row(alex, "none"), row(alex, "some", receipt="A refund of $1.00 has been made.")]
        )

    session.expire_all()
    stored = await session.scalars(select(models.IdempotencyKey))
    rows = {r.key: r.receipt for r in stored}
    assert rows == {"none": None, "some": "A refund of $1.00 has been made."}


async def test_a_row_needs_a_customer_that_exists(session, world):
    with pytest.raises(IntegrityError):
        async with session.begin():
            session.add(row(999_999))


def test_the_migration_adds_the_table_and_takes_it_away(test_database_url):
    """On a database of its own, so the shared test database is not rolled back under the tests."""
    url = make_url(settings.database_url)
    admin = url.set(drivername="postgresql", database="postgres")
    own = url.set(database=OTHER_DATABASE).render_as_string(hide_password=False)
    env = os.environ | {"DATABASE_URL": own}

    def alembic(*args: str) -> None:
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND,
            env=env,
            check=True,
            capture_output=True,
        )

    def exists() -> bool:
        plain = url.set(drivername="postgresql", database=OTHER_DATABASE)
        with psycopg.connect(plain.render_as_string(hide_password=False)) as conn:
            return conn.execute("SELECT to_regclass('idempotency_keys')").fetchone()[0] is not None

    with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {OTHER_DATABASE} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {OTHER_DATABASE}")
    try:
        alembic("upgrade", "head")
        assert exists()
        alembic("downgrade", "0003")
        assert not exists()
        alembic("upgrade", "head")
        assert exists()
    finally:
        with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {OTHER_DATABASE} WITH (FORCE)")
