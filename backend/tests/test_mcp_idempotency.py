"""The idempotency key over MCP: the client sends it in the call's ``_meta``, the server reads it
into the tool context, and a repeat is answered, not run again. Real app, real tokens."""

import json

import pytest
import pytest_asyncio
from mcp.client import Client
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models
from app.agent.llm import ToolCall
from app.auth import to_current
from app.mcp_server.mapping import IDEMPOTENCY_KEY, from_call_result
from app.mcp_server.server import create_server
from app.tools.registry import TOOLS, CreateTicketArguments, ToolSpec, tool_schemas
from app.tools.results import TicketResult
from tests.mcp_app import mcp_backend
from tests.tool_world import NOW

TICKET = json.dumps({"subject": "Zipper", "body": "It broke."})


@pytest_asyncio.fixture
async def sessions(world, test_database_url):
    engine = create_async_engine(test_database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class Rig:
    def __init__(self, world, sessions):
        self.world = world
        self.sessions = sessions
        self.ids = {k: c.id for k, c in world.data.customers.items()}

    def backend(self, key: str | None = None):
        backend = mcp_backend(self.sessions, clock=lambda: NOW)
        if key is not None:
            backend.new_key = lambda: key
        return backend

    async def run(self, backend, customer: str, name: str, **arguments):
        row = self.world.data.customers[customer]
        return await backend.run(to_current(row), ToolCall("c1", name, json.dumps(arguments)))

    async def tickets(self) -> int:
        return await self.world.count(models.Ticket)

    async def refunds(self) -> int:
        return await self.world.count(models.Refund)


@pytest_asyncio.fixture
async def rig(world, sessions):
    return Rig(world, sessions)


async def test_a_ticket_sent_twice_with_one_key_is_opened_once(rig):
    backend = rig.backend("same")
    before = await rig.tickets()

    first = await rig.run(backend, "sam", "create_ticket", subject="Zipper", body="It broke.")
    second = await rig.run(backend, "sam", "create_ticket", subject="Zipper", body="It broke.")

    assert second == first and first.status == "ok"
    assert await rig.tickets() == before + 1


async def test_a_refund_sent_twice_with_one_key_is_made_once_with_the_same_receipt(rig):
    backend = rig.backend("same")
    before = await rig.refunds()
    arguments = {
        "order_item_id": rig.world.item("BOOT-TR"),
        "quantity": 1,
        "reason_code": "changed_mind",
    }

    first = await rig.run(backend, "alex", "issue_refund", **arguments)
    second = await rig.run(backend, "alex", "issue_refund", **arguments)

    assert second == first
    assert first.receipt == "A refund of $121.05 has been made."
    assert await rig.refunds() == before + 1


async def test_each_call_makes_its_own_key_unless_told_otherwise(rig):
    backend = rig.backend()  # the default: a new random key for every call
    before = await rig.tickets()

    await rig.run(backend, "sam", "create_ticket", subject="Zipper", body="It broke.")
    await rig.run(backend, "sam", "create_ticket", subject="Zipper", body="It broke.")

    assert await rig.tickets() == before + 2


async def test_the_same_key_for_two_customers_is_two_calls(rig):
    backend = rig.backend("shared")
    before = await rig.tickets()

    sam = await rig.run(backend, "sam", "create_ticket", subject="Zipper", body="It broke.")
    priya = await rig.run(backend, "priya", "create_ticket", subject="Zipper", body="It broke.")

    assert sam != priya
    assert await rig.tickets() == before + 2


async def test_the_same_key_for_a_different_request_is_refused(rig):
    backend = rig.backend("same")
    before = await rig.tickets()

    await rig.run(backend, "sam", "create_ticket", subject="Zipper", body="It broke.")
    other = await rig.run(backend, "sam", "create_ticket", subject="Strap", body="Snapped.")

    assert other.status == "invalid_request"
    assert await rig.tickets() == before + 1


# What the server does with the metadata, with a tool that records what it was given --------


class Spy:
    def __init__(self):
        self.keys: list[str | None] = []

    async def __call__(self, ctx, subject, body) -> TicketResult:
        self.keys.append(ctx.idempotency_key)
        return TicketResult(status="ok", ticket_id=1)


async def call_with(rig, spy, meta, arguments=None, idempotent=True):
    tools = {
        "create_ticket": ToolSpec(
            "create_ticket",
            "x",
            CreateTicketArguments,
            spy,
            action="a ticket",
            idempotent=idempotent,
        )
    }

    async def authenticate(headers):
        return to_current(rig.world.data.customers["sam"])

    server = create_server(rig.sessions, authenticate, clock=lambda: NOW, tools=tools)
    async with Client(server) as client:
        result = await client.call_tool(
            "create_ticket", arguments or {"subject": "s", "body": "b"}, meta=meta
        )
    return from_call_result(result)


async def test_a_key_in_the_metadata_reaches_the_tool_context(rig):
    spy = Spy()

    await call_with(rig, spy, {IDEMPOTENCY_KEY: "abc"})

    assert spy.keys == ["abc"]


async def test_no_key_in_the_metadata_is_no_key_in_the_context(rig):
    spy = Spy()

    await call_with(rig, spy, None)

    assert spy.keys == [None]


@pytest.mark.parametrize("key", [7, ["a"], {"k": 1}, True])
async def test_a_key_that_is_not_text_is_refused_and_runs_nothing(rig, key):
    spy = Spy()

    out = await call_with(rig, spy, {IDEMPOTENCY_KEY: key})

    assert out.status == "invalid_request"
    assert spy.keys == []


async def test_an_empty_key_is_refused_and_runs_nothing(rig):
    spy = Spy()

    out = await call_with(rig, spy, {IDEMPOTENCY_KEY: ""})

    assert out.status == "invalid_request"
    assert spy.keys == []


async def test_a_key_the_model_writes_into_the_arguments_is_turned_away(rig):
    spy = Spy()

    out = await call_with(rig, spy, None, {"subject": "s", "body": "b", "idempotency_key": "mine"})

    assert out.status == "invalid_request"
    assert "idempotency_key" in json.loads(out.text)["message"]
    assert spy.keys == []


def test_the_model_is_not_told_a_key_exists():
    told = json.dumps([vars(schema) for schema in tool_schemas(TOOLS)])

    assert "idempotency" not in told.lower()
    assert "northwind" not in told
