"""Who may call the server, over real HTTP: the app, its middleware and the SDK's own client."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx2
import jwt
import pytest
import pytest_asyncio
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import ALGORITHM, create_token
from app.mcp_server.auth import bearer_token, make_authenticate
from app.mcp_server.mapping import STATUS_KEY
from app.mcp_server.server import create_app
from app.tools.registry import TOOLS
from tests.tool_world import NOW

# A port is needed: on localhost the app only accepts a Host header that has one
BASE = "http://localhost:8765"
URL = f"{BASE}/mcp"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
OTHER_SECRET = "a-different-secret-of-32-bytes!!"


class Mcp:
    def __init__(self, app, world):
        self.app = app
        self.world = world

    def token(self, customer: str) -> str:
        return create_token(self.world.data.customers[customer].id)

    def http(self, authorization: str | None) -> httpx2.AsyncClient:
        headers = {"Authorization": authorization} if authorization else {}
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app), base_url=BASE, headers=headers
        )

    @asynccontextmanager
    async def running(self):
        """The app's own start-up, which the test transport does not send. It has to be entered
        in the test's own task, so it cannot live in a fixture."""
        async with self.app.router.lifespan_context(self.app):
            yield

    @asynccontextmanager
    async def connect(self, authorization: str | None):
        """An MCP client, and the HTTP client under it, with the app running."""
        async with (
            self.running(),
            self.http(authorization) as http,
            Client(streamable_http_client(URL, http_client=http)) as client,
        ):
            yield client, http

    async def status(self, authorization: str | None) -> int:
        async with self.http(authorization) as http:
            return (await http.post(URL, json=LIST_TOOLS, headers=MCP_HEADERS)).status_code


@pytest_asyncio.fixture
async def mcp(world, test_database_url):
    engine = create_async_engine(test_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(sessions, make_authenticate(sessions), clock=lambda: NOW, host="localhost")
    yield Mcp(app, world)
    await engine.dispose()


@pytest.mark.parametrize(
    ("header", "token"),
    [
        ("Bearer abc.def", "abc.def"),
        ("bearer abc.def", "abc.def"),
        ("BEARER  abc.def ", "abc.def"),
        ("Basic abc.def", None),
        ("Bearer", None),
        ("Bearer ", None),
        ("abc.def", None),
        ("", None),
    ],
)
def test_the_token_is_read_from_a_bearer_header(header, token):
    assert bearer_token({"Authorization": header}) == token


def test_the_header_name_is_not_case_sensitive():
    assert bearer_token({"authorization": "Bearer abc"}) == "abc"


def test_no_headers_is_no_token():
    assert bearer_token(None) is None
    assert bearer_token({}) is None


async def test_a_valid_token_lists_the_tools(mcp):
    async with mcp.connect(f"Bearer {mcp.token('alex')}") as (client, _):
        listed = await client.list_tools()

    assert [t.name for t in listed.tools] == list(TOOLS)


async def test_a_token_made_by_the_api_works_as_that_customer(mcp):
    order = mcp.world.order("alex-5d")
    async with mcp.connect(f"Bearer {mcp.token('alex')}") as (client, _):
        result = await client.call_tool("lookup_order", {"order_id": order})

    assert json.loads(result.content[0].text)["order"]["id"] == order


async def test_a_token_for_one_customer_cannot_reach_anothers_order(mcp):
    order = mcp.world.order("alex-5d")
    async with mcp.connect(f"Bearer {mcp.token('priya')}") as (client, _):
        result = await client.call_tool("lookup_order", {"order_id": order})

    assert result.meta[STATUS_KEY] == "not_found"


def forged(customer_id: int, secret: str, expires: datetime) -> str:
    claims = {"sub": str(customer_id), "iat": expires - timedelta(hours=1), "exp": expires}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


async def test_every_bad_token_is_refused_with_a_401(mcp):
    alex = mcp.world.data.customers["alex"].id
    now = datetime.now(UTC)
    bad = {
        "no header": None,
        "not a bearer header": "Basic abc",
        "garbage": "Bearer garbage",
        "another secret": f"Bearer {forged(alex, OTHER_SECRET, now + timedelta(hours=1))}",
        "expired": f"Bearer {create_token(alex, now - timedelta(days=2))}",
        "no such customer": f"Bearer {create_token(999_999)}",
    }

    statuses = {name: await mcp.status(header) for name, header in bad.items()}

    assert statuses == dict.fromkeys(bad, 401)


async def test_the_refusal_says_how_to_sign_in(mcp):
    async with mcp.http(None) as http:
        response = await http.post(URL, json=LIST_TOOLS, headers=MCP_HEADERS)

    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_a_good_token_is_not_refused(mcp):
    async with mcp.running():
        assert await mcp.status(f"Bearer {mcp.token('alex')}") != 401


async def test_a_client_with_a_bad_token_cannot_connect(mcp):
    with pytest.raises(BaseException):  # noqa: B017  (the SDK's error type varies)
        async with mcp.connect("Bearer garbage") as (client, _):
            await client.list_tools()


async def test_a_token_that_stops_working_mid_session_stops_the_calls(mcp):
    async with mcp.connect(f"Bearer {mcp.token('alex')}") as (client, http):
        assert await client.list_tools()

        http.headers["Authorization"] = "Bearer garbage"

        with pytest.raises(BaseException):  # noqa: B017
            await client.call_tool("list_orders", {})
