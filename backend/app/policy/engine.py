"""Refund policy: one pure function that says whether a refund may go ahead.

The engine reads no database and no conversation. It gets a validated request, the facts
about the order and customer, and the current time, so the same inputs always give the same
verdict. The rules and numbers come from ``data/POLICY_FACTS.md``; see ``docs/PHASE3.md``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

Outcome = Literal["allowed", "denied", "needs_approval", "manual_review"]
Reason = Literal[
    "ok",
    "not_delivered",
    "not_refundable",
    "needs_human_review",
    "final_sale",
    "outside_window",
    "refund_frequency",
    "over_threshold",
]
Membership = Literal["none", "basic", "plus"]
ReasonCode = Literal["changed_mind", "defect", "our_error"]

GIFT_CARD = "gift_card"


@dataclass(frozen=True)
class PolicyConfig:
    """The numbers the rules use, in one place."""

    standard_window_days: int = 30
    plus_window_days: int = 60
    frequency_limit: int = 3
    frequency_window_days: int = 90
    approval_threshold_cents: int = 20_000


DEFAULT_CONFIG = PolicyConfig()


@dataclass(frozen=True)
class RefundRequest:
    quantity: int
    reason_code: ReasonCode


@dataclass(frozen=True)
class RefundFacts:
    membership: Membership
    delivered_at: datetime | None
    item_category: str
    is_final_sale: bool
    unit_price_cents: int
    # When each earlier refund that counts (pending, approved or completed) was made
    refund_dates: Sequence[datetime]


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    reason: Reason
    message: str
    amount_cents: int


def _days_since(then: datetime, now: datetime) -> int:
    """Whole calendar days between two moments, in UTC."""
    if then.tzinfo is None or now.tzinfo is None:
        raise ValueError("policy times must be timezone-aware")
    return (now.astimezone(UTC).date() - then.astimezone(UTC).date()).days


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def evaluate_refund(
    request: RefundRequest,
    facts: RefundFacts,
    now: datetime,
    config: PolicyConfig = DEFAULT_CONFIG,
) -> Verdict:
    """Check the rules in order; the first one that fires decides."""
    amount = facts.unit_price_cents * request.quantity

    def verdict(outcome: Outcome, reason: Reason, message: str) -> Verdict:
        return Verdict(outcome, reason, message, amount)

    # 1. Delivery
    if facts.delivered_at is None:
        return verdict(
            "denied",
            "not_delivered",
            "This order has not been delivered, so it cannot be refunded.",
        )

    # 2. Gift cards
    if facts.item_category == GIFT_CARD:
        return verdict("denied", "not_refundable", "Gift cards cannot be refunded.")

    # 3. Defects and our errors need a person: the engine cannot check the claim, so a
    # stated reason never unlocks the final-sale or window exceptions by itself.
    if request.reason_code in ("defect", "our_error"):
        return verdict(
            "manual_review",
            "needs_human_review",
            "Defect and error claims are reviewed by a person, so this has been passed to the support team.",
        )

    # 4. Final sale
    if facts.is_final_sale:
        return verdict(
            "denied", "final_sale", "This item was bought as final sale and cannot be refunded."
        )

    # 5. Return window, counted in calendar days from delivery
    window = config.plus_window_days if facts.membership == "plus" else config.standard_window_days
    days = _days_since(facts.delivered_at, now)
    if days > window:
        return verdict(
            "denied",
            "outside_window",
            f"The return window is {window} days from delivery, and this order was delivered "
            f"{days} days ago.",
        )

    # 6. Refund frequency: refunds made in the last 90 days, a refund 90 days old no longer counts
    recent = sum(
        1 for made in facts.refund_dates if _days_since(made, now) < config.frequency_window_days
    )
    if recent >= config.frequency_limit:
        return verdict(
            "manual_review",
            "refund_frequency",
            f"This account has reached the limit of {config.frequency_limit} refunds in "
            f"{config.frequency_window_days} days, so a person will review the request.",
        )

    # 7. Approval threshold: strictly over
    if amount > config.approval_threshold_cents:
        return verdict(
            "needs_approval",
            "over_threshold",
            f"Refunds over {_dollars(config.approval_threshold_cents)} need approval, so this "
            "refund is pending review.",
        )

    # 8. Nothing stops it
    return verdict("allowed", "ok", f"The refund of {_dollars(amount)} can go ahead.")
