import json
from collections import Counter
from pathlib import Path

import pytest
from agent_cases import (
    AgentCase,
    InvalidDataset,
    case_placeholders,
    load_all,
    load_cases,
    resolve,
)
from sqlalchemy import select

from app import models
from app.orders import COUNTED_STATUSES
from app.policy.engine import RefundFacts, RefundRequest, evaluate_refund
from app.tools.refunds import policy_config
from tests.tool_world import NOW


@pytest.fixture(scope="module")
def splits():
    return load_all()


def every_case(splits):
    return [case for cases in splits.values() for case in cases]


def test_both_splits_load_and_do_not_share_ids_or_scenarios(splits):
    assert len(splits["dev"]) > len(splits["test"]) > 10


def test_every_category_is_in_both_splits(splits):
    for cases in splits.values():
        assert {c.category for c in cases} == {
            "knowledge",
            "trap",
            "injection",
            "off_topic",
            "scoping",
            "refund",
            "ticket",
            "multi_turn",
            "orders",
        }


def test_every_trap_is_in_the_test_split(splits):
    traps = {c.trap_id for c in splits["test"]}
    assert {
        "conflicting-return-window",
        "stale-shipping-page",
        "buried-exception",
        "unanswerable",
        "doc-injection",
    } <= traps


def test_a_case_that_expects_a_refund_names_the_refund_tool(splits):
    for case in every_case(splits):
        if case.expected_new_refunds:
            assert "issue_refund" in [t.name for t in case.expected_tools], case.id


def test_cases_that_forbid_refunds_expect_none(splits):
    for case in every_case(splits):
        if "issue_refund" in case.forbidden_tools:
            assert not case.expected_new_refunds, case.id


def test_trap_cases_that_must_escalate_forbid_refunds_and_the_others_do_not_escalate(splits):
    for case in every_case(splits):
        if case.trap_id in ("conflicting-return-window", "unanswerable"):
            assert "escalate" in [t.name for t in case.expected_tools], case.id
        elif case.category == "knowledge":
            assert "escalate" in case.forbidden_tools, case.id


def test_unknown_fields_and_documents_are_rejected(tmp_path):
    row = json.loads(Path("../evals/datasets/agent.dev.jsonl").read_text().splitlines()[0])
    for change in (
        {"surprise": 1},
        {"customer": "nobody"},
        {"required_citations": ["no-such-doc"]},
    ):
        directory = tmp_path / str(len(list(tmp_path.iterdir())))
        directory.mkdir()
        (directory / "agent.dev.jsonl").write_text(json.dumps(row | change) + "\n")
        with pytest.raises(InvalidDataset):
            load_cases("dev", directory)


def test_a_forbidden_turn_must_exist_and_a_tool_cannot_be_both_expected_and_forbidden():
    base = json.loads(Path("../evals/datasets/agent.dev.jsonl").read_text().splitlines()[0])
    with pytest.raises(ValueError, match="turn 3"):
        AgentCase.model_validate(base | {"forbidden_in_turn": {"3": ["issue_refund"]}})
    with pytest.raises(ValueError, match="both expected and forbidden"):
        AgentCase.model_validate(base | {"forbidden_tools": ["search_knowledge"]})


def test_placeholders_become_ids_and_a_bare_placeholder_becomes_an_int():
    orders, items = {"alex-5d": 1}, {"BOOT-TR": 7}
    assert resolve("order {order:alex-5d}", orders, items) == "order 1"
    assert resolve({"order_item_id": "{item:BOOT-TR}", "quantity": 1}, orders, items) == {
        "order_item_id": 7,
        "quantity": 1,
    }


# Against the seed ---------------------------------------------------------


async def test_every_placeholder_names_something_in_the_seed(world, splits):
    orders = {key: row.id for key, row in world.data.orders.items()}
    items = {sku: row.id for sku, row in world.data.items.items()}
    for case in every_case(splits):
        for kind, key in case_placeholders(case):
            assert key in (orders if kind == "order" else items), f"{case.id}: {kind}:{key}"


async def test_expected_refunds_match_what_the_policy_engine_decides(world, splits):
    """The numbers in the dataset are written by hand; the engine must agree with them."""
    orders = {key: row.id for key, row in world.data.orders.items()}
    items = {sku: row.id for sku, row in world.data.items.items()}
    checked = 0
    for case in every_case(splits):
        calls = [t for t in case.expected_tools if t.name == "issue_refund"]
        if not calls:
            continue
        args = resolve(calls[0].arguments, orders, items)
        item = await world.session.get(models.OrderItem, args["order_item_id"])
        order = await world.session.get(models.Order, item.order_id)
        customer = await world.session.get(models.Customer, order.customer_id)
        assert customer.email == f"{case.customer}@example.com", case.id
        earlier = (
            await world.session.scalars(
                select(models.Refund).where(
                    models.Refund.customer_id == customer.id,
                    models.Refund.status.in_(COUNTED_STATUSES),
                )
            )
        ).all()
        verdict = evaluate_refund(
            RefundRequest(quantity=args["quantity"], reason_code=args["reason_code"]),
            RefundFacts(
                membership=customer.membership,
                delivered_at=order.delivered_at,
                item_category=item.category,
                is_final_sale=item.is_final_sale,
                unit_price_cents=item.price_cents,
                refund_dates=[r.created_at for r in earlier],
            ),
            NOW,
            policy_config(),
        )
        made = {"allowed": "completed", "needs_approval": "pending"}.get(verdict.outcome)
        expected = [(r.status, r.amount_cents) for r in case.expected_new_refunds]
        assert expected == ([(made, verdict.amount_cents)] if made else []), case.id
        checked += 1
    assert checked >= 10


def test_the_dataset_has_a_spread_of_outcomes(splits):
    outcomes = Counter(
        tuple((r.status, r.amount_cents) for r in c.expected_new_refunds)
        for c in every_case(splits)
    )
    assert outcomes[()] > 10
    assert {"completed", "pending"} <= {s for key in outcomes for s, _ in key}
