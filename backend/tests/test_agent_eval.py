from agent_checks import CaseResult, RunRecord
from agent_eval import case_details, label, run_numbers, stability
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


def test_each_run_of_a_case_is_numbered_in_the_results_file():
    cases = [case(id="a")] * 2 + [case(id="b")]
    details = case_details(cases, [outcome(p) for p in (True, False, True)])
    assert [(d["id"], d["run"], d["passed"]) for d in details] == [
        ("a", 1, True),
        ("a", 2, False),
        ("b", 1, True),
    ]


def test_run_numbers_and_labels():
    cases = [case(id="a")] * 2 + [case(id="b")]
    assert run_numbers(cases) == [1, 2, 1]
    assert label(cases[1], 2, repeated=True) == "a (run 2)"
    assert label(cases[1], 1, repeated=False) == "a"
