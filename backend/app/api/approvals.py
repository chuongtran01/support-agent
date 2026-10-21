from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.auth import CurrentCustomer, require_approver
from app.db import get_session
from app.tools.context import utc_now
from app.tools.refunds import decide_refund

router = APIRouter()

Approver = Annotated[CurrentCustomer, Depends(require_approver)]
Session = Annotated[AsyncSession, Depends(get_session)]

# What decide_refund can say that is not a success, and the status each one becomes
_REFUSALS = {
    "not_authorized": status.HTTP_403_FORBIDDEN,
    "own_refund": status.HTTP_403_FORBIDDEN,
    "not_found": status.HTTP_404_NOT_FOUND,
    "not_pending": status.HTTP_409_CONFLICT,
}


class PendingRefund(BaseModel):
    refund_id: int
    order_id: int
    customer_name: str
    item_name: str
    quantity: int
    amount_cents: int
    reason: str
    created_at: datetime
    is_own: bool  # an approver cannot decide their own refund


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]


class DecisionOut(BaseModel):
    status: Literal["approved", "rejected"]
    refund_id: int


@router.get("/approvals")
async def list_approvals(approver: Approver, session: Session) -> list[PendingRefund]:
    rows = await session.execute(
        select(models.Refund, models.Customer.name, models.OrderItem.name)
        .join(models.Customer, models.Customer.id == models.Refund.customer_id)
        .join(models.OrderItem, models.OrderItem.id == models.Refund.order_item_id)
        .where(models.Refund.status == "pending")
        .order_by(models.Refund.created_at, models.Refund.id)
    )
    return [
        PendingRefund(
            refund_id=refund.id,
            order_id=refund.order_id,
            customer_name=customer_name,
            item_name=item_name,
            quantity=refund.quantity,
            amount_cents=refund.amount_cents,
            reason=refund.reason,
            created_at=refund.created_at,
            is_own=refund.customer_id == approver.id,
        )
        for refund, customer_name, item_name in rows
    ]


@router.post("/approvals/{refund_id}/decision")
async def decide(
    refund_id: int, body: Decision, approver: Approver, session: Session
) -> DecisionOut:
    async with session.begin():
        result = await decide_refund(session, approver.id, refund_id, body.decision, utc_now())
    if result.status in _REFUSALS:
        raise HTTPException(_REFUSALS[result.status], result.message)
    return DecisionOut(status=result.status, refund_id=refund_id)
