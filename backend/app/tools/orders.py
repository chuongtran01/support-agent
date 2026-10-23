from app.orders import find_orders, get_order
from app.tools.context import ToolContext
from app.tools.results import (
    ListOrdersResult,
    LookupOrderResult,
    OrderItemSummary,
    OrderSummary,
)

# More orders than this and the list says so, instead of filling the model's context
MAX_LISTED = 20


async def list_orders(ctx: ToolContext) -> ListOrdersResult:
    """The session customer's own orders, newest first, in short. The customer comes from the
    session, never from the model, so there is nothing to ask for and no one else's to see."""
    orders = await find_orders(ctx.session, ctx.customer_id, limit=MAX_LISTED + 1)
    if not orders:
        return ListOrdersResult(status="no_orders", message="This customer has no orders.")
    return ListOrdersResult(
        status="ok",
        orders=[
            OrderSummary(
                id=o.id,
                status=o.status,
                placed_at=o.placed_at,
                delivered_at=o.delivered_at,
                total_cents=o.total_cents,
                items=[
                    OrderItemSummary(
                        name=i.name, quantity=i.quantity, units_refunded=i.units_refunded
                    )
                    for i in o.items
                ],
            )
            for o in orders[:MAX_LISTED]
        ],
        truncated=len(orders) > MAX_LISTED,
    )


async def lookup_order(ctx: ToolContext, order_id: int) -> LookupOrderResult:
    """Return one of the session customer's orders. Another customer's order looks the same
    as one that does not exist."""
    order = await get_order(ctx.session, ctx.customer_id, order_id)
    if order is None:
        return LookupOrderResult(status="not_found", message="No matching order was found.")
    return LookupOrderResult(status="ok", order=order)
