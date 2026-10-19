from datetime import datetime
from typing import Literal, get_args

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.config import settings
from app.policy.engine import PolicyConfig, ReasonCode, RefundFacts, RefundRequest, evaluate_refund
from app.tools.checks import check_quantity
from app.tools.context import ToolContext
from app.tools.orders import COUNTED_STATUSES
from app.tools.results import DecisionResult, IssueRefundResult
from app.tools.tickets import open_ticket


def policy_config() -> PolicyConfig:
    return PolicyConfig(approval_threshold_cents=settings.refund_approval_threshold * 100)


def _invalid(message: str) -> IssueRefundResult:
    return IssueRefundResult(status="invalid_request", message=message)


async def issue_refund(
    ctx: ToolContext, order_item_id: int, quantity: int, reason_code: str
) -> IssueRefundResult:
    """Ask for a refund. The request is checked, then the policy engine decides, and the
    outcome is written. The model never supplies an amount: it is worked out from the price paid.
    """
    if reason_code not in get_args(ReasonCode):
        return _invalid(f"The reason code must be one of: {', '.join(get_args(ReasonCode))}.")

    # Serialize refunds per customer so two requests at the limit cannot both slip under it.
    customer = await ctx.session.scalar(
        select(models.Customer).where(models.Customer.id == ctx.customer_id).with_for_update()
    )
    found = (
        await ctx.session.execute(
            select(models.OrderItem, models.Order)
            .join(models.Order, models.Order.id == models.OrderItem.order_id)
            .where(
                models.OrderItem.id == order_item_id, models.Order.customer_id == ctx.customer_id
            )
        )
    ).first()
    if customer is None or found is None:
        return IssueRefundResult(status="not_found", message="No matching order item was found.")
    item, order = found

    earlier = (
        await ctx.session.scalars(
            select(models.Refund).where(
                models.Refund.customer_id == ctx.customer_id,
                models.Refund.status.in_(COUNTED_STATUSES),
            )
        )
    ).all()
    units_refunded = sum(r.quantity for r in earlier if r.order_item_id == item.id)
    if message := check_quantity(quantity, item.quantity, units_refunded):
        return _invalid(message)

    verdict = evaluate_refund(
        RefundRequest(quantity=quantity, reason_code=reason_code),
        RefundFacts(
            membership=customer.membership,
            delivered_at=order.delivered_at,
            item_category=item.category,
            is_final_sale=item.is_final_sale,
            unit_price_cents=item.price_cents,
            refund_dates=[r.created_at for r in earlier],
        ),
        ctx.clock(),
        policy_config(),
    )

    refund_id = ticket_id = None
    if verdict.outcome in ("allowed", "needs_approval"):
        refund = models.Refund(
            order_id=order.id,
            order_item_id=item.id,
            customer_id=ctx.customer_id,
            quantity=quantity,
            amount_cents=verdict.amount_cents,
            reason=reason_code,
            status="completed" if verdict.outcome == "allowed" else "pending",
            created_at=ctx.clock(),
        )
        ctx.session.add(refund)
        await ctx.session.flush()
        refund_id = refund.id
    elif verdict.outcome == "manual_review":
        ticket = await open_ticket(
            ctx,
            "manual_review",
            f"Refund review: order {order.id}, {item.name}",
            f"{verdict.message}\n\nRequested: {quantity} x {item.name} (order item {item.id}), "
            f"reason code {reason_code}, amount {verdict.amount_cents} cents. "
            f"Policy reason: {verdict.reason}.",
        )
        ticket_id = ticket.id
    return IssueRefundResult(
        status=verdict.outcome,
        reason=verdict.reason,
        message=verdict.message,
        amount_cents=verdict.amount_cents,
        refund_id=refund_id,
        ticket_id=ticket_id,
    )


async def decide_refund(
    session: AsyncSession,
    approver_id: int,
    refund_id: int,
    decision: Literal["approve", "reject"],
    now: datetime,
) -> DecisionResult:
    """Approve or reject a pending refund. This is not a tool: the agent cannot call it.

    The approver's role is read from the database, an approver cannot decide their own
    refund, and a refund can be decided once.
    """
    approver = await session.get(models.Customer, approver_id)
    if approver is None or approver.role != "approver":
        return DecisionResult(
            status="not_authorized", message="Only an approver can decide refunds."
        )
    refund = await session.scalar(
        select(models.Refund).where(models.Refund.id == refund_id).with_for_update()
    )
    if refund is None:
        return DecisionResult(status="not_found", message="No matching refund was found.")
    if refund.customer_id == approver_id:
        return DecisionResult(
            status="own_refund", message="An approver cannot decide their own refund."
        )
    if refund.status != "pending":
        return DecisionResult(
            status="not_pending", message=f"This refund is already {refund.status}."
        )
    refund.status = "approved" if decision == "approve" else "rejected"
    refund.decided_by = approver_id
    refund.decided_at = now
    await session.flush()
    return DecisionResult(status=refund.status, refund_id=refund.id)
