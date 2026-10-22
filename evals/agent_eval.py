"""Agent eval: play each case through the real agent and model, and check the result in code.

Run from ``backend/`` (``make eval-agent``):

    python ../evals/agent_eval.py [--split dev|test] [--only a001 a002 ...]

Each case starts from freshly seeded customers, orders and refunds (the knowledge base is
left as ingested), logs in as the case's customer, and sends the messages one after another
through the agent loop with the real model. This calls the OpenAI API: expect a few model
calls per case. Tune the prompt against the dev split. The test split is held out: read its
numbers, do not tune on them.

This resets the business tables of the database in DATABASE_URL, as ``make seed`` does.
"""

import argparse
import asyncio
import json
import statistics
from collections import defaultdict
from dataclasses import asdict
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
from sqlalchemy import select

from app import models
from app.agent import store
from app.agent.events import DoneEvent, ErrorEvent
from app.agent.llm import OpenAIClient
from app.agent.loop import AgentDeps, run_turn
from app.agent.messages import AssistantContent, ToolContent
from app.auth import to_current
from app.db import SessionLocal, engine
from app.retrieval.embeddings import OpenAIEmbedder
from app.seed import Seeded, seed

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
        async for event in run_turn(deps, customer, chat.id, text):
            if isinstance(event, DoneEvent):
                done = event
            elif isinstance(event, ErrorEvent):
                error = event.message
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


async def run_case(deps: AgentDeps, case: AgentCase) -> tuple[RunRecord, CaseResult]:
    async with SessionLocal.begin() as session:
        seeded = await seed(session)
    run = await _play(deps, seeded, case)
    orders = {key: order.id for key, order in seeded.orders.items()}
    items = {sku: item.id for sku, item in seeded.items.items()}
    expected = [(t.name, resolve(t.arguments, orders, items)) for t in case.expected_tools]
    return run, check_case(case, run, expected)


def _rate(passed: int, total: int) -> float | None:
    return round(passed / total, 3) if total else None


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
    }


def _percent(entry: dict) -> str:
    rate = entry["rate"]
    return f"{entry['passed']}/{entry['total']}" + (f" ({rate:.0%})" if rate is not None else "")


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
    failed = [(c, r) for c, (_, r) in zip(cases, outcomes, strict=True) if not r.passed]
    if failed:
        print("\nFailed cases")
        for case, result in failed:
            print(f"  {case.id} [{case.category}] {case.description}")
            for problem in result.problems:
                print(f"      {problem}")


async def main(split: str, only: list[str] | None) -> None:
    cases = load_cases(split)
    if only:
        cases = [c for c in cases if c.id in only]
    deps = AgentDeps(sessions=SessionLocal, llm=OpenAIClient(), embedder=OpenAIEmbedder())
    outcomes = []
    try:
        for number, case in enumerate(cases, start=1):
            run, result = await run_case(deps, case)
            outcomes.append((run, result))
            print(f"[{number}/{len(cases)}] {case.id} {'pass' if result.passed else 'FAIL'}")
    finally:
        await engine.dispose()

    summary = summarize(cases, outcomes)
    print_report(summary, cases, outcomes)
    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = RESULTS / f"agent-{split}-{stamp}.json"
    detail = [
        {
            "id": case.id,
            "category": case.category,
            "passed": result.passed,
            "checks": result.checks,
            "problems": result.problems,
            "turns": [asdict(turn) for turn in run.turns],
        }
        for case, (run, result) in zip(cases, outcomes, strict=True)
    ]
    path.write_text(json.dumps({"split": split, "summary": summary, "cases": detail}, indent=2))
    print(f"\nWrote {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--only", nargs="+", help="run just these case ids")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.split, arguments.only))
