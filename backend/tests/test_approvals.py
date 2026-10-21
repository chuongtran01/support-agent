from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app import models
from app.approvals import decide_refund, list_pending
from app.tools.refunds import issue_refund
from tests.tool_world import NOW


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


async def test_list_pending_shows_who_what_and_how_much(world):
    (pending,) = await list_pending(world.session)
    refund = (
        await world.session.scalars(select(models.Refund).where(models.Refund.status == "pending"))
    ).one()
    assert pending.refund_id == refund.id
    assert (pending.customer_name, pending.item_name) == ("Priya Shah", "Insulated parka")
    assert pending.customer_id == world.data.customers["priya"].id
    assert (pending.quantity, pending.amount_cents, pending.reason) == (1, 24000, "changed_mind")


async def test_list_pending_leaves_out_decided_refunds(world):
    approver = world.data.customers["approver"].id
    refund_id = await pending_refund_id(world)
    await world.run(decide_refund(world.session, approver, refund_id, "approve", NOW))
    assert await list_pending(world.session) == []


async def test_list_pending_is_oldest_first(world):
    async with world.session.begin():
        order = models.Order(
            customer_id=world.data.customers["alex"].id,
            status="delivered",
            placed_at=NOW,
            delivered_at=NOW,
            total_cents=1,
        )
        world.session.add(order)
        await world.session.flush()
        item = models.OrderItem(order_id=order.id, sku="ANY", name="Any", price_cents=30000)
        world.session.add(item)
        await world.session.flush()
        world.session.add(
            models.Refund(
                order_id=order.id,
                order_item_id=item.id,
                customer_id=order.customer_id,
                quantity=1,
                amount_cents=30000,
                reason="changed_mind",
                status="pending",
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
    first, second = await list_pending(world.session)
    assert first.created_at < second.created_at
    assert first.item_name == "Any"
