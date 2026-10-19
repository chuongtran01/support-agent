from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from app.policy.engine import (
    PolicyConfig,
    RefundFacts,
    RefundRequest,
    evaluate_refund,
)

CASES = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "policy.jsonl"
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


class PolicyCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    group: str
    description: str
    membership: str
    delivered_days_ago: int | None
    item_category: str
    is_final_sale: bool
    unit_price_cents: int
    refund_ages_days: list[int]
    reason_code: str
    quantity: int
    expected_outcome: str
    expected_reason: str
    note: str | None


def load_cases() -> list[PolicyCase]:
    return [PolicyCase.model_validate_json(line) for line in CASES.read_text().splitlines() if line]


def ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def facts(**overrides) -> RefundFacts:
    fields = {
        "membership": "basic",
        "delivered_at": ago(10),
        "item_category": "other",
        "is_final_sale": False,
        "unit_price_cents": 8000,
        "refund_dates": [],
    }
    return RefundFacts(**(fields | overrides))


def request(quantity: int = 1, reason_code: str = "changed_mind") -> RefundRequest:
    return RefundRequest(quantity=quantity, reason_code=reason_code)


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case.id)
def test_policy_case(case: PolicyCase):
    verdict = evaluate_refund(
        RefundRequest(quantity=case.quantity, reason_code=case.reason_code),
        RefundFacts(
            membership=case.membership,
            delivered_at=None if case.delivered_days_ago is None else ago(case.delivered_days_ago),
            item_category=case.item_category,
            is_final_sale=case.is_final_sale,
            unit_price_cents=case.unit_price_cents,
            refund_dates=[ago(age) for age in case.refund_ages_days],
        ),
        NOW,
    )
    assert (verdict.outcome, verdict.reason) == (case.expected_outcome, case.expected_reason), (
        case.description
    )


def test_cases_file_is_well_formed():
    cases = load_cases()
    assert len(cases) == 46
    assert len({case.id for case in cases}) == len(cases)


def test_verdict_carries_the_amount_computed_from_price_and_quantity():
    verdict = evaluate_refund(request(quantity=3), facts(unit_price_cents=1500), NOW)
    assert verdict.amount_cents == 4500 - 795


def test_change_of_mind_pays_the_label_fee_once_for_the_whole_request():
    verdict = evaluate_refund(request(quantity=2), facts(unit_price_cents=5000), NOW)
    assert verdict.amount_cents == 10_000 - 795
    assert "$7.95 return label fee" in verdict.message


@pytest.mark.parametrize("reason_code", ["defect", "our_error"])
def test_defects_and_our_errors_are_not_charged_the_label_fee(reason_code):
    verdict = evaluate_refund(request(reason_code=reason_code), facts(), NOW)
    assert verdict.amount_cents == 8000


@pytest.mark.parametrize(("price", "refunded"), [(795, 795), (500, 500), (796, 1)])
def test_the_label_fee_is_waived_when_the_refund_would_not_cover_it(price, refunded):
    verdict = evaluate_refund(request(), facts(unit_price_cents=price), NOW)
    assert (verdict.outcome, verdict.amount_cents) == ("allowed", refunded)
    assert ("label fee" in verdict.message) == (price == 796)


def test_denied_verdicts_still_report_the_amount():
    verdict = evaluate_refund(request(), facts(is_final_sale=True), NOW)
    assert verdict.outcome == "denied"
    assert verdict.amount_cents == 8000 - 795


def test_window_counts_calendar_days_not_elapsed_hours():
    just_after_midnight = datetime(2026, 6, 15, 0, 1, tzinfo=UTC)
    late_evening = datetime(2026, 5, 16, 23, 59, tzinfo=UTC)  # 30 calendar days earlier
    assert (
        evaluate_refund(request(), facts(delivered_at=late_evening), just_after_midnight).outcome
        == "allowed"
    )

    late_now = datetime(2026, 6, 15, 23, 59, tzinfo=UTC)
    early_morning = datetime(2026, 5, 15, 0, 1, tzinfo=UTC)  # 31 calendar days earlier
    assert (
        evaluate_refund(request(), facts(delivered_at=early_morning), late_now).reason
        == "outside_window"
    )


def test_config_changes_the_numbers_the_rules_use():
    config = PolicyConfig(standard_window_days=45, approval_threshold_cents=5_000)
    assert (
        evaluate_refund(
            request(), facts(delivered_at=ago(40), unit_price_cents=4000), NOW, config
        ).outcome
        == "allowed"
    )
    assert (
        evaluate_refund(request(), facts(delivered_at=ago(46)), NOW, config).reason
        == "outside_window"
    )
    assert (
        evaluate_refund(request(), facts(unit_price_cents=5796), NOW, config).reason
        == "over_threshold"
    )


def test_messages_state_the_numbers_involved():
    window = evaluate_refund(request(), facts(delivered_at=ago(45)), NOW)
    assert "30 days" in window.message and "45 days" in window.message
    approval = evaluate_refund(request(), facts(unit_price_cents=30000), NOW)
    assert "$200.00" in approval.message
    frequency = evaluate_refund(request(), facts(refund_dates=[ago(1), ago(2), ago(3)]), NOW)
    assert "3 refunds in 90 days" in frequency.message


def test_times_must_be_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_refund(request(), facts(delivered_at=datetime(2026, 6, 1)), NOW)
