"""Deciding held refunds. This is not a tool: the agent cannot call anything in this module.

A refund the policy engine holds for approval is written as ``pending``. A person with the
``approver`` role decides it here, through the API.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models


class DecisionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal[
        "approved", "rejected", "not_authorized", "not_found", "own_refund", "not_pending"
    ]
    message: str | None = None
    refund_id: int | None = None


@dataclass(frozen=True)
class PendingApproval:
    refund_id: int
    order_id: int
    customer_id: int
    customer_name: str
    item_name: str
    quantity: int
    amount_cents: int
    reason: str
    created_at: datetime


async def list_pending(session: AsyncSession) -> list[PendingApproval]:
    """Refunds waiting for a decision, oldest first."""
    rows = await session.execute(
        select(models.Refund, models.Customer.name, models.OrderItem.name)
        .join(models.Customer, models.Customer.id == models.Refund.customer_id)
        .join(models.OrderItem, models.OrderItem.id == models.Refund.order_item_id)
        .where(models.Refund.status == "pending")
        .order_by(models.Refund.created_at, models.Refund.id)
    )
    return [
        PendingApproval(
            refund_id=refund.id,
            order_id=refund.order_id,
            customer_id=refund.customer_id,
            customer_name=customer_name,
            item_name=item_name,
            quantity=refund.quantity,
            amount_cents=refund.amount_cents,
            reason=refund.reason,
            created_at=refund.created_at,
        )
        for refund, customer_name, item_name in rows
    ]


async def decide_refund(
    session: AsyncSession,
    approver_id: int,
    refund_id: int,
    decision: Literal["approve", "reject"],
    now: datetime,
) -> DecisionResult:
    """Approve or reject a pending refund.

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
