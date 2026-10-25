"""The tool cases of ``test_tools.py`` and ``test_registry.py`` that go through ``run_tool``, run
twice: once straight into it and once through the MCP server's in-memory transport. The same
expectations must hold for both, which is what lets the agent swap one for the other.

Calls that cannot be made over MCP (arguments that are not a JSON object) are not here: the
protocol turns those away before the server sees them.
"""

import json

import pytest
import pytest_asyncio
from mcp.client import Client
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models
from app.auth import to_current
from app.mcp_server.mapping import from_call_result
from app.mcp_server.server import create_server
from app.tools.registry import TOOLS, CreateTicketArguments, ToolOutput, ToolSpec, run_tool
from app.tools.results import TicketResult
from tests.tool_world import NOW


class Direct:
    """The tools called straight into ``run_tool``, as the agent does today."""

    def __init__(self, world):
        self.world = world

    async def run(self, customer, name, tools=TOOLS, **arguments) -> ToolOutput:
        if self.world.session.in_transaction():  # a read in the test started one; end it first
            await self.world.session.commit()
        return await run_tool(self.world.ctx(customer), name, json.dumps(arguments), tools)


class ViaMcp:
    """The same calls through a server and the SDK's in-memory client."""

    def __init__(self, world, sessions):
        self.world = world
        self.sessions = sessions

    async def run(self, customer, name, tools=TOOLS, **arguments) -> ToolOutput:
        row = self.world.data.customers[customer]

        async def authenticate(headers):
            return to_current(row)

        server = create_server(self.sessions, authenticate, clock=lambda: NOW, tools=tools)
        async with Client(server) as client:
            return from_call_result(await client.call_tool(name, arguments))


@pytest_asyncio.fixture(params=["direct", "mcp"])
async def backend(request, world, test_database_url):
    engine = create_async_engine(test_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield Direct(world) if request.param == "direct" else ViaMcp(world, sessions)
    await engine.dispose()


def body(output: ToolOutput) -> dict:
    return json.loads(output.text)


async def refunds(world) -> int:
    return await world.count(models.Refund)


# Reading orders ----------------------------------------------------------


async def test_lookup_order_returns_the_order_without_empty_fields(world, backend):
    out = await backend.run("alex", "lookup_order", order_id=world.order("alex-socks"))

    assert out.status == "ok"
    assert "message" not in body(out)
    assert body(out)["order"]["items"][0]["order_item_id"] == world.item("SOCK-TR")
    assert out.receipt is None


async def test_another_customers_order_looks_like_one_that_does_not_exist(world, backend):
    foreign = await backend.run("alex", "lookup_order", order_id=world.order("priya-45d"))
    missing = await backend.run("alex", "lookup_order", order_id=99999)

    assert foreign == missing
    assert foreign.status == "not_found"


async def test_list_orders_lists_the_session_customers_orders_only(world, backend):
    out = await backend.run("priya", "list_orders")

    priya = world.data.customers["priya"].id
    mine = {o.id for o in world.data.orders.values() if o.customer_id == priya}
    assert out.status == "ok"
    assert {o["id"] for o in body(out)["orders"]} == mine


# issue_refund: each verdict ----------------------------------------------


async def test_an_allowed_refund_is_committed_with_the_computed_amount(world, backend):
    out = await backend.run(
        "alex",
        "issue_refund",
        order_item_id=world.item("BOOT-TR"),
        quantity=1,
        reason_code="changed_mind",
    )

    assert (out.status, body(out)["amount_cents"]) == ("allowed", 12105)
    assert out.receipt == "A refund of $121.05 has been made."
    assert "ticket_id" not in body(out)
    world.session.expire_all()
    refund = await world.session.get(models.Refund, body(out)["refund_id"])
    assert (refund.status, refund.amount_cents, refund.created_at) == ("completed", 12105, NOW)


async def test_part_of_a_multi_unit_line_is_refunded(world, backend):
    out = await backend.run(
        "alex",
        "issue_refund",
        order_item_id=world.item("SOCK-TR"),
        quantity=2,
        reason_code="changed_mind",
    )

    assert (out.status, body(out)["amount_cents"]) == ("allowed", 2005)


@pytest.mark.parametrize(
    ("customer", "sku", "reason"),
    [
        ("alex", "BAG-SLP", "outside_window"),
        ("alex", "SHL-RN", "final_sale"),
        ("alex", "GC-50", "not_refundable"),
        ("alex", "BTL-WTR", "not_delivered"),
        ("priya", "GLV-WN", "outside_window"),
    ],
)
async def test_a_denied_refund_writes_nothing_and_has_no_receipt(
    world, backend, customer, sku, reason
):
    before = await refunds(world)

    out = await backend.run(
        customer,
        "issue_refund",
        order_item_id=world.item(sku),
        quantity=1,
        reason_code="changed_mind",
    )

    assert (out.status, body(out)["reason"], out.receipt) == ("denied", reason, None)
    assert await refunds(world) == before


async def test_a_large_refund_is_held_as_pending(world, backend):
    out = await backend.run(
        "priya",
        "issue_refund",
        order_item_id=world.item("TENT-4S"),
        quantity=1,
        reason_code="changed_mind",
    )

    assert (out.status, body(out)["reason"]) == ("needs_approval", "over_threshold")
    assert out.receipt == "A refund of $421.05 is waiting for approval."
    world.session.expire_all()
    refund = await world.session.get(models.Refund, body(out)["refund_id"])
    assert (refund.status, refund.amount_cents) == ("pending", 42105)


async def test_another_customers_item_is_not_found(world, backend):
    before = await refunds(world)

    out = await backend.run(
        "sam",  # Alex's socks, asked for by Sam
        "issue_refund",
        order_item_id=world.item("SOCK-TR"),
        quantity=1,
        reason_code="changed_mind",
    )

    assert out.status == "not_found"
    assert await refunds(world) == before


async def test_a_foreign_customer_id_argument_changes_nothing(world, backend):
    before = await refunds(world)

    out = await backend.run(
        "alex",
        "issue_refund",
        order_item_id=world.item("SOCK-TR"),
        quantity=1,
        reason_code="changed_mind",
        customer_id=world.data.customers["priya"].id,
    )

    assert out.status == "invalid_request"
    assert await refunds(world) == before


# Tickets -----------------------------------------------------------------


async def test_tickets_belong_to_the_session_customer(world, backend):
    sam = world.data.customers["sam"].id

    support = await backend.run("sam", "create_ticket", subject="Zipper", body="It broke.")
    escalation = await backend.run("sam", "escalate", reason="Two articles disagree.")

    assert (support.status, escalation.status) == ("ok", "ok")
    assert support.receipt == f"A support ticket (#{body(support)['ticket_id']}) has been opened."
    world.session.expire_all()
    for output, kind in ((support, "support"), (escalation, "escalation")):
        ticket = await world.session.get(models.Ticket, body(output)["ticket_id"])
        assert (ticket.kind, ticket.customer_id) == (kind, sam)


async def test_empty_or_oversized_ticket_text_is_rejected(world, backend):
    blank_subject = await backend.run("sam", "create_ticket", subject="  ", body="body")
    long_body = await backend.run("sam", "create_ticket", subject="subject", body="x" * 4001)
    blank_reason = await backend.run("sam", "escalate", reason="")

    statuses = (blank_subject.status, long_body.status, blank_reason.status)
    assert statuses == ("invalid_request",) * 3
    assert await world.count(models.Ticket) == 0


# Bad calls ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments", "fragment"),
    [
        ("refund_everything", {}, "no tool called"),
        ("lookup_order", {}, "order_id"),
        ("lookup_order", {"order_id": "abc"}, "order_id"),
        ("issue_refund", {"order_item_id": 1, "quantity": 0, "reason_code": "defect"}, "quantity"),
        (
            "issue_refund",
            {"order_item_id": 1, "quantity": 1, "reason_code": "bored"},
            "reason_code",
        ),
        ("issue_refund", {"order_item_id": 1, "quantity": 1}, "reason_code"),
        (
            "issue_refund",
            {"order_item_id": 1, "quantity": 1, "reason_code": "defect", "customer_id": 2},
            "customer_id",
        ),
        (
            "issue_refund",
            {"order_item_id": 1, "quantity": 1, "reason_code": "defect", "amount_cents": 1},
            "amount_cents",
        ),
        ("list_orders", {"customer_id": 2}, "customer_id"),
    ],
)
async def test_an_invalid_call_is_turned_away_with_a_reason(backend, name, arguments, fragment):
    out = await backend.run("alex", name, **arguments)

    assert out.status == "invalid_request"
    assert out.receipt is None
    assert fragment in body(out)["message"]


# A tool that fails --------------------------------------------------------


async def test_a_tool_that_raises_leaves_nothing_behind(world, backend):
    async def writes_then_fails(ctx, subject, body) -> TicketResult:
        ctx.session.add(
            models.Ticket(customer_id=ctx.customer_id, kind="support", subject=subject, body=body)
        )
        await ctx.session.flush()
        raise RuntimeError("boom")

    tools = {
        "create_ticket": ToolSpec("create_ticket", "x", CreateTicketArguments, writes_then_fails)
    }

    out = await backend.run("sam", "create_ticket", tools=tools, subject="s", body="b")

    assert (out.status, out.receipt) == ("error", None)
    assert await world.count(models.Ticket) == 0
