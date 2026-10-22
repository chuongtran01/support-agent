from agent_checks import CaseResult, RunRecord
from agent_eval import stability
from test_agent_checks import case


def outcome(passed: bool):
    result = CaseResult()
    result.record("no_error", passed, "failed")
    return RunRecord([], [], [], [], customer_id=1), result


def test_stability_separates_steady_flaky_and_failing_cases():
    cases = [case(id="a")] * 2 + [case(id="b")] * 2 + [case(id="c")] * 2
    outcomes = [outcome(p) for p in (True, True, True, False, False, False)]
    assert stability(cases, outcomes) == {
        "every_run": 1,
        "no_run": 1,
        "flaky": {"b": "1/2"},
        "cases": 3,
    }
