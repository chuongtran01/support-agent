from app.orders import orders_for
from app.tools.context import ToolContext
from app.tools.results import LookupOrderResult


async def lookup_order(ctx: ToolContext, order_id: int) -> LookupOrderResult:
    """Return one of the session customer's orders. Another customer's order looks the same
    as one that does not exist."""
    orders = await orders_for(ctx.session, ctx.customer_id, order_id)
    if not orders:
        return LookupOrderResult(status="not_found", message="No matching order was found.")
    return LookupOrderResult(status="ok", order=orders[0])
