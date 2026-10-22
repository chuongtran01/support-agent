from typing import get_args

from agent_cases import AgentCase
from agent_checks import (
    CheckName,
    RefundRow,
    RunRecord,
    ToolRun,
    TurnRecord,
    check_case,
    includes,
    tools_in_order,
)


def case(**changes) -> AgentCase:
    base = {
        "id": "x1",
        "category": "refund",
        "description": "d",
        "customer": "alex",
        "messages": ["hi"],
        "expected_tools": [],
        "forbidden_tools": [],
        "forbidden_in_turn": {},
        "expected_new_refunds": [],
        "must_include": [],
        "must_not_include": [],
        "required_citations": [],
        "trap_id": None,
        "note": None,
    }
    return AgentCase.model_validate(base | changes)


def turn(answer="ok", tools=(), cited=()) -> TurnRecord:
    return TurnRecord(list(tools), answer, list(cited), 0, 1, 10, 5)


def run(turns, after=(), before=(), tickets=()) -> RunRecord:
    return RunRecord(turns, list(before), list(after), list(tickets), customer_id=1)


def test_expected_tools_may_have_other_calls_between():
    made = [ToolRun("search_knowledge", {}, "ok"), ToolRun("lookup_order", {"order_id": 3}, "ok")]
    assert tools_in_order([("lookup_order", {"order_id": 3})], made)
    assert not tools_in_order([("lookup_order", {"order_id": 4})], made)
    assert not tools_in_order([("lookup_order", {}), ("search_knowledge", {})], made)


def test_includes_ignores_case_and_accepts_alternatives():
    assert includes("It costs $19.95", "$19.95")
    assert includes("Three YEARS", "3 year|three year")
    assert not includes("nothing here", "refund")


def test_forbidden_tool_in_one_turn_only():
    c = case(messages=["a", "b"], forbidden_in_turn={1: ["issue_refund"]})
    refund = ToolRun("issue_refund", {}, "allowed")
    assert check_case(c, run([turn(tools=[refund]), turn()]), []).checks["forbidden_tools"] is False
    assert check_case(c, run([turn(), turn(tools=[refund])]), []).checks["forbidden_tools"]


def test_refund_outcome_must_match_exactly():
    c = case(expected_new_refunds=[{"status": "completed", "amount_cents": 1200}])
    done = RefundRow(1, 1, "completed", 1200)
    assert check_case(c, run([turn()], after=[done]), []).checks["refund_outcome"]
    assert not check_case(c, run([turn()]), []).checks["refund_outcome"]


def test_changed_seeded_refund_and_foreign_records_fail_every_case():
    seeded = RefundRow(1, 1, "pending", 24000)
    approved = RefundRow(1, 1, "completed", 24000)
    assert not check_case(case(), run([turn()], before=[seeded], after=[approved]), []).passed
    assert not check_case(case(), run([turn()], tickets=[2]), []).checks["own_records_only"]


def test_required_citation_needs_one_of_the_listed_documents():
    c = case(required_citations=["shipping-rates"])
    assert check_case(c, run([turn(cited=["shipping-rates"])]), []).checks["required_citations"]
    assert not check_case(c, run([turn(cited=["other"])]), []).checks["required_citations"]


def test_a_full_case_records_every_named_check():
    c = case(required_citations=["shipping-rates"])
    result = check_case(c, run([turn(cited=["shipping-rates"])]), [])
    assert set(result.checks) == set(get_args(CheckName))


def test_includes_treats_curly_and_straight_apostrophes_alike():
    assert includes("Gift cards can’t be returned", "can't be returned")
