"""Agent eval: play each case through the real agent and model, and check the result in code.

Run from ``backend/`` (``make eval-agent``):

    python ../evals/agent_eval.py [--split dev|test] [--only a001 a002 ...] [--repeat N]
    [--backend local|mcp] [--judge]

Each case starts from freshly seeded customers, orders and refunds (the knowledge base is
left as ingested), logs in as the case's customer, and sends the messages one after another
through the agent loop with the real model. This calls the OpenAI API: expect a few model
calls per case. Tune the prompt against the dev split. The test split is held out: read its
numbers, do not tune on them. A model answers differently each time, so ``--repeat N`` plays
each case N times; the rates then count runs, and the report says which cases are flaky.

``--backend mcp`` runs the agent's tool calls over MCP instead of in this process. Start the
server on the same database first (``make mcp``); the default, ``local``, needs no server.

This resets the business tables of the database in DATABASE_URL, as ``make seed`` does.
"""

import argparse
import asyncio
import json
import math
import statistics
import time
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from agent_cases import AgentCase, load_cases, resolve
from agent_checks import (
    CaseResult,
    CheckName,
    RefundRow,
    RunRecord,
    ToolRun,
    TurnRecord,
    check_case,
)
from judge import JudgeClient, OpenAIJudge, judge_answer
from sqlalchemy import select

from app import models
from app.agent import store
from app.agent.backends import McpBackend, ToolBackend
from app.agent.events import DoneEvent, ErrorEvent, TextEvent, ToolEvent
from app.agent.loop import AgentDeps, run_turn
from app.agent.messages import AssistantContent, ToolContent
from app.agent.openai_client import OpenAIClient
from app.auth import to_current
from app.config import settings
from app.db import SessionLocal, engine
from app.pricing import cost_usd
from app.retrieval.embeddings import OpenAIEmbedder
from app.seed import Seeded, seed
from app.tracing import get_tracer

RESULTS = Path(__file__).resolve().parent / "results"


async def _refunds() -> list[RefundRow]:
    async with SessionLocal() as session:
        rows = await session.scalars(select(models.Refund).order_by(models.Refund.id))
        return [RefundRow(r.id, r.customer_id, r.status, r.amount_cents) for r in rows]


async def _ticket_owners() -> list[int]:
    async with SessionLocal() as session:
        rows = await session.scalars(select(models.Ticket.customer_id))
        return list(rows)


def _tool_runs(messages: list[store.StoredMessage]) -> list[ToolRun]:
    """The tool calls in a stretch of a chat, each with the status its result carried."""
    results: dict[str, str | None] = {}
    for message in messages:
        if isinstance(message.content, ToolContent):
            try:
                results[message.content.tool_call_id] = json.loads(message.content.content)[
                    "status"
                ]
            except (ValueError, KeyError, TypeError):
                results[message.content.tool_call_id] = None
    runs = []
    for message in messages:
        if isinstance(message.content, AssistantContent):
            for call in message.content.tool_calls:
                try:
                    arguments = json.loads(call.function.arguments)
                except ValueError:
                    arguments = {}
                runs.append(ToolRun(call.function.name, arguments, results.get(call.id)))
    return runs


def _sources(messages: list[store.StoredMessage]) -> list[str]:
    """What search_knowledge returned in a stretch of a chat: the passages the agent was given."""
    searches = {
        call.id
        for m in messages
        if isinstance(m.content, AssistantContent)
        for call in m.content.tool_calls
        if call.function.name == "search_knowledge"
    }
    return [
        m.content.content
        for m in messages
        if isinstance(m.content, ToolContent) and m.content.tool_call_id in searches
    ]


async def _play(deps: AgentDeps, seeded: Seeded, case: AgentCase) -> RunRecord:
    orders = {key: order.id for key, order in seeded.orders.items()}
    items = {sku: item.id for sku, item in seeded.items.items()}
    customer = to_current(seeded.customers[case.customer])
    refunds_before = await _refunds()
    chat = await store.create_session(deps.sessions, customer.id)

    turns: list[TurnRecord] = []
    seen = 0  # stored messages already attributed to an earlier turn
    for text in resolve(case.messages, orders, items):
        done: DoneEvent | None = None
        error: str | None = None
        started = time.perf_counter()
        first_text: float | None = None
        async for event in run_turn(deps, customer, chat.id, text):
            if isinstance(event, DoneEvent):
                done = event
            elif isinstance(event, ErrorEvent):
                error = event.message
            elif isinstance(event, ToolEvent):
                first_text = None  # words before a tool call are not the answer
            elif isinstance(event, TextEvent) and first_text is None:
                first_text = time.perf_counter() - started
        seconds = time.perf_counter() - started
        history = await store.load_history(deps.sessions, chat.id, customer.id)
        fresh = history[seen:]
        seen = len(history)
        answers = [m.content.content for m in fresh if isinstance(m.content, AssistantContent)]
        turns.append(
            TurnRecord(
                tools=_tool_runs(fresh),
                answer=(answers[-1] or "") if answers and not error else "",
                cited=[c.document_id for c in done.citations] if done else [],
                dropped_citations=done.dropped_citations if done else 0,
                model_calls=done.model_calls if done else 0,
                prompt_tokens=done.prompt_tokens if done else 0,
                completion_tokens=done.completion_tokens if done else 0,
                error=error,
                question=text,
                seconds=seconds,
                first_text_seconds=first_text,
                sources=_sources(fresh),
            )
        )
        if error:
            break
    return RunRecord(
        turns=turns,
        refunds_before=refunds_before,
        refunds_after=await _refunds(),
        ticket_owners=await _ticket_owners(),
        customer_id=customer.id,
    )


# The judge reads answers that state facts from the knowledge base
JUDGED_CATEGORIES = ("knowledge", "trap")


async def _judged(run: RunRecord, judge: JudgeClient) -> RunRecord:
    """The run with the judge's verdict on each answer. The verdicts change no check."""
    turns = [
        replace(turn, verdict=await judge_answer(judge, turn.question, turn.answer, turn.sources))
        for turn in run.turns
    ]
    return replace(run, turns=turns)


async def run_case(
    deps: AgentDeps, case: AgentCase, judge: JudgeClient | None = None
) -> tuple[RunRecord, CaseResult]:
    async with SessionLocal.begin() as session:
        seeded = await seed(session)
    run = await _play(deps, seeded, case)
    if judge and case.category in JUDGED_CATEGORIES:
        run = await _judged(run, judge)
    orders = {key: order.id for key, order in seeded.orders.items()}
    items = {sku: item.id for sku, item in seeded.items.items()}
    expected = [(t.name, resolve(t.arguments, orders, items)) for t in case.expected_tools]
    return run, check_case(case, run, expected)


def _rate(passed: int, total: int) -> float | None:
    return round(passed / total, 3) if total else None


def percentile(values: list[float], q: float) -> float | None:
    """The value at or below which ``q`` (0 to 1) of the values fall, by nearest rank. With few
    values the 95th percentile is the largest, which is the honest thing to show."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _spread(values: list[float]) -> dict:
    return {
        "n": len(values),
        "median": round(statistics.median(values), 2) if values else None,
        "p95": round(p, 2) if (p := percentile(values, 0.95)) is not None else None,
    }


def latency(cases: list[AgentCase], outcomes: list[tuple[RunRecord, CaseResult]]) -> dict:
    """Seconds per customer message: the whole turn, and until the answer began. Turns that
    ended in an error have no answer and are left out of the second."""
    whole: dict[str, list[float]] = defaultdict(list)
    first: dict[str, list[float]] = defaultdict(list)
    for case, (run, _) in zip(cases, outcomes, strict=True):
        for turn in run.turns:
            whole[case.category].append(turn.seconds)
            if turn.first_text_seconds is not None and not turn.error:
                first[case.category].append(turn.first_text_seconds)

    def pooled(by: dict[str, list[float]]) -> list[float]:
        return [v for values in by.values() for v in values]

    return {
        "turn": _spread(pooled(whole)),
        "first_text": _spread(pooled(first)),
        "turn_by_category": {name: _spread(v) for name, v in sorted(whole.items())},
    }


def cost(outcomes: list[tuple[RunRecord, CaseResult]]) -> dict:
    """Dollars for the agent's tokens at the table's price, or None if the model has none. The
    judge's tokens are counted apart."""
    per_case = [
        cost_usd(
            settings.agent_model,
            sum(t.prompt_tokens for t in run.turns),
            sum(t.completion_tokens for t in run.turns),
        )
        for run, _ in outcomes
    ]
    judged = [t.verdict for run, _ in outcomes for t in run.turns if t.verdict]
    priced = [c for c in per_case if c is not None]
    judge_cost = cost_usd(
        settings.judge_model,
        sum(v.prompt_tokens for v in judged),
        sum(v.completion_tokens for v in judged),
    )
    return {
        "model": settings.agent_model,
        "per_case_usd": round(statistics.mean(priced), 6) if priced else None,
        "total_usd": round(sum(priced), 4) if priced else None,
        "judge_model": settings.judge_model,
        "judge_tokens": sum(v.prompt_tokens + v.completion_tokens for v in judged),
        "judge_usd": round(judge_cost, 4) if judge_cost is not None else None,
    }


def judging(cases: list[AgentCase], outcomes: list[tuple[RunRecord, CaseResult]]) -> dict:
    """The judge's verdicts: how many answers it called supported and responsive, and which ones
    it did not. The run numbers are those of ``case_details``."""
    judged = []
    for case, number, (run, _) in zip(cases, run_numbers(cases), outcomes, strict=True):
        judged += [(case, number, t.verdict) for t in run.turns if t.verdict]
    unsupported = [f"{c.id} (run {n})" for c, n, v in judged if not v.supported]
    off_topic = [f"{c.id} (run {n})" for c, n, v in judged if not v.answers_question]

    def rate(flags: list[bool]) -> dict:
        return {"passed": sum(flags), "total": len(flags), "rate": _rate(sum(flags), len(flags))}

    return {
        "judged": len(judged),
        "supported": rate([v.supported for _, _, v in judged]),
        "answers_question": rate([v.answers_question for _, _, v in judged]),
        "not_supported": unsupported,
        "not_answering": off_topic,
    }


def summarize(cases: list[AgentCase], outcomes: list[tuple[RunRecord, CaseResult]]) -> dict:
    """The numbers for the whole run."""
    by_category: dict[str, list[bool]] = defaultdict(list)
    by_trap: dict[str, list[bool]] = defaultdict(list)
    check_totals: dict[CheckName, list[bool]] = defaultdict(list)
    for case, (_, result) in zip(cases, outcomes, strict=True):
        by_category[case.category].append(result.passed)
        if case.trap_id:
            by_trap[case.trap_id].append(result.passed)
        for name, ok in result.checks.items():
            check_totals[name].append(ok)

    def rate(flags: list[bool]) -> dict:
        return {"passed": sum(flags), "total": len(flags), "rate": _rate(sum(flags), len(flags))}

    turns = [turn for run, _ in outcomes for turn in run.turns]
    per_case_calls = [sum(t.model_calls for t in run.turns) for run, _ in outcomes]
    per_case_tokens = [
        sum(t.prompt_tokens + t.completion_tokens for t in run.turns) for run, _ in outcomes
    ]
    return {
        "overall": rate([result.passed for _, result in outcomes]),
        "by_category": {name: rate(flags) for name, flags in sorted(by_category.items())},
        "by_trap": {name: rate(flags) for name, flags in sorted(by_trap.items())},
        "by_check": {name: rate(flags) for name, flags in sorted(check_totals.items())},
        "tool_call_accuracy": rate(
            check_totals["expected_tools"] + check_totals["forbidden_tools"]
        ),
        "refund_outcome_correct": rate(check_totals["refund_outcome"]),
        "citations_required_met": rate(check_totals["required_citations"]),
        "dropped_citations": sum(t.dropped_citations for t in turns),
        "model_calls_per_case": round(statistics.mean(per_case_calls), 2) if turns else None,
        "tokens_per_case": round(statistics.mean(per_case_tokens)) if turns else None,
        "stability": stability(cases, outcomes),
        "latency": latency(cases, outcomes),
        "cost": cost(outcomes),
        "judge": judging(cases, outcomes),
    }


def stability(cases: list[AgentCase], outcomes: list[tuple[RunRecord, CaseResult]]) -> dict:
    """How many runs of each case passed, and how many cases passed every run, some or none."""
    passes: dict[str, list[bool]] = defaultdict(list)
    for case, (_, result) in zip(cases, outcomes, strict=True):
        passes[case.id].append(result.passed)
    every = [i for i, flags in passes.items() if all(flags)]
    none = [i for i, flags in passes.items() if not any(flags)]
    flaky = {i: f"{sum(f)}/{len(f)}" for i, f in passes.items() if any(f) and not all(f)}
    return {"every_run": len(every), "no_run": len(none), "flaky": flaky, "cases": len(passes)}


def run_numbers(cases: list[AgentCase]) -> list[int]:
    """For each entry, which run of its case it is, counting from 1."""
    seen: dict[str, int] = defaultdict(int)
    numbers = []
    for case in cases:
        seen[case.id] += 1
        numbers.append(seen[case.id])
    return numbers


def label(case: AgentCase, number: int, repeated: bool) -> str:
    return f"{case.id} (run {number})" if repeated else case.id


def case_details(cases: list[AgentCase], outcomes: list[tuple[RunRecord, CaseResult]]) -> list:
    """One entry per run, for the results file. ``run`` counts a case's runs from 1."""
    details = []
    for case, number, (run, result) in zip(cases, run_numbers(cases), outcomes, strict=True):
        details.append(
            {
                "id": case.id,
                "run": number,
                "category": case.category,
                "passed": result.passed,
                "checks": result.checks,
                "problems": result.problems,
                "turns": [asdict(turn) for turn in run.turns],
            }
        )
    return details


def _percent(entry: dict) -> str:
    rate = entry["rate"]
    return f"{entry['passed']}/{entry['total']}" + (f" ({rate:.0%})" if rate is not None else "")


def _seconds(spread: dict) -> str:
    if spread["median"] is None:
        return "-"
    return f"{spread['median']:.1f} median, {spread['p95']:.1f} p95 (n={spread['n']})"


def print_latency_and_cost(summary: dict) -> None:
    seconds, money = summary["latency"], summary["cost"]
    print("\nSeconds per message")
    print(f"  whole turn           {_seconds(seconds['turn'])}")
    print(f"  until the answer     {_seconds(seconds['first_text'])}")
    for name, spread in seconds["turn_by_category"].items():
        print(f"  {name:<20} {_seconds(spread)}")
    if money["per_case_usd"] is None:
        print(f"\nCost: no price for {money['model']} in app/pricing.py")
        return
    print(
        f"\nCost ({money['model']}, an upper bound): ${money['per_case_usd']:.5f} per case, "
        f"${money['total_usd']:.4f} in all"
    )


def print_judge(judge: dict) -> None:
    if not judge["judged"]:
        return
    print(f"\nJudge (knowledge and trap answers, {judge['judged']} judged)")
    print(f"  claims backed by the sources   {_percent(judge['supported'])}")
    print(f"  answers the question           {_percent(judge['answers_question'])}")
    for title, key in (("not supported", "not_supported"), ("not answering", "not_answering")):
        if judge[key]:
            print(f"  {title}: {', '.join(judge[key])}")


def print_report(summary: dict, cases: list[AgentCase], outcomes: list) -> None:
    print(f"\nOverall: {_percent(summary['overall'])}")
    for title, key in (("By category", "by_category"), ("By trap", "by_trap")):
        print(f"\n{title}")
        for name, entry in summary[key].items():
            print(f"  {name:<28}{_percent(entry)}")
    print("\nBy check")
    for name, entry in summary["by_check"].items():
        print(f"  {name:<28}{_percent(entry)}")
    print(f"\nTool-call accuracy      {_percent(summary['tool_call_accuracy'])}")
    print(f"Refund outcome correct  {_percent(summary['refund_outcome_correct'])}")
    print(f"Required citations met  {_percent(summary['citations_required_met'])}")
    print(f"Cited but not retrieved {summary['dropped_citations']}")
    print(f"Model calls per case    {summary['model_calls_per_case']}")
    print(f"Tokens per case         {summary['tokens_per_case']}")
    print_latency_and_cost(summary)
    print_judge(summary["judge"])
    steady = summary["stability"]
    if len(cases) > steady["cases"]:  # some case was run more than once
        print(
            f"\nPassed every run: {steady['every_run']}/{steady['cases']} cases, "
            f"no run: {steady['no_run']}"
        )
        for case_id, rate in steady["flaky"].items():
            print(f"  flaky {case_id} {rate}")
    repeated = len(cases) > summary["stability"]["cases"]
    failed = [
        (c, n, r)
        for c, n, (_, r) in zip(cases, run_numbers(cases), outcomes, strict=True)
        if not r.passed
    ]
    if failed:
        print("\nFailed cases")
        for case, number, result in failed:
            print(f"  {label(case, number, repeated)} [{case.category}] {case.description}")
            for problem in result.problems:
                print(f"      {problem}")


def build_backend(name: str) -> ToolBackend | None:
    """The agent's tool backend: None runs tools in this process, as the loop does by default."""
    if name == "local":
        return None
    if name == "mcp":
        return McpBackend(settings.mcp_server_url)
    raise ValueError(f"unknown backend {name!r}: use local or mcp")


async def main(
    split: str, only: list[str] | None, repeat: int, judged: bool, backend: str = "local"
) -> None:
    cases = load_cases(split)
    if only:
        cases = [c for c in cases if c.id in only]
    cases = [case for case in cases for _ in range(repeat)]  # a case's runs sit together
    tracer = get_tracer()  # Langfuse if the keys are set, otherwise nothing
    deps = AgentDeps(
        sessions=SessionLocal,
        llm=OpenAIClient(),
        embedder=OpenAIEmbedder(),
        tracer=tracer,
        tools=build_backend(backend),
    )
    judge = OpenAIJudge(settings.openai_api_key, settings.judge_model) if judged else None
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")  # names the result file and the traces
    outcomes = []
    try:
        for number, (case, run_number) in enumerate(zip(cases, run_numbers(cases), strict=True), 1):
            # Each trace says which case and run it is, and which result file holds the verdict
            traced = replace(
                deps,
                tracer=tracer.tagged(
                    {
                        "case": case.id,
                        "category": case.category,
                        "run": str(run_number),
                        "eval_run": f"agent-{split}-{stamp}",
                    },
                    ["eval", split],
                ),
            )
            run, result = await run_case(traced, case, judge)
            outcomes.append((run, result))
            name = label(case, run_number, repeat > 1)
            print(f"[{number}/{len(cases)}] {name} {'pass' if result.passed else 'FAIL'}")
    finally:
        tracer.flush()
        await engine.dispose()

    summary = summarize(cases, outcomes)
    print_report(summary, cases, outcomes)
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"agent-{split}-{stamp}.json"
    detail = case_details(cases, outcomes)
    path.write_text(json.dumps({"split": split, "summary": summary, "cases": detail}, indent=2))
    print(f"\nWrote {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--only", nargs="+", help="run just these case ids")
    parser.add_argument("--repeat", type=int, default=1, help="runs of each case (default 1)")
    parser.add_argument(
        "--backend",
        choices=("local", "mcp"),
        default="local",
        help="where tool calls run: in this process, or over MCP (needs make mcp running)",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="have the judge model read the knowledge and trap answers (about 10%% more tokens)",
    )
    arguments = parser.parse_args()
    if arguments.repeat < 1:
        parser.error("--repeat must be at least 1")
    asyncio.run(
        main(arguments.split, arguments.only, arguments.repeat, arguments.judge, arguments.backend)
    )
