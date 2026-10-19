from datetime import UTC, date, datetime

from sqlalchemy import func, select, text

from app import models
from app.seed import seed

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


async def seeded(session):
    async with session.begin():
        return await seed(session, NOW)


def delivery_days(order) -> int:
    return (NOW.date() - order.delivered_at.date()).days


async def test_seed_loads_customers_with_every_role_and_membership(session):
    data = await seeded(session)
    customers = data.customers.values()
    assert len(data.customers) == 6
    assert {c.membership for c in customers} == {"none", "basic", "plus"}
    assert [c.role for c in customers].count("approver") == 1


async def test_seed_can_be_run_again(session):
    first = await seeded(session)
    second = await seeded(session)
    assert len(second.orders) == len(first.orders)
    assert second.customers["alex"].id == 1
    count = await session.scalar(select(func.count()).select_from(models.Customer))
    assert count == 6


async def test_orders_sit_on_both_sides_of_each_window_boundary(session):
    data = await seeded(session)
    for key, days in {
        "alex-5d": 5,
        "alex-29d": 29,
        "alex-30d": 30,
        "alex-31d": 31,
        "alex-45d": 45,
        "priya-45d": 45,
        "priya-60d": 60,
        "priya-61d": 61,
    }.items():
        assert delivery_days(data.orders[key]) == days, key


async def test_order_totals_and_dates_are_consistent(session):
    data = await seeded(session)
    for key, order in data.orders.items():
        lines = [i for i in data.items.values() if i.order_id == order.id]
        assert order.total_cents == sum(i.price_cents * i.quantity for i in lines), key
        assert (order.status == "delivered") == (order.delivered_at is not None), key
        if order.delivered_at:
            assert order.placed_at <= order.delivered_at <= NOW, key


async def test_refunds_match_their_order_item_and_customer(session):
    data = await seeded(session)
    items = {i.id: i for i in data.items.values()}
    orders = {o.id: o for o in data.orders.values()}
    refunded: dict[int, int] = {}
    for refund in data.refunds:
        item = items[refund.order_item_id]
        assert item.order_id == refund.order_id
        assert orders[refund.order_id].customer_id == refund.customer_id
        assert refund.amount_cents == item.price_cents * refund.quantity
        refunded[item.id] = refunded.get(item.id, 0) + refund.quantity
    assert all(units <= items[item_id].quantity for item_id, units in refunded.items())


async def test_refund_history_sets_up_the_frequency_rule(session):
    data = await seeded(session)

    def ages(customer: str) -> list[int]:
        customer_id = data.customers[customer].id
        return sorted(
            (NOW.date() - r.created_at.date()).days
            for r in data.refunds
            if r.customer_id == customer_id
        )

    assert ages("jordan") == [5, 30, 60]
    assert ages("morgan") == [20, 45, 91]


async def test_one_large_refund_waits_for_an_approver(session):
    data = await seeded(session)
    pending = [r for r in data.refunds if r.status == "pending"]
    assert len(pending) == 1
    assert pending[0].amount_cents > 20_000
    assert pending[0].customer_id == data.customers["priya"].id


async def test_seed_leaves_the_knowledge_base_alone(session):
    async with session.begin():
        await session.execute(
            text(
                "INSERT INTO documents (id, title, status, updated_at, source, content, content_hash)"
                " VALUES ('doc', 'Doc', 'current', :day, 'test', 'body', 'hash')"
            ),
            {"day": date(2026, 1, 1)},
        )
    await seeded(session)
    count = await session.scalar(select(func.count()).select_from(models.Document))
    assert count == 1
