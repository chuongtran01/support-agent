def orders_of(api, customer):
    return [o for o in api.seeded.orders.values() if o.customer_id == api.customer_id(customer)]


async def get_orders(api, customer):
    return await api.client.get("/orders", headers=api.headers(customer))


async def test_listing_orders_needs_a_login(api):
    assert (await api.client.get("/orders")).status_code == 401


async def test_a_customer_sees_exactly_their_own_orders(api):
    response = await get_orders(api, "alex")
    assert response.status_code == 200
    assert {o["id"] for o in response.json()} == {o.id for o in orders_of(api, "alex")}


async def test_no_customer_sees_anyone_elses_order(api):
    mine = {o["id"] for o in (await get_orders(api, "alex")).json()}
    theirs = {o["id"] for o in (await get_orders(api, "priya")).json()}
    assert mine.isdisjoint(theirs)
    assert theirs == {o.id for o in orders_of(api, "priya")}


async def test_orders_come_newest_first(api):
    placed = [o["placed_at"] for o in (await get_orders(api, "alex")).json()]
    assert placed == sorted(placed, reverse=True)
    assert len(placed) > 1


async def test_an_order_carries_its_items_and_how_many_units_are_refunded(api):
    socks = api.seeded.orders["alex-socks"]
    order = next(o for o in (await get_orders(api, "alex")).json() if o["id"] == socks.id)
    (item,) = order["items"]
    assert (item["name"], item["quantity"], item["units_refunded"]) == ("Trail socks", 3, 1)
    assert order["status"] == "delivered"
    assert order["total_cents"] == 4200
    (refund,) = order["refunds"]
    assert (refund["status"], refund["quantity"]) == ("completed", 1)


async def test_a_pending_refund_shows_on_the_owners_order_and_counts_as_used(api):
    parka = api.seeded.orders["priya-parka"]
    order = next(o for o in (await get_orders(api, "priya")).json() if o["id"] == parka.id)
    assert [r["status"] for r in order["refunds"]] == ["pending"]
    assert order["items"][0]["units_refunded"] == 1


async def test_an_order_that_has_not_arrived_has_no_delivery_date(api):
    shipped = api.seeded.orders["alex-shipped"]
    order = next(o for o in (await get_orders(api, "alex")).json() if o["id"] == shipped.id)
    assert order["status"] == "shipped"
    assert order["delivered_at"] is None


async def test_someone_with_no_orders_gets_an_empty_list(api):
    response = await get_orders(api, "approver")
    assert (response.status_code, response.json()) == (200, [])
