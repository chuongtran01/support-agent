"""A customer's orders, with their items and refunds. The one place that reads them, so the
``lookup_order`` tool and the orders endpoint cannot disagree about whose they are."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.tools.results import OrderItemView, OrderView, RefundView

# Refunds that use up units of an item. A rejected refund does not.
COUNTED_STATUSES = ("pending", "approved", "completed")


async def orders_for(
    session: AsyncSession,
    customer_id: int,
    order_id: int | None = None,
    limit: int | None = None,
) -> list[OrderView]:
    """The customer's orders, newest first, or just one of them, or only the newest ``limit``.
    Another customer's order is never returned: it looks the same as one that does not exist."""
    query = select(models.Order).where(models.Order.customer_id == customer_id)
    if order_id is not None:
        query = query.where(models.Order.id == order_id)
    orders = (
        await session.scalars(query.order_by(models.Order.placed_at.desc(), models.Order.id.desc()))
    ).all()
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
