from sqlalchemy import event

from app.orders import find_orders, get_order


def mine(world, customer):
    return {
        o.id for o in world.data.orders.values() if o.customer_id == world.ctx(customer).customer_id
    }


async def test_get_order_returns_the_customers_own_order_with_its_items_and_refunds(world):
    order = await get_order(world.session, world.ctx("alex").customer_id, world.order("alex-socks"))
    assert order.id == world.order("alex-socks")
    assert [(i.name, i.units_refunded) for i in order.items] == [("Trail socks", 1)]
    assert [r.status for r in order.refunds] == ["completed"]


async def test_get_order_gives_none_for_another_customers_order_and_for_a_missing_one(world):
    alex = world.ctx("alex").customer_id
    assert await get_order(world.session, alex, world.order("priya-45d")) is None
    assert await get_order(world.session, alex, 99999) is None


async def test_find_orders_gives_only_the_customers_own_newest_first(world):
    orders = await find_orders(world.session, world.ctx("alex").customer_id)
    assert {o.id for o in orders} == mine(world, "alex")
    placed = [o.placed_at for o in orders]
    assert placed == sorted(placed, reverse=True)


async def test_find_orders_limit_gives_the_newest_ones(world):
    everything = await find_orders(world.session, world.ctx("alex").customer_id)
    newest = await find_orders(world.session, world.ctx("alex").customer_id, limit=3)
    assert [o.id for o in newest] == [o.id for o in everything[:3]]


async def test_find_orders_limit_is_in_the_query_and_loads_no_more_than_that(world):
    """Not a slice done afterwards: the limit is in the SQL, so only that many orders' items
    and refunds are asked for."""
    seen = []

    def record(conn, cursor, statement, parameters, *rest):
        seen.append((statement, parameters))

    engine = world.session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        await find_orders(world.session, world.ctx("alex").customer_id, limit=2)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    orders_sql = next(s for s, _ in seen if "FROM orders" in s)
    assert "LIMIT" in orders_sql
    _, item_parameters = next((s, p) for s, p in seen if "FROM order_items" in s)
    assert len(item_parameters) == 2  # the ids of the two orders, and nothing else


async def test_a_customer_with_no_orders_gets_an_empty_list(world):
    assert await find_orders(world.session, world.ctx("approver").customer_id) == []
