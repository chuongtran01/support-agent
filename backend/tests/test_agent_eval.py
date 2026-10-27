import pytest
import pytest_asyncio
from agent_checks import CaseResult, RunRecord
from agent_eval import (
    PreflightError,
    TimedBackend,
    TimedCall,
    build_backend,
    case_details,
    label,
    preflight,
    print_tool_timing,
    run_numbers,
    stability,
    tool_timing,
)
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


# Latency, cost and the judge's numbers

from agent_checks import TurnRecord
from agent_eval import cost, judging, latency, percentile
from judge import Verdict
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.backends import McpBackend
from app.agent.llm import ToolCall
from app.auth import to_current
from app.config import settings
from app.tools.registry import ToolOutput, tool_schemas
from tests.mcp_app import mcp_backend
from tests.tool_world import NOW


def timed(seconds, first=None, error=None, verdict=None, prompt=0, completion=0):
    turn = TurnRecord(
        [], "answer", [], 0, 1, prompt, completion, error, "q", seconds, first, [], verdict
    )
    return RunRecord([turn], [], [], [], customer_id=1), CaseResult()


def test_percentile_takes_the_nearest_rank_and_the_largest_for_a_few_values():
    assert percentile([], 0.95) is None
    assert percentile([4.0], 0.95) == 4.0
    assert percentile([1, 2, 3, 4], 0.5) == 2
    assert percentile([5, 1, 3], 0.95) == 5  # three values: the 95th percentile is the largest
    assert percentile(list(range(1, 101)), 0.95) == 95


def test_latency_is_per_message_and_by_category_and_skips_turns_with_no_answer():
    cases = [case(id="a", category="refund"), case(id="b", category="refund"), case(id="c")]
    cases[2] = case(id="c", category="knowledge")
    outcomes = [timed(2.0, 1.0), timed(6.0, 3.0), timed(4.0, None, error="down")]
    spread = latency(cases, outcomes)
    assert spread["turn"] == {"n": 3, "median": 4.0, "p95": 6.0}
    assert spread["first_text"] == {"n": 2, "median": 2.0, "p95": 3.0}  # the error turn is out
    assert spread["turn_by_category"]["refund"] == {"n": 2, "median": 4.0, "p95": 6.0}
    assert spread["turn_by_category"]["knowledge"]["n"] == 1


def test_cost_prices_the_agents_tokens_and_the_judges_apart(monkeypatch):
    monkeypatch.setattr(settings, "agent_model", "gpt-6-luna")
    monkeypatch.setattr(settings, "judge_model", "gpt-6-luna")
    verdict = Verdict(True, True, "fine", 1_000_000, 0)
    outcomes = [
        timed(1, prompt=1_000_000, completion=1_000_000, verdict=verdict),
        timed(1, prompt=0, completion=0),
    ]
    money = cost(outcomes)
    assert money["total_usd"] == 0.6  # $0.10 in and $0.50 out, for one million of each
    assert money["per_case_usd"] == 0.3
    assert money["judge_tokens"] == 1_000_000 and money["judge_usd"] == 0.1


def test_a_model_with_no_price_gives_no_cost(monkeypatch):
    monkeypatch.setattr(settings, "agent_model", "some-other-model")
    monkeypatch.setattr(settings, "judge_model", "some-other-model")
    money = cost([timed(1, prompt=10, completion=10)])
    assert money["per_case_usd"] is None and money["total_usd"] is None


def test_the_judges_verdicts_are_counted_and_the_failures_named_by_run():
    cases = [case(id="k1", category="knowledge")] * 2 + [case(id="t1", category="trap")]
    outcomes = [
        timed(1, verdict=Verdict(True, True, "", 1, 1)),
        timed(1, verdict=Verdict(False, True, "invented a number", 1, 1)),
        timed(1, verdict=Verdict(True, False, "changed the subject", 1, 1)),
    ]
    result = judging(cases, outcomes)
    assert result["judged"] == 3
    assert (result["supported"]["passed"], result["answers_question"]["passed"]) == (2, 2)
    assert result["not_supported"] == ["k1 (run 2)"]
    assert result["not_answering"] == ["t1 (run 1)"]


def test_unjudged_runs_are_not_counted():
    result = judging([case(id="r1")], [timed(1)])
    assert result["judged"] == 0 and result["supported"]["rate"] is None


def test_the_backend_flag_picks_where_tools_run():
    assert build_backend("local") is None
    backend = build_backend("mcp")
    assert isinstance(backend, McpBackend)
    assert backend.url == settings.mcp_server_url
    with pytest.raises(ValueError, match="unknown backend"):
        build_backend("grpc")


@pytest_asyncio.fixture
async def sessions(world, test_database_url):
    engine = create_async_engine(test_database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_the_preflight_passes_when_the_server_offers_the_registrys_tools(world, sessions):
    backend = mcp_backend(sessions, clock=lambda: NOW)
    await preflight(backend, to_current(world.data.customers["alex"]))


async def test_the_preflight_stops_a_run_when_the_server_is_not_there(world):
    backend = McpBackend("http://127.0.0.1:1/mcp", timeout=3)
    with pytest.raises(PreflightError, match="not answering; start it with make mcp"):
        await preflight(backend, to_current(world.data.customers["alex"]))


async def test_the_preflight_stops_a_run_when_the_server_offers_other_tools(
    world, sessions, monkeypatch
):
    backend = mcp_backend(sessions, clock=lambda: NOW)
    served = tool_schemas()
    monkeypatch.setattr("agent_eval.tool_schemas", lambda: served[:-1])
    with pytest.raises(PreflightError, match="different tools"):
        await preflight(backend, to_current(world.data.customers["alex"]))


class Scripted:
    """A backend that answers from a list, or raises what it is given."""

    def __init__(self, *answers):
        self.answers = list(answers)

    async def run(self, customer, call):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def ticks(*times):
    it = iter(times)
    return lambda: next(it)


async def test_the_timed_backend_returns_what_the_backend_returns_and_times_each_call():
    answer = ToolOutput("{}", "ok")
    timed = TimedBackend(Scripted(answer, ToolOutput("{}", "denied")), ticks(0, 0.5, 1, 3))
    call = ToolCall("1", "lookup_order", "{}")
    assert await timed.run(None, call) is answer
    await timed.run(None, ToolCall("2", "issue_refund", "{}"))
    assert timed.calls == [
        TimedCall("lookup_order", "ok", 0.5),
        TimedCall("issue_refund", "denied", 2),
    ]


async def test_the_timed_backend_records_a_call_that_raised_and_lets_it_through():
    timed = TimedBackend(Scripted(RuntimeError("boom")), ticks(0, 4))
    with pytest.raises(RuntimeError):
        await timed.run(None, ToolCall("1", "lookup_order", "{}"))
    assert timed.calls == [TimedCall("lookup_order", "raised", 4)]


def test_the_timing_splits_reads_from_writes_and_counts_the_calls_that_were_lost():
    calls = [
        TimedCall("lookup_order", "ok", 0.01),
        TimedCall("search_knowledge", "ok", 0.03),
        TimedCall("issue_refund", "allowed", 0.1),
        TimedCall("create_ticket", "unknown", 0.5),
        TimedCall("list_orders", "error", 0.02),
    ]
    timing = tool_timing(calls)
    assert timing["calls"] == 5
    assert timing["lost"] == 2
    assert timing["reads"]["n"] == 3
    assert timing["writes"]["n"] == 2
    assert timing["all"]["median"] == 0.03
    assert tool_timing([])["all"]["median"] is None


def test_the_report_shows_the_tool_timing_in_milliseconds_and_the_lost_calls(capsys):
    print_tool_timing(tool_timing([TimedCall("lookup_order", "ok", 0.012)]))
    out = capsys.readouterr().out
    assert "1 lost" not in out
    assert "(1, 0 lost" in out
    assert "12 ms median" in out
    assert "writes  -" in out
