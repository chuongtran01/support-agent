from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_approver
from app.approvals import decide_refund, list_pending
from app.auth import CurrentCustomer
from app.db import get_session
from app.tools.context import utc_now

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
    return [
        PendingRefund(
            refund_id=p.refund_id,
            order_id=p.order_id,
            customer_name=p.customer_name,
            item_name=p.item_name,
            quantity=p.quantity,
            amount_cents=p.amount_cents,
            reason=p.reason,
            created_at=p.created_at,
            is_own=p.customer_id == approver.id,
        )
        for p in await list_pending(session)
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
