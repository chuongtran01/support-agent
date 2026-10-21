"""Seeded data and a context for each customer, for the tool and approval tests.

The ``world`` fixture is imported in ``conftest.py``, so tests ask for it by name.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.seed import seed
from app.tools.context import ToolContext

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


class World:
    def __init__(self, session, data):
        self.session = session
        self.data = data

    def ctx(self, customer: str) -> ToolContext:
        return ToolContext(self.session, self.data.customers[customer].id, clock=lambda: NOW)

    def item(self, sku: str) -> int:
        return self.data.items[sku].id

    def order(self, key: str) -> int:
        return self.data.orders[key].id

    async def run(self, call):
        """Run one tool call in its own transaction, as the dispatcher will."""
        if self.session.in_transaction():  # a read in the test started one; end it first
            await self.session.commit()
        async with self.session.begin():
            return await call

    async def count(self, model, **where) -> int:
        statement = select(func.count()).select_from(model)
        for column, value in where.items():
            statement = statement.where(getattr(model, column) == value)
        return await self.session.scalar(statement)


@pytest.fixture
async def world(session):
    async with session.begin():
        data = await seed(session, NOW)
    return World(session, data)
