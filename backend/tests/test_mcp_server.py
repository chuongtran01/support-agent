"""The server, called through the SDK's in-memory client over the seeded test database."""

import json

import pytest
import pytest_asyncio
from mcp.client import Client
from mcp.shared.exceptions import MCPError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import to_current
from app.mcp_server.mapping import RECEIPT_KEY, STATUS_KEY, from_call_result
from app.mcp_server.server import create_server
from app.policy.engine import dollars
from app.tools.registry import TOOLS, run_tool, tool_schemas
from tests.tool_world import NOW


class Connection:
    """A client of a server that treats every request as coming from one customer."""

    def __init__(self, world, sessions):
        self.world = world
        self.sessions = sessions
        self.customer = "alex"

    async def authenticate(self, headers):
        if self.customer is None:
            return None
        return to_current(self.world.data.customers[self.customer])

    async def client(self):
        server = create_server(self.sessions, self.authenticate, clock=lambda: NOW)
        return Client(server)


@pytest_asyncio.fixture
async def connection(world, test_database_url):
    engine = create_async_engine(test_database_url)
    yield Connection(world, async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


async def test_it_lists_the_registry_tools_and_only_those(connection):
    async with await connection.client() as client:
        listed = await client.list_tools()

    assert [t.name for t in listed.tools] == list(TOOLS)


async def test_each_tool_has_the_registry_description_and_schema(connection):
    async with await connection.client() as client:
        listed = await client.list_tools()

    expected = {s.name: s for s in tool_schemas()}
    for tool in listed.tools:
        assert tool.description == expected[tool.name].description
        assert tool.input_schema == expected[tool.name].parameters


async def test_a_call_runs_as_the_customer_and_matches_run_tool(connection, world):
    order = world.order("alex-5d")
    async with await connection.client() as client:
        result = await client.call_tool("lookup_order", {"order_id": order})

    direct = await run_tool(world.ctx("alex"), "lookup_order", json.dumps({"order_id": order}))
    assert from_call_result(result) == direct
    assert json.loads(result.content[0].text)["order"]["id"] == order


async def test_a_call_cannot_reach_another_customers_order(connection, world):
    order = world.order("alex-5d")
    connection.customer = "priya"
    async with await connection.client() as client:
        result = await client.call_tool("lookup_order", {"order_id": order})

    assert result.meta[STATUS_KEY] == "not_found"
    assert result.is_error is False


async def test_a_refund_carries_its_receipt_in_meta_only(connection, world):
    item = world.item("BOOT-TR")
    async with await connection.client() as client:
        result = await client.call_tool(
            "issue_refund", {"order_item_id": item, "quantity": 1, "reason_code": "changed_mind"}
        )

    assert result.meta[STATUS_KEY] == "allowed"
    receipt = result.meta[RECEIPT_KEY]
    amount = json.loads(result.content[0].text)["amount_cents"]
    assert receipt == f"A refund of {dollars(amount)} has been made."
    assert receipt not in result.content[0].text


async def test_a_customer_argument_is_refused_and_nothing_runs(connection, world):
    order = world.order("alex-5d")
    async with await connection.client() as client:
        result = await client.call_tool("lookup_order", {"order_id": order, "customer_id": 99})

    assert result.is_error is True
    assert result.meta[STATUS_KEY] == "invalid_request"


async def test_an_unknown_tool_is_an_invalid_request(connection):
    async with await connection.client() as client:
        result = await client.call_tool("decide_refund", {"refund_id": 1})

    assert result.is_error is True
    assert result.meta[STATUS_KEY] == "invalid_request"


async def test_a_caller_who_is_not_signed_in_gets_an_error_not_tools(connection):
    connection.customer = None
    async with await connection.client() as client:
        with pytest.raises(MCPError):
            await client.list_tools()
        with pytest.raises(MCPError):
            await client.call_tool("list_orders", {})


async def test_without_an_embedder_search_fails_as_an_error_and_the_rest_work(connection):
    async with await connection.client() as client:
        search = await client.call_tool("search_knowledge", {"query": "return window"})
        orders = await client.call_tool("list_orders", {})

    assert search.is_error is True
    assert search.meta[STATUS_KEY] == "error"
    assert orders.is_error is False
    assert orders.meta[STATUS_KEY] == "ok"
