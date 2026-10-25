"""``McpBackend`` against the real MCP app: a real token through the real middleware, over the
SDK's own client. The app is reached through an ASGI transport, so no socket is opened.

The cases that matter are the ones ``LocalBackend`` is held to: the same call must give the same
``ToolOutput`` on either backend, and the customer must come from the loop, not the model.
"""

import json
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx2
import jwt
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models
from app.agent.backends import TRANSPORT_FAILED_MESSAGE, LocalBackend, McpBackend
from app.agent.llm import ToolCall
from app.auth import ALGORITHM, to_current
from app.config import settings
from app.mcp_server.auth import make_authenticate
from app.mcp_server.server import MCP_PATH, create_app
from app.tools.registry import ToolOutput
from tests.tool_world import NOW

# A port is needed: on localhost the app only accepts a Host header that has one
BASE = "http://localhost:8765"
URL = f"{BASE}{MCP_PATH}"


class Rig:
    """An ``McpBackend`` wired to the app, and a record of what it sent."""

    def __init__(self, app, sessions, world):
        self.app = app
        self.sessions = sessions
        self.world = world
        self.requests: list[dict[str, str]] = []  # the headers of each client it built
        self.backend = McpBackend(URL, http_client=self.client)
        self.local = LocalBackend(sessions, None, lambda: NOW)

    def client(self, headers: dict[str, str]) -> httpx2.AsyncClient:
        self.requests.append(headers)
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app), base_url=BASE, headers=headers
        )

    @asynccontextmanager
    async def running(self):
        """The app's own start-up, which the test transport does not send. It has to be entered
        in the test's own task, so it cannot live in a fixture."""
        async with self.app.router.lifespan_context(self.app):
            yield

    def customer(self, key: str):
        return to_current(self.world.data.customers[key])

    async def over_mcp(self, key: str, name: str, **arguments) -> ToolOutput:
        call = ToolCall("call-1", name, json.dumps(arguments))
        async with self.running():
            return await self.backend.run(self.customer(key), call)

    async def in_process(self, key: str, name: str, **arguments) -> ToolOutput:
        if self.world.session.in_transaction():  # a read in the test started one; end it first
            await self.world.session.commit()
        return await self.local.run(
            self.customer(key), ToolCall("call-1", name, json.dumps(arguments))
        )


@pytest_asyncio.fixture
async def rig(world, test_database_url):
    engine = create_async_engine(test_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(sessions, make_authenticate(sessions), clock=lambda: NOW, host="localhost")
    yield Rig(app, sessions, world)
    await engine.dispose()


def body(output: ToolOutput) -> dict:
    return json.loads(output.text)


async def test_a_lookup_gives_the_output_the_local_backend_gives(rig):
    order = rig.world.order("alex-socks")

    over_mcp = await rig.over_mcp("alex", "lookup_order", order_id=order)
    local = await rig.in_process("alex", "lookup_order", order_id=order)

    assert over_mcp == local
    assert over_mcp.status == "ok"


async def test_an_allowed_refund_comes_back_with_its_receipt_and_is_committed(rig):
    out = await rig.over_mcp(
        "alex",
        "issue_refund",
        order_item_id=rig.world.item("BOOT-TR"),
        quantity=1,
        reason_code="changed_mind",
    )

    assert (out.status, body(out)["amount_cents"]) == ("allowed", 12105)
    assert out.receipt == "A refund of $121.05 has been made."
    assert "receipt" not in out.text
    rig.world.session.expire_all()
    refund = await rig.world.session.get(models.Refund, body(out)["refund_id"])
    assert (refund.status, refund.amount_cents) == ("completed", 12105)


async def test_a_denied_refund_has_no_receipt_and_writes_nothing(rig):
    before = await rig.world.count(models.Refund)

    out = await rig.over_mcp(
        "alex",
        "issue_refund",
        order_item_id=rig.world.item("SHL-RN"),
        quantity=1,
        reason_code="changed_mind",
    )

    assert (out.status, body(out)["reason"], out.receipt) == ("denied", "final_sale", None)
    assert await rig.world.count(models.Refund) == before


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("refund_everything", {}),
        ("lookup_order", {}),
        ("list_orders", {"customer_id": 2}),
    ],
)
async def test_an_invalid_call_gives_the_local_backends_answer(rig, name, arguments):
    over_mcp = await rig.over_mcp("alex", name, **arguments)
    local = await rig.in_process("alex", name, **arguments)

    assert over_mcp == local
    assert over_mcp.status == "invalid_request"


async def test_the_call_runs_as_the_loops_customer(rig):
    alexs = rig.world.order("alex-5d")
    call = ToolCall("call-1", "lookup_order", json.dumps({"order_id": alexs}))

    async with rig.running():  # the app can only be started once, so both calls share it
        as_alex = await rig.backend.run(rig.customer("alex"), call)
        as_priya = await rig.backend.run(rig.customer("priya"), call)

    assert as_alex.status == "ok"
    assert as_priya.status == "not_found"


async def test_a_customer_id_the_model_adds_changes_nothing(rig):
    before = await rig.world.count(models.Refund)

    out = await rig.over_mcp(
        "alex",
        "issue_refund",
        order_item_id=rig.world.item("SOCK-TR"),
        quantity=1,
        reason_code="changed_mind",
        customer_id=rig.world.data.customers["priya"].id,
    )

    assert out.status == "invalid_request"
    assert await rig.world.count(models.Refund) == before


async def test_the_token_is_for_that_customer_and_short_lived(rig):
    await rig.over_mcp("sam", "list_orders")

    header = rig.requests[0]["Authorization"]
    claims = jwt.decode(header.removeprefix("Bearer "), settings.jwt_secret, algorithms=[ALGORITHM])
    assert claims["sub"] == str(rig.world.data.customers["sam"].id)
    assert claims["exp"] - claims["iat"] == timedelta(minutes=2).total_seconds()


@pytest.mark.parametrize("arguments", ["not json", "[1, 2]", '"text"', "7", "null"])
async def test_arguments_that_are_not_a_json_object_never_reach_the_server(rig, arguments):
    call = ToolCall("call-1", "list_orders", arguments)

    out = await rig.backend.run(rig.customer("alex"), call)

    assert (out.status, out.receipt) == ("invalid_request", None)
    assert "JSON object" in body(out)["message"]
    assert rig.requests == []


async def test_empty_arguments_are_an_empty_object(rig):
    call = ToolCall("call-1", "list_orders", "")

    async with rig.running():
        out = await rig.backend.run(rig.customer("priya"), call)

    assert out.status == "ok"


async def test_a_server_that_is_not_there_is_not_reported_as_nothing_changed(rig):
    def refused(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    down = McpBackend(
        URL,
        http_client=lambda headers: httpx2.AsyncClient(
            transport=httpx2.MockTransport(refused), headers=headers
        ),
    )
    call = ToolCall("call-1", "list_orders", "{}")

    out = await down.run(rig.customer("alex"), call)

    assert (out.status, out.receipt) == ("error", None)
    assert body(out)["message"] == TRANSPORT_FAILED_MESSAGE
    assert "nothing was changed" not in out.text
    assert "may or may not" in out.text


async def test_a_server_that_refuses_the_token_gives_an_error_and_no_receipt(rig):
    ghost = rig.customer("alex").model_copy(update={"id": 999_999})  # a token for no one
    call = ToolCall("call-1", "list_orders", "{}")

    async with rig.running():
        out = await rig.backend.run(ghost, call)

    assert (out.status, out.receipt) == ("error", None)
    assert "may or may not" in out.text
