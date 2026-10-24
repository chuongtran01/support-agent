"""Agent eval cases: the schema, the loader and the placeholders.

Each row of ``datasets/agent.dev.jsonl`` and ``agent.test.jsonl`` is one scenario for the agent:
who is logged in, what they say, and what must (and must not) happen. The runner plays the
scenario against a freshly seeded database and checks the result with code, not a judge.

Messages and tool arguments may hold placeholders for ids that only exist after seeding:
``{order:alex-5d}`` is the id of the seed order with that key and ``{item:BOOT-TR}`` is the
id of the order line with that SKU. An argument that is only a placeholder becomes an int.
"""

import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"
KB_DIRECTORY = ROOT.parent / "data" / "kb"
TRAPS = ROOT.parent / "data" / "traps.yaml"

SPLITS = ("dev", "test")

Customer = Literal["alex", "priya", "sam", "jordan", "morgan"]
Category = Literal[
    "knowledge",
    "trap",
    "injection",
    "off_topic",
    "scoping",
    "refund",
    "ticket",
    "multi_turn",
    "orders",
]
ToolName = Literal[
    "search_knowledge", "lookup_order", "list_orders", "issue_refund", "create_ticket", "escalate"
]
RefundStatus = Literal["completed", "pending"]

_PLACEHOLDER = re.compile(r"\{(order|item):([A-Za-z0-9_-]+)\}")


class InvalidDataset(ValueError):
    """The dataset files are malformed or inconsistent."""


class ExpectedTool(BaseModel):
    """A call that must happen. The listed arguments must match; extra arguments are fine."""

    model_config = ConfigDict(extra="forbid")

    name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)


class ExpectedRefund(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RefundStatus
    amount_cents: int


class AgentCase(BaseModel):
    """One scenario.

    ``expected_tools`` must all appear, in this order, among the calls the agent makes (other
    calls may come between). ``expected_new_refunds`` is the exact list of refunds the run
    adds, so an empty list means none. ``must_include`` and ``must_not_include`` are checked
    against the final answer, ignoring case; an entry like ``"a|b"`` is satisfied by either.
    ``required_citations`` is satisfied when the answer cites at least one of the ids.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    description: str
    customer: Customer
    messages: list[str] = Field(min_length=1)
    expected_tools: list[ExpectedTool]
    forbidden_tools: list[ToolName]
    forbidden_in_turn: dict[int, list[ToolName]]
    expected_new_refunds: list[ExpectedRefund]
    must_include: list[str]
    must_not_include: list[str]
    required_citations: list[str]
    trap_id: str | None
    note: str | None

    @field_validator("messages", "must_include", "must_not_include")
    @classmethod
    def _no_blank_strings(cls, values: list[str]) -> list[str]:
        for value in values:
            if not all(part.strip() for part in value.split("|")):
                raise ValueError(f"blank text or blank alternative in {value!r}")
        return values

    @model_validator(mode="after")
    def _turns_exist(self) -> "AgentCase":
        for turn in self.forbidden_in_turn:
            if not 1 <= turn <= len(self.messages):
                raise ValueError(f"forbidden_in_turn names turn {turn}, which does not exist")
        expected = {tool.name for tool in self.expected_tools}
        if expected & set(self.forbidden_tools):
            raise ValueError("a tool is both expected and forbidden")
        return self


def known_document_ids(directory: Path = KB_DIRECTORY) -> set[str]:
    return {path.stem for path in directory.glob("*.md")}


def known_trap_ids(path: Path = TRAPS) -> set[str]:
    return {trap["id"] for trap in yaml.safe_load(path.read_text())["traps"]}


def _read(path: Path) -> list[AgentCase]:
    cases = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(AgentCase.model_validate_json(line))
        except ValueError as error:
            raise InvalidDataset(f"{path.name} line {number}: {error}") from error
    return cases


def load_cases(split: str, directory: Path = DATASETS) -> list[AgentCase]:
    """Load one split, checking ids, document ids and trap ids."""
    if split not in SPLITS:
        raise InvalidDataset(f"unknown split {split!r}")
    cases = _read(directory / f"agent.{split}.jsonl")
    documents, traps = known_document_ids(), known_trap_ids()
    for case in cases:
        unknown = [d for d in case.required_citations if d not in documents]
        if unknown:
            raise InvalidDataset(f"{case.id}: unknown document ids {unknown}")
        if case.trap_id is not None and case.trap_id not in traps:
            raise InvalidDataset(f"{case.id}: unknown trap id {case.trap_id!r}")
    return cases


def load_all(directory: Path = DATASETS) -> dict[str, list[AgentCase]]:
    """Load both splits and check that ids and messages do not repeat across them."""
    splits = {split: load_cases(split, directory) for split in SPLITS}
    ids = [case.id for cases in splits.values() for case in cases]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise InvalidDataset(f"repeated ids: {repeated}")
    firsts = {(c.customer, c.messages[0]) for c in splits["dev"]}
    leaked = [c.id for c in splits["test"] if (c.customer, c.messages[0]) in firsts]
    if leaked:
        raise InvalidDataset(f"test cases that repeat a dev scenario: {leaked}")
    return splits


def placeholders(value: Any) -> list[tuple[str, str]]:
    """Every ``(kind, key)`` placeholder found in a string, list or dict."""
    if isinstance(value, str):
        return _PLACEHOLDER.findall(value)
    if isinstance(value, list):
        return [found for item in value for found in placeholders(item)]
    if isinstance(value, dict):
        return [found for item in value.values() for found in placeholders(item)]
    return []


def case_placeholders(case: AgentCase) -> list[tuple[str, str]]:
    return placeholders(case.messages) + placeholders([t.arguments for t in case.expected_tools])


def resolve(value: Any, orders: dict[str, int], items: dict[str, int]) -> Any:
    """Replace placeholders with seeded ids. A value that is only a placeholder becomes an int."""
    ids = {"order": orders, "item": items}
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole:
            return ids[whole.group(1)][whole.group(2)]
        return _PLACEHOLDER.sub(lambda m: str(ids[m.group(1)][m.group(2)]), value)
    if isinstance(value, list):
        return [resolve(item, orders, items) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, orders, items) for key, item in value.items()}
    return value


def dumps(case: AgentCase) -> str:
    return json.dumps(case.model_dump(mode="json"), ensure_ascii=False)
