"""What each tool hands back. The registry turns these into JSON for the model."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.policy.engine import dollars


class View(BaseModel):
    """A piece of data inside a result."""

    model_config = ConfigDict(frozen=True)


class Result(View):
    """What a tool hands back.

    ``receipt`` is for the case where the agent runs out of steps right after this tool changed
    something: the model gets no turn to explain it, so the loop tells the customer with this
    sentence instead. Return one plain sentence from the result's own fields if the call
    changed something (a refund made, a ticket opened) and None if it did not. The model never
    sees it, and it is not used when the model writes the final answer.
    """

    status: str

    def receipt(self) -> str | None:
        return None


class OrderItemView(View):
    order_item_id: int
    sku: str
    name: str
    price_cents: int
    quantity: int
    category: str
    is_final_sale: bool
    units_refunded: int


class RefundView(View):
    refund_id: int
    order_item_id: int
    quantity: int
    amount_cents: int
    status: str
    created_at: str


class OrderView(View):
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

    def receipt(self) -> str | None:
        amount = dollars(self.amount_cents or 0)
        match self.status:
            case "allowed":
                return f"A refund of {amount} has been made."
            case "needs_approval":
                return f"A refund of {amount} is waiting for approval."
            case "manual_review":
                return "Your refund request has been passed to a person."
        return None


class TicketResult(Result):
    status: Literal["ok", "invalid_request"]
    message: str | None = None
    ticket_id: int | None = None

    def receipt(self) -> str | None:
        if self.status == "ok":
            return f"A support ticket (#{self.ticket_id}) has been opened."
        return None


class DecisionResult(Result):
    status: Literal[
        "approved", "rejected", "not_authorized", "not_found", "own_refund", "not_pending"
    ]
    message: str | None = None
    refund_id: int | None = None


class PassageView(View):
    document_id: str
    title: str
    status: str
    updated_at: str
    heading_path: str
    content: str


class SearchKnowledgeResult(Result):
    status: Literal["ok", "no_results"]
    low_confidence: bool
    message: str | None = None
    passages: list[PassageView] = []
