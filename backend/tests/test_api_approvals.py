import pytest
from sqlalchemy import select

from app import models


async def refund_status(api, refund_id):
    async with api.sessions() as session:
        return await session.scalar(
            select(models.Refund.status).where(models.Refund.id == refund_id)
        )


def pending_id(api):
    """The seeded pending refund: Priya's insulated parka."""
    (refund,) = [r for r in api.seeded.refunds if r.status == "pending"]
    return refund.id


async def decide(api, refund_id, decision, customer="approver"):
    return await api.client.post(
        f"/approvals/{refund_id}/decision",
        json={"decision": decision},
        headers=api.headers(customer),
    )


# Who may call


@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/approvals"), ("POST", "/approvals/1/decision")]
)
async def test_the_approver_endpoints_need_a_login(api, method, path):
    response = await api.client.request(method, path, json={"decision": "approve"})
    assert response.status_code == 401


async def test_a_customer_cannot_list_or_decide(api):
    refund_id = pending_id(api)
    listed = await api.client.get("/approvals", headers=api.headers("alex"))
    decided = await decide(api, refund_id, "approve", customer="alex")
    assert (listed.status_code, decided.status_code) == (403, 403)
    assert await refund_status(api, refund_id) == "pending"


async def test_the_refunds_owner_cannot_decide_it_even_with_the_approver_endpoint(api):
    assert (await decide(api, pending_id(api), "approve", customer="priya")).status_code == 403


# Listing


async def test_the_approver_sees_pending_refunds_with_who_what_and_how_much(api):
    response = await api.client.get("/approvals", headers=api.headers("approver"))
    assert response.status_code == 200
    (refund,) = response.json()
    assert refund["refund_id"] == pending_id(api)
    assert refund["customer_name"] == "Priya Shah"
    assert refund["item_name"] == "Insulated parka"
    assert (refund["quantity"], refund["reason"], refund["is_own"]) == (1, "changed_mind", False)
    assert refund["amount_cents"] > 20_000


async def test_a_decided_refund_leaves_the_list(api):
    await decide(api, pending_id(api), "reject")
    assert (await api.client.get("/approvals", headers=api.headers("approver"))).json() == []


async def test_completed_refunds_are_not_listed(api):
    listed = (await api.client.get("/approvals", headers=api.headers("approver"))).json()
    assert [r["refund_id"] for r in listed] == [pending_id(api)]
    assert len(api.seeded.refunds) > 1  # the others are not pending


async def test_the_approvers_own_refund_is_marked(api):
    approver = api.seeded.customers["approver"]
    order = api.seeded.orders["alex-5d"]
    item = api.seeded.items["BOOT-TR"]
    async with api.sessions.begin() as session:
        session.add(
            models.Refund(
                order_id=order.id,
                order_item_id=item.id,
                customer_id=approver.id,
                quantity=1,
                amount_cents=25_000,
                reason="changed_mind",
                status="pending",
            )
        )
    listed = (await api.client.get("/approvals", headers=api.headers("approver"))).json()
    assert sorted(r["is_own"] for r in listed) == [False, True]


# Deciding


@pytest.mark.parametrize(("decision", "status"), [("approve", "approved"), ("reject", "rejected")])
async def test_an_approver_can_approve_or_reject(api, decision, status):
    refund_id = pending_id(api)
    response = await decide(api, refund_id, decision)
    assert response.status_code == 200
    assert response.json() == {"status": status, "refund_id": refund_id}
    assert await refund_status(api, refund_id) == status
    async with api.sessions() as session:
        refund = await session.get(models.Refund, refund_id)
    assert refund.decided_by == api.seeded.customers["approver"].id
    assert refund.decided_at is not None


async def test_a_refund_is_decided_once(api):
    refund_id = pending_id(api)
    assert (await decide(api, refund_id, "approve")).status_code == 200
    again = await decide(api, refund_id, "reject")
    assert again.status_code == 409
    assert await refund_status(api, refund_id) == "approved"


async def test_an_unknown_refund_is_not_found(api):
    assert (await decide(api, 99999, "approve")).status_code == 404


async def test_a_completed_refund_cannot_be_decided(api):
    completed = next(r for r in api.seeded.refunds if r.status == "completed")
    response = await decide(api, completed.id, "approve")
    assert response.status_code == 409
    assert await refund_status(api, completed.id) == "completed"


async def test_an_approver_cannot_decide_their_own_refund(api):
    approver = api.seeded.customers["approver"]
    async with api.sessions.begin() as session:
        own = models.Refund(
            order_id=api.seeded.orders["alex-5d"].id,
            order_item_id=api.seeded.items["BOOT-TR"].id,
            customer_id=approver.id,
            quantity=1,
            amount_cents=25_000,
            reason="changed_mind",
            status="pending",
        )
        session.add(own)
        await session.flush()
        own_id = own.id
    response = await decide(api, own_id, "approve")
    assert response.status_code == 403
    assert await refund_status(api, own_id) == "pending"


@pytest.mark.parametrize(
    "body",
    [{}, {"decision": "maybe"}, {"decision": "approve", "amount_cents": 1}, {"approve": True}],
)
async def test_a_bad_decision_is_rejected_and_changes_nothing(api, body):
    refund_id = pending_id(api)
    response = await api.client.post(
        f"/approvals/{refund_id}/decision", json=body, headers=api.headers("approver")
    )
    assert response.status_code == 422
    assert await refund_status(api, refund_id) == "pending"


async def test_demoting_the_approver_stops_their_existing_token(api):
    headers = api.headers("approver")
    assert (await api.client.get("/approvals", headers=headers)).status_code == 200
    async with api.sessions.begin() as session:
        approver = await session.get(models.Customer, api.seeded.customers["approver"].id)
        approver.role = "customer"
    assert (await api.client.get("/approvals", headers=headers)).status_code == 403
    assert await refund_status(api, pending_id(api)) == "pending"
