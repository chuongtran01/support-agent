"""A customer's orders, with their items and refunds. The one place that reads them, so the
``lookup_order`` and ``list_orders`` tools and the orders endpoint cannot disagree about whose
they are: every query starts from ``_mine``."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.tools.results import OrderItemView, OrderView, RefundView

# Refunds that use up units of an item. A rejected refund does not.
COUNTED_STATUSES = ("pending", "approved", "completed")


def _mine(customer_id: int):
    """The one place that says whose orders these are."""
    return select(models.Order).where(models.Order.customer_id == customer_id)


def _newest_first(query):
    return query.order_by(models.Order.placed_at.desc(), models.Order.id.desc())


async def get_order(session: AsyncSession, customer_id: int, order_id: int) -> OrderView | None:
    """One of the customer's orders, or None. Another customer's order is never returned: it
    looks the same as one that does not exist."""
    query = _mine(customer_id).where(models.Order.id == order_id)
    views = await _views(session, (await session.scalars(query)).all())
    return views[0] if views else None


async def find_orders(
    session: AsyncSession, customer_id: int, *, limit: int | None = None
) -> list[OrderView]:
    """The customer's orders, newest first, or only the newest ``limit`` of them. The limit is
    in the query, so a customer with thousands of orders does not load them all."""
    query = _newest_first(_mine(customer_id))
    if limit is not None:
        query = query.limit(limit)
    return await _views(session, (await session.scalars(query)).all())


async def _views(session: AsyncSession, orders: Sequence[models.Order]) -> list[OrderView]:
    """The orders with their items and refunds, in the order given: two queries in all, not
    two per order."""
    if not orders:
        return []
    ids = [order.id for order in orders]
    items = (
        await session.scalars(
            select(models.OrderItem)
            .where(models.OrderItem.order_id.in_(ids))
            .order_by(models.OrderItem.id)
        )
    ).all()
    refunds = (
        await session.scalars(
            select(models.Refund).where(models.Refund.order_id.in_(ids)).order_by(models.Refund.id)
        )
    ).all()
    return [
        OrderView(
            id=order.id,
            status=order.status,
            placed_at=order.placed_at.isoformat(),
            delivered_at=order.delivered_at.isoformat() if order.delivered_at else None,
            total_cents=order.total_cents,
            items=[
                OrderItemView(
                    order_item_id=item.id,
                    sku=item.sku,
                    name=item.name,
                    price_cents=item.price_cents,
                    quantity=item.quantity,
                    category=item.category,
                    is_final_sale=item.is_final_sale,
                    units_refunded=sum(
                        r.quantity
                        for r in refunds
                        if r.order_item_id == item.id and r.status in COUNTED_STATUSES
                    ),
                )
                for item in items
                if item.order_id == order.id
            ],
            refunds=[
                RefundView(
                    refund_id=r.id,
                    order_item_id=r.order_item_id,
                    quantity=r.quantity,
                    amount_cents=r.amount_cents,
                    status=r.status,
                    created_at=r.created_at.isoformat(),
                )
                for r in refunds
                if r.order_id == order.id
            ],
        )
        for order in orders
    ]
