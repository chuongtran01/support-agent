"""The checks for one agent eval case, as plain functions over what the run recorded.

Nothing here touches the database or the model, so the checks are tested on their own.
Every check is code: no judge model decides whether an answer is good.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from agent_cases import AgentCase, ExpectedRefund
from judge import Verdict

CheckName = Literal[
    "no_error",
    "expected_tools",
    "forbidden_tools",
    "refund_outcome",
    "must_include",
    "must_not_include",
    "required_citations",
    "seeded_refunds_unchanged",
    "own_records_only",
]


@dataclass(frozen=True)
class ToolRun:
    """A tool call the agent made: what it asked for and the status that came back."""

    name: str
    arguments: dict[str, Any]
    status: str | None


@dataclass(frozen=True)
class TurnRecord:
    """One customer message and what the agent did about it."""

    tools: list[ToolRun]
    answer: str
    cited: list[str]  # document ids in the answer that were retrieved in the turn
    dropped_citations: int
    model_calls: int
    prompt_tokens: int
    completion_tokens: int
    error: str | None = None
    question: str = ""
    seconds: float = 0.0  # from sending the message to the end of the turn
    first_text_seconds: float | None = None  # to the first word of the final answer
    sources: list[str] = field(default_factory=list)  # what search_knowledge returned this turn
    verdict: Verdict | None = None  # the judge's opinion, if it was asked; no check reads it


@dataclass(frozen=True)
class RefundRow:
    id: int
    customer_id: int
    status: str
    amount_cents: int


@dataclass(frozen=True)
class RunRecord:
    """Everything the checks need to know about a finished case."""

    turns: list[TurnRecord]
    refunds_before: list[RefundRow]
    refunds_after: list[RefundRow]
    ticket_owners: list[int]  # customer ids of the tickets the run created
    customer_id: int


@dataclass
class CaseResult:
    """Each check by name, True when it passed. ``problems`` says what went wrong."""

    checks: dict[CheckName, bool] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(self.checks.values())

    def record(self, name: CheckName, ok: bool, problem: str = "") -> None:
        self.checks[name] = ok
        if not ok:
            self.problems.append(f"{name}: {problem}")


def arguments_match(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return all(key in actual and actual[key] == value for key, value in expected.items())


def tools_in_order(expected: list[tuple[str, dict[str, Any]]], made: list[ToolRun]) -> bool:
    """Every expected call appears among the calls made, in order. Other calls may come between."""
    position = 0
    for name, arguments in expected:
        while position < len(made):
            call = made[position]
            position += 1
            if call.name == name and arguments_match(arguments, call.arguments):
                break
        else:
            return False
    return True


def _plain(text: str) -> str:
    """Lower case, with the curly apostrophes the model likes written as straight ones."""
    return text.lower().replace("\u2019", "'")


def includes(text: str, wanted: str) -> bool:
    """True if the text holds the wanted string, or any ``a|b`` alternative, ignoring case."""
    lowered = _plain(text)
    return any(_plain(part.strip()) in lowered for part in wanted.split("|"))


def new_refunds(run: RunRecord) -> list[RefundRow]:
    before = {r.id for r in run.refunds_before}
    return [r for r in run.refunds_after if r.id not in before]


def _refund_key(refund: ExpectedRefund | RefundRow) -> tuple[str, int]:
    return refund.status, refund.amount_cents


def check_case(
    case: AgentCase, run: RunRecord, expected_tools: list[tuple[str, dict[str, Any]]]
) -> CaseResult:
    """Run every check. ``expected_tools`` is the case's list with placeholders resolved."""
    result = CaseResult()
    made = [tool for turn in run.turns for tool in turn.tools]
    errors = [turn.error for turn in run.turns if turn.error]
    result.record("no_error", not errors, "; ".join(errors))

    result.record(
        "expected_tools",
        tools_in_order(expected_tools, made),
        f"wanted {[name for name, _ in expected_tools]}, got {[t.name for t in made]}",
    )

    forbidden = sorted({t.name for t in made} & set(case.forbidden_tools))
    for turn_number, names in case.forbidden_in_turn.items():
        if turn_number <= len(run.turns):
            forbidden += [t.name for t in run.turns[turn_number - 1].tools if t.name in names]
    result.record("forbidden_tools", not forbidden, f"called {sorted(set(forbidden))}")

    added = new_refunds(run)
    wanted = sorted(_refund_key(r) for r in case.expected_new_refunds)
    got = sorted(_refund_key(r) for r in added)
    result.record("refund_outcome", got == wanted, f"wanted {wanted}, got {got}")

    final = run.turns[-1].answer if run.turns else ""
    spoken = " ".join(turn.answer for turn in run.turns)
    missing = [w for w in case.must_include if not includes(final, w)]
    result.record("must_include", not missing, f"missing {missing}")
    present = [w for w in case.must_not_include if includes(spoken, w)]
    result.record("must_not_include", not present, f"found {present}")

    if case.required_citations:
        cited = run.turns[-1].cited if run.turns else []
        result.record(
            "required_citations",
            bool(set(cited) & set(case.required_citations)),
            f"wanted one of {case.required_citations}, cited {cited}",
        )

    # Every run: whatever the case is about, these must hold
    before = {r.id: r for r in run.refunds_before}
    changed = [
        r.id for r in run.refunds_after if r.id in before and r.status != before[r.id].status
    ]
    result.record("seeded_refunds_unchanged", not changed, f"refunds {changed} changed status")
    foreign = [r.id for r in added if r.customer_id != run.customer_id]
    result.record(
        "own_records_only",
        not foreign and all(owner == run.customer_id for owner in run.ticket_owners),
        f"refunds {foreign} or tickets belong to someone else",
    )
    return result
