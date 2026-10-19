"""What each tool hands back. The registry turns these into JSON for the model."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Result(BaseModel):
    model_config = ConfigDict(frozen=True)


class OrderItemView(Result):
    order_item_id: int
    sku: str
    name: str
    price_cents: int
    quantity: int
    category: str
    is_final_sale: bool
    units_refunded: int


class RefundView(Result):
    refund_id: int
    order_item_id: int
    quantity: int
    amount_cents: int
    status: str
    created_at: str


class OrderView(Result):
    id: int
    status: str
    placed_at: str
    delivered_at: str | None
    total_cents: int
    items: list[OrderItemView]
    refunds: list[RefundView]


class LookupOrderResult(Result):
    status: Literal["ok", "not_found"]
    message: str | None = None
    order: OrderView | None = None


class IssueRefundResult(Result):
    status: Literal[
        "allowed", "needs_approval", "manual_review", "denied", "invalid_request", "not_found"
    ]
    message: str
    reason: str | None = None
    amount_cents: int | None = None
    refund_id: int | None = None
    ticket_id: int | None = None


class TicketResult(Result):
    status: Literal["ok", "invalid_request"]
    message: str | None = None
    ticket_id: int | None = None


class DecisionResult(Result):
    status: Literal[
        "approved", "rejected", "not_authorized", "not_found", "own_refund", "not_pending"
    ]
    message: str | None = None
    refund_id: int | None = None
