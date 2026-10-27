"""The real tools with a key: each outcome of a write is answered the same way when it is sent
again, and only the three write tools take a key."""

import json

from sqlalchemy import select

from app import models
from app.tools.context import ToolContext
from app.tools.registry import TOOLS, ToolOutput, run_tool
from tests.tool_world import NOW


def test_exactly_the_three_write_tools_take_a_key():
    assert {name for name, spec in TOOLS.items() if spec.idempotent} == {
        "issue_refund",
        "create_ticket",
        "escalate",
    }


class Keyed:
    def __init__(self, world):
        self.world = world
        self.ids = {k: c.id for k, c in world.data.customers.items()}

    async def call(self, customer: str, key: str | None, name: str, **arguments) -> ToolOutput:
        if self.world.session.in_transaction():  # a read in the test started one; end it first
            await self.world.session.commit()
        ctx = ToolContext(
            self.world.session, self.ids[customer], clock=lambda: NOW, idempotency_key=key
        )
        return await run_tool(ctx, name, json.dumps(arguments), TOOLS)

    def refund(self, customer, key, sku, quantity=1, reason="changed_mind"):
        return self.call(
            customer,
            key,
            "issue_refund",
            order_item_id=self.world.item(sku),
            quantity=quantity,
            reason_code=reason,
        )


def body(output: ToolOutput) -> dict:
    return json.loads(output.text)


async def test_an_allowed_refund_sent_twice_is_made_once(world):
    keyed = Keyed(world)
    before = await world.count(models.Refund)

    first = await keyed.refund("alex", "k1", "BOOT-TR")
    second = await keyed.refund("alex", "k1", "BOOT-TR")

    assert second == first
    assert (first.status, body(first)["amount_cents"]) == ("allowed", 12105)
    assert first.receipt == "A refund of $121.05 has been made."
    assert await world.count(models.Refund) == before + 1


async def test_a_refund_held_for_approval_sent_twice_is_held_once(world):
    keyed = Keyed(world)
    before = await world.count(models.Refund)

    first = await keyed.refund("priya", "k1", "TENT-4S")
    second = await keyed.refund("priya", "k1", "TENT-4S")

    assert second == first
    assert first.status == "needs_approval"
    assert first.receipt == "A refund of $421.05 is waiting for approval."
    assert await world.count(models.Refund) == before + 1


async def test_a_denied_refund_sent_twice_gives_the_same_denial(world):
    keyed = Keyed(world)
    before = await world.count(models.Refund)

    first = await keyed.refund("alex", "k1", "SHL-RN")
    second = await keyed.refund("alex", "k1", "SHL-RN")

    assert second == first
    assert (first.status, body(first)["reason"], first.receipt) == ("denied", "final_sale", None)
    assert await world.count(models.Refund) == before


async def test_a_manual_review_sent_twice_opens_one_ticket(world):
    keyed = Keyed(world)
    refunds = await world.count(models.Refund)
    reviews = await world.count(models.Ticket, kind="manual_review")

    first = await keyed.refund("alex", "k1", "BOOT-TR", reason="defect")
    second = await keyed.refund("alex", "k1", "BOOT-TR", reason="defect")

    assert second == first
    assert first.status == "manual_review"
    assert await world.count(models.Ticket, kind="manual_review") == reviews + 1
    assert await world.count(models.Refund) == refunds


async def test_a_replayed_refund_is_the_one_stored_not_a_fresh_decision(world):
    keyed = Keyed(world)
    before = await world.count(models.Refund)
    first = await keyed.refund("alex", "k1", "BOOT-TR")

    # The line is now fully refunded, so a new request for it would be refused. The repeat is not
    # a new request: it gets the answer the first one gave.
    new_key = await keyed.refund("alex", "k2", "BOOT-TR")
    repeat = await keyed.refund("alex", "k1", "BOOT-TR")

    assert new_key.status == "invalid_request"
    assert repeat == first and repeat.status == "allowed"
    assert await world.count(models.Refund) == before + 1


async def test_a_new_key_is_a_new_request_so_a_model_that_repeats_itself_is_not_stopped(world):
    """The limit this phase does not close: the key identifies one call. A model that calls
    again after an unknown outcome makes a new call, and a multi-unit line refunds twice."""
    keyed = Keyed(world)
    before = await world.count(models.Refund)

    first = await keyed.refund("alex", "k1", "SOCK-TR", quantity=1)
    again = await keyed.refund("alex", "k2", "SOCK-TR", quantity=1)

    assert (first.status, again.status) == ("allowed", "allowed")
    assert await world.count(models.Refund) == before + 2


async def test_a_ticket_and_an_escalation_sent_twice_are_opened_once(world):
    keyed = Keyed(world)

    ticket = await keyed.call("sam", "t1", "create_ticket", subject="Zipper", body="It broke.")
    ticket_again = await keyed.call(
        "sam", "t1", "create_ticket", subject="Zipper", body="It broke."
    )
    escalation = await keyed.call("sam", "e1", "escalate", reason="Two articles disagree.")
    escalation_again = await keyed.call("sam", "e1", "escalate", reason="Two articles disagree.")

    assert (ticket_again, escalation_again) == (ticket, escalation)
    kinds = [t.kind for t in await world.session.scalars(select(models.Ticket))]
    assert sorted(kinds) == ["escalation", "support"]


async def test_a_ticket_result_the_tool_turned_away_is_stored_and_replayed(world):
    keyed = Keyed(world)

    first = await keyed.call("sam", "k1", "create_ticket", subject="  ", body="body")
    second = await keyed.call("sam", "k1", "create_ticket", subject="  ", body="body")

    assert second == first and first.status == "invalid_request"


async def test_the_read_tools_take_no_key_and_run_every_time(world):
    keyed = Keyed(world)

    await keyed.call("priya", "k1", "list_orders")
    await keyed.call("priya", "k1", "list_orders")

    assert await world.count(models.IdempotencyKey) == 0


async def test_every_stored_answer_belongs_to_the_customer_who_made_it(world):
    keyed = Keyed(world)
    await keyed.refund("alex", "shared", "BOOT-TR")
    await keyed.refund("priya", "shared", "TENT-4S")

    stored = {
        row.customer_id: row.status
        for row in await world.session.scalars(select(models.IdempotencyKey))
    }

    assert stored == {keyed.ids["alex"]: "allowed", keyed.ids["priya"]: "needs_approval"}
