"""A call whose outcome is unknown is tried once more with the same key, when repeating it is
safe. The server then runs it for the first time or answers with what the first attempt did, so
the step happens once and the client learns how it ended. Real app, real tokens; the answer or
the request is lost on the way by a transport that sits in front of the app."""

import json
import time
from contextlib import asynccontextmanager

import anyio
import httpx2
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models
from app.agent.backends import UNKNOWN_WRITE_MESSAGE, McpBackend
from app.agent.llm import ToolCall
from app.auth import to_current
from app.mcp_server.mapping import IDEMPOTENCY_KEY
from app.tools.registry import TOOLS, ToolOutput
from tests.mcp_app import URL, mcp_backend
from tests.tool_world import NOW


class Flaky(httpx2.AsyncBaseTransport):
    """In front of the app. For the numbered ``tools/call`` requests it loses the answer (the app
    ran the call, the client sees a failure) or the request (the app never sees it)."""

    def __init__(self, inner, lose_answer=(), lose_request=()):
        self.inner = inner
        self.lose_answer = set(lose_answer)
        self.lose_request = set(lose_request)
        self.calls: list[dict] = []  # the tools/call requests that were sent, as JSON

    async def handle_async_request(self, request):
        body = await request.aread()
        if b"tools/call" not in body:
            return await self.inner.handle_async_request(request)
        self.calls.append(json.loads(body))
        number = len(self.calls)
        if number in self.lose_request:
            raise httpx2.ReadError("the request was lost")
        response = await self.inner.handle_async_request(request)
        if number in self.lose_answer:
            raise httpx2.ReadError("the answer was lost")
        return response


class StallsFirst:
    """Sessions for the server whose first use ends late, after the commit."""

    def __init__(self, sessions, seconds):
        self.sessions = sessions
        self.seconds = seconds
        self.uses = 0

    @asynccontextmanager
    async def __call__(self):
        self.uses += 1
        late = self.uses == 1
        async with self.sessions() as session:
            yield session
        if late:
            await anyio.sleep(self.seconds)


@pytest_asyncio.fixture
async def sessions(world, test_database_url):
    engine = create_async_engine(test_database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class Rig:
    def __init__(self, world, sessions):
        self.world = world
        self.sessions = sessions
        self.flaky: Flaky | None = None

    def backend(self, *, serving=None, **flaky) -> McpBackend:
        # One Flaky for all attempts: it counts the calls across them
        shared: list[Flaky] = []

        def wrap_once(inner):
            if not shared:
                shared.append(Flaky(inner, **flaky))
                self.flaky = shared[0]
            # Each attempt has a new app, so the transport in front of it is new; the count
            # carries over
            shared[0].inner = inner
            return shared[0]

        return mcp_backend(self.sessions, clock=lambda: NOW, serving=serving, wrap=wrap_once)

    async def run(self, backend, customer, name, **arguments) -> ToolOutput:
        row = self.world.data.customers[customer]
        return await backend.run(to_current(row), ToolCall("c1", name, json.dumps(arguments)))

    async def count(self, model) -> int:
        return await self.world.count(model)


@pytest_asyncio.fixture
async def rig(world, sessions):
    return Rig(world, sessions)


def keys_sent(rig) -> list[str]:
    return [c["params"]["_meta"][IDEMPOTENCY_KEY] for c in rig.flaky.calls]


async def test_a_lost_answer_is_retried_and_the_first_result_comes_back(rig):
    backend = rig.backend(lose_answer=[1])
    before = await rig.count(models.Ticket)

    out = await rig.run(backend, "sam", "create_ticket", subject="Zipper", body="It broke.")

    assert (out.status, out.receipt) == ("ok", out.receipt)
    assert out.receipt.startswith("A support ticket (#")
    assert await rig.count(models.Ticket) == before + 1  # done once, not twice
    assert len(rig.flaky.calls) == 2


async def test_the_retry_sends_the_same_key(rig):
    backend = rig.backend(lose_answer=[1])

    await rig.run(backend, "sam", "create_ticket", subject="Zipper", body="It broke.")

    first, second = keys_sent(rig)
    assert first == second


async def test_a_lost_refund_answer_is_retried_and_the_refund_is_made_once(rig):
    backend = rig.backend(lose_answer=[1])
    before = await rig.count(models.Refund)

    out = await rig.run(
        backend,
        "alex",
        "issue_refund",
        order_item_id=rig.world.item("BOOT-TR"),
        quantity=1,
        reason_code="changed_mind",
    )

    assert out.status == "allowed"
    assert out.receipt == "A refund of $121.05 has been made."
    assert await rig.count(models.Refund) == before + 1


async def test_a_lost_request_is_retried_and_runs_once(rig):
    backend = rig.backend(lose_request=[1])
    before = await rig.count(models.Ticket)

    out = await rig.run(backend, "sam", "create_ticket", subject="Zipper", body="It broke.")

    assert out.status == "ok"
    assert await rig.count(models.Ticket) == before + 1


async def test_a_read_whose_answer_is_lost_is_retried(rig):
    backend = rig.backend(lose_answer=[1])

    out = await rig.run(backend, "priya", "list_orders")

    assert out.status == "ok"
    assert len(rig.flaky.calls) == 2


async def test_a_timeout_after_the_commit_is_retried_and_replays_the_stored_answer(rig):
    serving = StallsFirst(rig.sessions, seconds=10)
    backend = rig.backend(serving=serving)
    backend.timeout = 1.0
    before = await rig.count(models.Ticket)

    started = time.monotonic()
    out = await rig.run(backend, "sam", "create_ticket", subject="Zipper", body="It broke.")

    assert out.status == "ok" and out.receipt.startswith("A support ticket (#")
    assert await rig.count(models.Ticket) == before + 1
    assert time.monotonic() - started < 5


async def test_two_failed_attempts_are_unknown_and_the_step_still_happened_once(rig):
    backend = rig.backend(lose_answer=[1, 2])
    before = await rig.count(models.Ticket)

    out = await rig.run(backend, "sam", "create_ticket", subject="Zipper", body="It broke.")

    assert out.status == "unknown"
    assert json.loads(out.text)["message"] == UNKNOWN_WRITE_MESSAGE
    assert out.receipt == "Whether your support ticket went through could not be confirmed."
    assert len(rig.flaky.calls) == 2  # one retry, not more
    assert await rig.count(models.Ticket) == before + 1


async def test_a_write_that_is_not_idempotent_is_not_retried(rig):
    unmarked = {
        name: spec
        if name != "create_ticket"
        else spec.__class__(
            spec.name,
            spec.description,
            spec.arguments,
            spec.handler,
            action=spec.action,
            idempotent=False,
        )
        for name, spec in TOOLS.items()
    }
    backend = rig.backend(lose_answer=[1])
    backend.tools = unmarked
    before = await rig.count(models.Ticket)

    out = await rig.run(backend, "sam", "create_ticket", subject="Zipper", body="It broke.")

    assert out.status == "unknown"
    assert len(rig.flaky.calls) == 1
    assert await rig.count(models.Ticket) == before + 1  # it ran, and nothing ran it again


async def test_a_failure_before_anything_was_sent_is_not_retried(rig):
    attempts = []

    def refused(request):
        attempts.append(request)
        raise httpx2.ConnectError("connection refused")

    backend = McpBackend(
        URL,
        http_client=lambda headers: httpx2.AsyncClient(
            transport=httpx2.MockTransport(refused), headers=headers
        ),
    )
    row = rig.world.data.customers["sam"]

    out = await backend.run(
        to_current(row), ToolCall("c1", "create_ticket", '{"subject": "s", "body": "b"}')
    )

    assert out.status == "error"
    assert len(attempts) == 1


async def test_an_unknown_tool_name_is_not_retried(rig):
    backend = rig.backend(lose_answer=[1])

    out = await rig.run(backend, "sam", "refund_everything")

    assert out.status == "unknown"
    assert len(rig.flaky.calls) == 1


async def test_a_call_that_answers_the_first_time_is_sent_once(rig):
    backend = rig.backend()

    out = await rig.run(backend, "priya", "list_orders")

    assert out.status == "ok"
    assert len(rig.flaky.calls) == 1
