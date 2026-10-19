import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models
from app.seed import seed
from app.tools.context import ToolContext
from app.tools.orders import lookup_order
from app.tools.refunds import decide_refund, issue_refund
from app.tools.tickets import create_ticket, escalate

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


# lookup_order ----------------------------------------------------------


async def test_lookup_returns_the_customers_own_order(world):
    result = await world.run(lookup_order(world.ctx("alex"), world.order("alex-socks")))
    order = result.order
    assert result.status == "ok"
    socks = order.items[0]
    assert (socks.quantity, socks.units_refunded) == (3, 1)
    assert socks.order_item_id == world.item("SOCK-TR")
    assert len(order.refunds) == 1


async def test_another_customers_order_looks_like_one_that_does_not_exist(world):
    foreign = await world.run(lookup_order(world.ctx("alex"), world.order("priya-45d")))
    missing = await world.run(lookup_order(world.ctx("alex"), 99999))
    assert foreign == missing
    assert foreign.status == "not_found"


# issue_refund: each verdict --------------------------------------------


async def test_allowed_refund_is_completed_with_the_computed_amount(world):
    result = await world.run(
        issue_refund(world.ctx("alex"), world.item("BOOT-TR"), 1, "changed_mind")
    )
    assert (result.status, result.reason, result.amount_cents) == ("allowed", "ok", 12900)
    refund = await world.session.get(models.Refund, result.refund_id)
    assert (refund.status, refund.amount_cents, refund.quantity) == ("completed", 12900, 1)
    assert refund.created_at == NOW


async def test_part_of_a_multi_unit_line(world):
    result = await world.run(
        issue_refund(world.ctx("alex"), world.item("SOCK-TR"), 2, "changed_mind")
    )
    assert (result.status, result.amount_cents) == ("allowed", 2800)


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
async def test_denied_refund_writes_nothing(world, customer, sku, reason):
    before = await world.count(models.Refund)
    result = await world.run(issue_refund(world.ctx(customer), world.item(sku), 1, "changed_mind"))
    assert (result.status, result.reason) == ("denied", reason)
    assert result.refund_id is None and result.ticket_id is None
    assert await world.count(models.Refund) == before


async def test_large_refund_is_held_as_pending(world):
    result = await world.run(
        issue_refund(world.ctx("priya"), world.item("TENT-4S"), 1, "changed_mind")
    )
    assert (result.status, result.reason) == ("needs_approval", "over_threshold")
    refund = await world.session.get(models.Refund, result.refund_id)
    assert (refund.status, refund.amount_cents) == ("pending", 42900)


async def test_plus_member_inside_the_sixty_day_window(world):
    result = await world.run(
        issue_refund(world.ctx("priya"), world.item("PACK-65"), 1, "changed_mind")
    )
    assert result.reason == "over_threshold"  # allowed through the window, then over $200


async def test_defect_claim_opens_a_ticket_and_no_refund(world):
    before = await world.count(models.Refund)
    result = await world.run(issue_refund(world.ctx("alex"), world.item("BOOT-TR"), 1, "defect"))
    assert (result.status, result.reason) == ("manual_review", "needs_human_review")
    assert result.refund_id is None
    ticket = await world.session.get(models.Ticket, result.ticket_id)
    assert ticket.kind == "manual_review"
    assert ticket.customer_id == world.data.customers["alex"].id
    assert "defect" in ticket.body
    assert await world.count(models.Refund) == before


async def test_fourth_refund_in_ninety_days_goes_to_a_person(world):
    result = await world.run(
        issue_refund(world.ctx("jordan"), world.item("LNT-CMP"), 1, "changed_mind")
    )
    assert (result.status, result.reason) == ("manual_review", "refund_frequency")
    assert result.ticket_id is not None


async def test_a_refund_91_days_old_does_not_count(world):
    result = await world.run(
        issue_refund(world.ctx("morgan"), world.item("CMP-MG"), 1, "changed_mind")
    )
    assert result.status == "allowed"


# issue_refund: request checks ------------------------------------------


async def test_more_units_than_remain_is_an_invalid_request(world):
    result = await world.run(
        issue_refund(world.ctx("alex"), world.item("SOCK-TR"), 3, "changed_mind")
    )
    assert result.status == "invalid_request"
    assert result.message == "Only 2 units can still be refunded."


async def test_pending_refund_uses_up_the_units(world):
    result = await world.run(
        issue_refund(world.ctx("priya"), world.item("PRK-INS"), 1, "changed_mind")
    )
    assert result.status == "invalid_request"
    assert "fully refunded" in result.message


async def test_rejected_refund_frees_the_units_again(world):
    pending = (
        await world.session.scalars(select(models.Refund).where(models.Refund.status == "pending"))
    ).one()
    approver = world.data.customers["approver"].id
    await world.run(decide_refund(world.session, approver, pending.id, "reject", NOW))
    result = await world.run(
        issue_refund(world.ctx("priya"), world.item("PRK-INS"), 1, "changed_mind")
    )
    assert result.status == "needs_approval"


@pytest.mark.parametrize("quantity", [0, -1])
async def test_quantity_must_be_positive(world, quantity):
    result = await world.run(
        issue_refund(world.ctx("alex"), world.item("BOOT-TR"), quantity, "changed_mind")
    )
    assert result.status == "invalid_request"


async def test_unknown_reason_code_is_rejected(world):
    result = await world.run(
        issue_refund(world.ctx("alex"), world.item("BOOT-TR"), 1, "feeling_lucky")
    )
    assert result.status == "invalid_request"


async def test_another_customers_item_looks_like_one_that_does_not_exist(world):
    before = await world.count(models.Refund)
    foreign = await world.run(
        issue_refund(world.ctx("alex"), world.item("TENT-4S"), 1, "changed_mind")
    )
    missing = await world.run(issue_refund(world.ctx("alex"), 99999, 1, "changed_mind"))
    assert foreign == missing
    assert foreign.status == "not_found"
    assert await world.count(models.Refund) == before


# tickets ---------------------------------------------------------------


async def test_tickets_belong_to_the_session_customer(world):
    support = await world.run(create_ticket(world.ctx("sam"), "Broken strap", "The strap snapped."))
    escalation = await world.run(
        escalate(world.ctx("sam"), "Two articles disagree about the return window.")
    )
    sam = world.data.customers["sam"].id
    tickets = {t.id: t for t in (await world.session.scalars(select(models.Ticket))).all()}
    assert (tickets[support.ticket_id].kind, tickets[support.ticket_id].customer_id) == (
        "support",
        sam,
    )
    assert (
        tickets[escalation.ticket_id].kind,
        tickets[escalation.ticket_id].customer_id,
    ) == ("escalation", sam)


async def test_empty_or_oversized_ticket_text_is_rejected(world):
    blank_subject = await world.run(create_ticket(world.ctx("sam"), "  ", "body"))
    long_body = await world.run(create_ticket(world.ctx("sam"), "subject", "x" * 4001))
    blank_reason = await world.run(escalate(world.ctx("sam"), ""))
    assert blank_subject.status == long_body.status == blank_reason.status == "invalid_request"
    assert await world.count(models.Ticket) == 0


# decide_refund ---------------------------------------------------------


async def pending_refund_id(world) -> int:
    return (
        (
            await world.session.scalars(
                select(models.Refund).where(models.Refund.status == "pending")
            )
        )
        .one()
        .id
    )


@pytest.mark.parametrize(("decision", "status"), [("approve", "approved"), ("reject", "rejected")])
async def test_approver_decides_a_pending_refund(world, decision, status):
    approver = world.data.customers["approver"].id
    refund_id = await pending_refund_id(world)
    result = await world.run(decide_refund(world.session, approver, refund_id, decision, NOW))
    assert (result.status, result.refund_id) == (status, refund_id)
    refund = await world.session.get(models.Refund, refund_id)
    assert (refund.status, refund.decided_by, refund.decided_at) == (status, approver, NOW)


async def test_a_customer_cannot_decide_a_refund(world):
    refund_id = await pending_refund_id(world)
    result = await world.run(
        decide_refund(world.session, world.data.customers["alex"].id, refund_id, "approve", NOW)
    )
    assert result.status == "not_authorized"
    assert (await world.session.get(models.Refund, refund_id)).status == "pending"


async def test_a_refund_can_only_be_decided_once(world):
    approver = world.data.customers["approver"].id
    refund_id = await pending_refund_id(world)
    await world.run(decide_refund(world.session, approver, refund_id, "approve", NOW))
    again = await world.run(decide_refund(world.session, approver, refund_id, "reject", NOW))
    assert again.status == "not_pending"
    assert (await world.session.get(models.Refund, refund_id)).status == "approved"


async def test_completed_refunds_cannot_be_decided(world):
    refund = (
        await world.session.scalars(
            select(models.Refund).where(models.Refund.status == "completed")
        )
    ).first()
    result = await world.run(
        decide_refund(world.session, world.data.customers["approver"].id, refund.id, "approve", NOW)
    )
    assert result.status == "not_pending"


async def test_unknown_refund_is_not_found(world):
    result = await world.run(
        decide_refund(world.session, world.data.customers["approver"].id, 99999, "approve", NOW)
    )
    assert result.status == "not_found"


async def test_an_approver_cannot_decide_their_own_refund(world):
    async with world.session.begin():
        order = models.Order(
            customer_id=world.data.customers["approver"].id,
            status="delivered",
            placed_at=NOW,
            delivered_at=NOW,
            total_cents=30000,
        )
        world.session.add(order)
        await world.session.flush()
        item = models.OrderItem(order_id=order.id, sku="ANY", name="Any", price_cents=30000)
        world.session.add(item)
    result = await world.run(issue_refund(world.ctx("approver"), item.id, 1, "changed_mind"))
    assert result.status == "needs_approval"
    decided = await world.run(
        decide_refund(
            world.session, world.data.customers["approver"].id, result.refund_id, "approve", NOW
        )
    )
    assert decided.status == "own_refund"
    assert (await world.session.get(models.Refund, result.refund_id)).status == "pending"


# concurrency -----------------------------------------------------------


async def test_two_simultaneous_refunds_at_the_limit_cannot_both_go_through(
    world, test_database_url
):
    morgan = world.data.customers["morgan"].id  # two refunds count, so the next one is the third
    async with world.session.begin():
        skus = []
        for n in (1, 2):
            order = models.Order(
                customer_id=morgan,
                status="delivered",
                placed_at=NOW,
                delivered_at=NOW,
                total_cents=1000,
            )
            world.session.add(order)
            await world.session.flush()
            world.session.add(
                models.OrderItem(
                    order_id=order.id, sku=f"RACE-{n}", name="Race item", price_cents=1000
                )
            )
            skus.append(f"RACE-{n}")
    items = (
        await world.session.scalars(
            select(models.OrderItem).where(models.OrderItem.sku.like("RACE-%"))
        )
    ).all()
    # Morgan's earlier refunds are at 20 and 45 days, and the seeded 91-day one does not count,
    # so one more is allowed and the one after it must go to manual review.
    engine = create_async_engine(test_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def request(item_id):
        async with maker() as session, session.begin():
            ctx = ToolContext(session, morgan, clock=lambda: NOW)
            return await issue_refund(ctx, item_id, 1, "changed_mind")

    results = await asyncio.gather(*(request(item.id) for item in items))
    await engine.dispose()
    assert sorted(r.status for r in results) == ["allowed", "manual_review"]
