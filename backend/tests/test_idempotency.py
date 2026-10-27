"""A write tool marked idempotent runs once per customer and key, and a repeat gets the first
answer. The tests mark ``create_ticket`` (a real tool with a visible effect) as idempotent in a
tool table of their own; which real tools are marked is tested with the tools themselves."""

import asyncio
import dataclasses
import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models
from app.tools.context import ToolContext
from app.tools.idempotency import MAX_KEY_LENGTH
from app.tools.registry import TOOLS, ToolOutput, ToolSpec, run_tool
from app.tools.tickets import create_ticket
from tests.tool_world import NOW

TICKET = json.dumps({"subject": "Zipper", "body": "It broke."})


class Counted:
    """A ``create_ticket`` that counts how often the tool itself runs."""

    def __init__(self):
        self.runs = 0

    async def __call__(self, ctx, **arguments):
        self.runs += 1
        return await create_ticket(ctx, **arguments)


def table(handler=create_ticket, idempotent=True, name="create_ticket") -> dict[str, ToolSpec]:
    return {
        name: ToolSpec(
            name,
            "x",
            TOOLS["create_ticket"].arguments,
            handler,
            action="a ticket",
            idempotent=idempotent,
        )
    }


async def call(world, customer, key, raw=TICKET, tools=None, name="create_ticket") -> ToolOutput:
    if world.session.in_transaction():  # a read in the test started one; end it first
        await world.session.commit()
    # A tool that fails rolls the session back, which expires the seeded rows: read the id first
    if not hasattr(world, "ids"):
        world.ids = {k: c.id for k, c in world.data.customers.items()}
    ids = world.ids
    ctx = ToolContext(world.session, ids[customer], clock=lambda: NOW, idempotency_key=key)
    return await run_tool(ctx, name, raw, tools or table())


async def keys(world) -> list[models.IdempotencyKey]:
    stored = select(models.IdempotencyKey).execution_options(populate_existing=True)
    return list(await world.session.scalars(stored))


async def test_a_repeat_gets_the_first_answer_and_changes_nothing(world):
    first = await call(world, "sam", "k1")
    second = await call(world, "sam", "k1")

    assert second == first
    assert first.status == "ok" and first.receipt
    assert await world.count(models.Ticket) == 1


async def test_the_tool_itself_runs_once(world):
    counted = Counted()

    await call(world, "sam", "k1", tools=table(counted))
    await call(world, "sam", "k1", tools=table(counted))

    assert counted.runs == 1


async def test_the_first_call_is_stored_as_it_was_answered(world):
    out = await call(world, "sam", "k1")

    (stored,) = await keys(world)
    assert (stored.tool, stored.output, stored.status, stored.receipt) == (
        "create_ticket",
        out.text,
        out.status,
        out.receipt,
    )
    assert stored.customer_id == world.data.customers["sam"].id
    assert stored.fingerprint and stored.created_at


async def test_the_same_request_in_another_word_order_is_the_same_request(world):
    await call(world, "sam", "k1", json.dumps({"subject": "Zipper", "body": "It broke."}))
    again = await call(world, "sam", "k1", json.dumps({"body": "It broke.", "subject": "Zipper"}))

    assert again.status == "ok"
    assert await world.count(models.Ticket) == 1


async def test_the_same_key_with_other_arguments_is_refused_and_runs_nothing(world):
    await call(world, "sam", "k1")

    other = await call(world, "sam", "k1", json.dumps({"subject": "Strap", "body": "Snapped."}))

    assert other.status == "invalid_request"
    assert "different request" in json.loads(other.text)["message"]
    assert await world.count(models.Ticket) == 1


async def test_the_same_key_for_another_tool_is_refused_and_runs_nothing(world):
    both = table() | table(name="escalate")  # a second, differently named tool
    both["escalate"] = dataclasses.replace(TOOLS["escalate"], idempotent=True)
    await call(world, "sam", "k1", tools=both)

    other = await call(
        world,
        "sam",
        "k1",
        json.dumps({"reason": "Two articles disagree."}),
        tools=both,
        name="escalate",
    )

    assert other.status == "invalid_request"
    assert await world.count(models.Ticket) == 1


async def test_calls_with_no_key_are_all_run(world):
    await call(world, "sam", None)
    await call(world, "sam", None)

    assert await world.count(models.Ticket) == 2
    assert await keys(world) == []


async def test_a_key_on_a_tool_that_is_not_marked_is_ignored(world):
    tools = table(idempotent=False)

    await call(world, "sam", "k1", tools=tools)
    await call(world, "sam", "k1", tools=tools)

    assert await world.count(models.Ticket) == 2
    assert await keys(world) == []


async def test_one_customers_key_is_not_another_customers(world):
    sam = await call(world, "sam", "shared")
    priya = await call(world, "priya", "shared")
    sam_again = await call(world, "sam", "shared")

    assert sam_again == sam
    assert priya != sam  # a ticket of her own, not Sam's
    assert await world.count(models.Ticket) == 2
    assert {k.customer_id for k in await keys(world)} == {
        world.data.customers["sam"].id,
        world.data.customers["priya"].id,
    }


@pytest.mark.parametrize("key", ["", "x" * (MAX_KEY_LENGTH + 1)])
async def test_a_key_that_is_empty_or_too_long_is_refused_and_runs_nothing(world, key):
    out = await call(world, "sam", key)

    assert out.status == "invalid_request"
    assert await world.count(models.Ticket) == 0


async def test_a_key_of_the_longest_allowed_length_works(world):
    out = await call(world, "sam", "x" * MAX_KEY_LENGTH)

    assert out.status == "ok"


async def test_a_tool_that_fails_leaves_no_key_so_the_retry_runs_it(world):
    async def writes_then_fails(ctx, subject, body):
        ctx.session.add(
            models.Ticket(customer_id=ctx.customer_id, kind="support", subject=subject, body=body)
        )
        await ctx.session.flush()
        raise RuntimeError("boom")

    failed = await call(world, "sam", "k1", tools=table(writes_then_fails))
    assert failed.status == "error"
    assert await world.count(models.Ticket) == 0
    assert await keys(world) == []

    retried = await call(world, "sam", "k1")

    assert retried.status == "ok"
    assert await world.count(models.Ticket) == 1


@pytest_asyncio.fixture
async def own_sessions(test_database_url):
    engine = create_async_engine(test_database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_two_identical_calls_at_once_give_one_run_and_one_replay(world, own_sessions):
    sam = world.data.customers["sam"].id
    counted = Counted()
    await world.session.commit()

    async def one() -> ToolOutput:
        async with own_sessions() as session:
            ctx = ToolContext(session, sam, clock=lambda: NOW, idempotency_key="together")
            return await run_tool(ctx, "create_ticket", TICKET, table(counted))

    first, second = await asyncio.gather(one(), one())

    assert first == second
    assert counted.runs == 1
    assert await world.count(models.Ticket) == 1
    assert len(await keys(world)) == 1
