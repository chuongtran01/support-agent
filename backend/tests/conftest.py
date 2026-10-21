"""Database fixtures: a throwaway ``support_test`` database built by the real migrations.

Tests that use the ``session`` fixture are skipped when Postgres is not reachable, so the
rest of the suite runs anywhere.
"""

import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.seed import BUSINESS_TABLES
from tests.api_world import api  # noqa: F401  (fixture)
from tests.tool_world import world  # noqa: F401  (fixture)

BACKEND = Path(__file__).resolve().parents[1]
TEST_DATABASE = "support_test"


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = make_url(settings.database_url)
    test_url = url.set(database=TEST_DATABASE).render_as_string(hide_password=False)
    admin_url = url.set(drivername="postgresql", database="postgres")
    try:
        admin = psycopg.connect(
            admin_url.render_as_string(hide_password=False), autocommit=True, connect_timeout=3
        )
    except psycopg.OperationalError:
        pytest.skip("Postgres is not reachable")
    with admin:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DATABASE} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {TEST_DATABASE}")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=os.environ | {"DATABASE_URL": test_url},
        check=True,
        capture_output=True,
    )
    return test_url


@pytest_asyncio.fixture
async def session(test_database_url):
    engine = create_async_engine(test_database_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {BUSINESS_TABLES}, documents CASCADE"))
    await engine.dispose()
