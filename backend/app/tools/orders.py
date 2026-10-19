from sqlalchemy import select

from app import models
from app.tools.context import ToolContext
from app.tools.results import LookupOrderResult, OrderItemView, OrderView, RefundView

COUNTED_STATUSES = ("pending", "approved", "completed")


async def lookup_order(ctx: ToolContext, order_id: int) -> LookupOrderResult:
    """Return one of the session customer's orders. Another customer's order looks the same
    as one that does not exist."""
    order = await ctx.session.scalar(
        select(models.Order).where(
            models.Order.id == order_id, models.Order.customer_id == ctx.customer_id
        )
    )
    if order is None:
        return LookupOrderResult(status="not_found", message="No matching order was found.")
    items = (
        await ctx.session.scalars(
            select(models.OrderItem)
            .where(models.OrderItem.order_id == order.id)
            .order_by(models.OrderItem.id)
        )
    ).all()
    refunds = (
        await ctx.session.scalars(
            select(models.Refund)
            .where(models.Refund.order_id == order.id)
            .order_by(models.Refund.id)
        )
    ).all()
    return LookupOrderResult(
        status="ok",
        order=OrderView(
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
            ],
        ),
    )
