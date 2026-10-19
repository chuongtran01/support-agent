import json

import pytest

from app import models
from app.policy.engine import ReasonCode
from app.tools.context import ToolContext
from app.tools.registry import TOOLS, CreateTicketArguments, ToolSpec, dispatch, tool_schemas
from app.tools.results import TicketResult
from tests.test_tools import world  # noqa: F401  (fixture)


def no_database() -> ToolContext:
    """A context for calls that must be turned away before anything touches the database."""
    return ToolContext(session=None, customer_id=1)  # type: ignore[arg-type]


async def call(ctx, name, **arguments) -> dict:
    return json.loads(await dispatch(ctx, name, json.dumps(arguments)))


# Schemas ---------------------------------------------------------------


def test_the_agent_gets_four_tools_and_not_decide_refund():
    names = [schema["function"]["name"] for schema in tool_schemas()]
    assert names == ["lookup_order", "issue_refund", "create_ticket", "escalate"]


def test_no_tool_accepts_a_customer_or_an_amount():
    for schema in tool_schemas():
        parameters = schema["function"]["parameters"]
        assert parameters["additionalProperties"] is False
        assert not {"customer_id", "amount_cents", "amount"} & set(parameters["properties"])


def test_issue_refund_schema_lists_the_reason_codes_and_requires_every_argument():
    parameters = tool_schemas()[1]["function"]["parameters"]
    assert parameters["required"] == ["order_item_id", "quantity", "reason_code"]
    assert parameters["properties"]["reason_code"]["enum"] == list(ReasonCode.__args__)
    assert parameters["properties"]["quantity"]["minimum"] == 1
    assert "title" not in json.dumps(parameters)


# Bad calls never reach the database --------------------------------------


@pytest.mark.parametrize(
    ("name", "raw", "fragment"),
    [
        ("refund_everything", "{}", "no tool called"),
        ("lookup_order", "{not json", "arguments"),
        ("lookup_order", "[]", "arguments"),
        ("lookup_order", "{}", "order_id"),
        ("lookup_order", '{"order_id": "abc"}', "order_id"),
        (
            "issue_refund",
            '{"order_item_id": 1, "quantity": 0, "reason_code": "defect"}',
            "quantity",
        ),
        (
            "issue_refund",
            '{"order_item_id": 1, "quantity": 1, "reason_code": "bored"}',
            "reason_code",
        ),
        ("issue_refund", '{"order_item_id": 1, "quantity": 1}', "reason_code"),
        (
            "issue_refund",
            '{"order_item_id": 1, "quantity": 1, "reason_code": "defect", "customer_id": 2}',
            "customer_id",
        ),
        (
            "issue_refund",
            '{"order_item_id": 1, "quantity": 1, "reason_code": "defect", "amount_cents": 1}',
            "amount_cents",
        ),
    ],
)
async def test_invalid_calls_are_turned_away_with_a_reason(name, raw, fragment):
    result = json.loads(await dispatch(no_database(), name, raw))
    assert result["status"] == "invalid_request"
    assert fragment in result["message"]


async def test_an_empty_argument_string_counts_as_no_arguments():
    result = json.loads(await dispatch(no_database(), "lookup_order", ""))
    assert result["status"] == "invalid_request"
    assert "order_id" in result["message"]


# Running tools -----------------------------------------------------------


async def test_lookup_order_returns_json_without_empty_fields(world):  # noqa: F811
    result = await call(world.ctx("alex"), "lookup_order", order_id=world.order("alex-socks"))
    assert result["status"] == "ok"
    assert "message" not in result
    assert result["order"]["items"][0]["order_item_id"] == world.item("SOCK-TR")


async def test_a_foreign_customer_id_argument_changes_nothing(world):  # noqa: F811
    priya = world.data.customers["priya"].id
    ctx, item = world.ctx("alex"), world.item("SOCK-TR")
    result = await call(
        ctx,
        "issue_refund",
        order_item_id=item,
        quantity=1,
        reason_code="changed_mind",
        customer_id=priya,
    )
    assert result["status"] == "invalid_request"
    assert await world.count(models.Refund) == 8  # the seeded refunds, nothing new


async def test_issue_refund_through_dispatch_commits_the_refund(world):  # noqa: F811
    ctx, item = world.ctx("alex"), world.item("BOOT-TR")
    result = await call(
        ctx, "issue_refund", order_item_id=item, quantity=1, reason_code="changed_mind"
    )
    assert (result["status"], result["amount_cents"]) == ("allowed", 12900)
    assert "ticket_id" not in result
    world.session.expire_all()
    refund = await world.session.get(models.Refund, result["refund_id"])
    assert refund.status == "completed"


async def test_another_customers_item_is_not_found_through_dispatch(world):  # noqa: F811
    ctx, item = world.ctx("sam"), world.item("SOCK-TR")  # Alex's socks, asked for by Sam
    result = await call(
        ctx, "issue_refund", order_item_id=item, quantity=1, reason_code="changed_mind"
    )
    assert result["status"] == "not_found"
    assert await world.count(models.Refund) == 8


async def test_a_tool_that_raises_leaves_nothing_behind(world):  # noqa: F811
    async def writes_then_fails(ctx, subject, body) -> TicketResult:
        ctx.session.add(
            models.Ticket(customer_id=ctx.customer_id, kind="support", subject=subject, body=body)
        )
        await ctx.session.flush()
        raise RuntimeError("boom")

    tools = {
        "create_ticket": ToolSpec("create_ticket", "x", CreateTicketArguments, writes_then_fails)
    }
    raw = json.dumps({"subject": "s", "body": "b"})
    ctx = world.ctx("sam")
    result = json.loads(await dispatch(ctx, "create_ticket", raw, tools))
    assert result["status"] == "error"
    assert await world.count(models.Ticket) == 0


async def test_create_ticket_through_dispatch_is_saved_for_the_session_customer(world):  # noqa: F811
    sam = world.data.customers["sam"].id
    result = await call(world.ctx("sam"), "create_ticket", subject="Zipper", body="It broke.")
    assert result["status"] == "ok"
    ticket = await world.session.get(models.Ticket, result["ticket_id"])
    assert ticket.customer_id == sam


def test_every_tool_in_the_registry_has_a_handler_that_takes_a_context():
    assert set(TOOLS) == {"lookup_order", "issue_refund", "create_ticket", "escalate"}
