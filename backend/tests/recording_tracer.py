"""A tracer for tests: keeps what the loop reports, so a test can read it back."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.agent.llm import ToolCall, Usage
from app.agent.messages import MessageContent
from app.tracing import ModelSpan, ToolSpan, Tracer, TurnTrace


@dataclass
class RecordedModelCall:
    step: int
    system: str
    history: list[MessageContent]
    first_output: bool = False
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
    model: str | None = None
    error: str | None = None


@dataclass
class RecordedToolCall:
    name: str
    arguments: str
    status: str | None = None
    output: str | None = None


@dataclass
class RecordedTurn:
    customer_id: int
    session_id: str
    text: str
    metadata: dict[str, str]
    tags: list[str]
    steps: list[RecordedModelCall | RecordedToolCall] = field(default_factory=list)
    outcome: str | None = None
    answer: str | None = None
    finishes: int = 0  # how many times finish() was called, to see that it is harmless

    @property
    def model_calls(self) -> list[RecordedModelCall]:
        return [s for s in self.steps if isinstance(s, RecordedModelCall)]

    @property
    def tool_calls(self) -> list[RecordedToolCall]:
        return [s for s in self.steps if isinstance(s, RecordedToolCall)]


class _Model(ModelSpan):
    def __init__(self, record: RecordedModelCall):
        self._record = record

    def first_output(self) -> None:
        self._record.first_output = True

    def finish(self, *, text, tool_calls, usage, model) -> None:
        self._record.text, self._record.usage, self._record.model = text, usage, model
        self._record.tool_calls = list(tool_calls)

    def fail(self, message: str) -> None:
        self._record.error = message


class _Tool(ToolSpan):
    def __init__(self, record: RecordedToolCall):
        self._record = record

    def finish(self, *, status: str, output: str) -> None:
        self._record.status, self._record.output = status, output


class _Turn(TurnTrace):
    def __init__(self, record: RecordedTurn):
        self._record = record

    def model_call(self, step: int, system: str, history: Sequence[MessageContent]) -> ModelSpan:
        record = RecordedModelCall(step, system, list(history))
        self._record.steps.append(record)
        return _Model(record)

    def tool_call(self, name: str, arguments: str) -> ToolSpan:
        record = RecordedToolCall(name, arguments)
        self._record.steps.append(record)
        return _Tool(record)

    def finish(self, outcome: str, answer: str | None = None) -> None:
        self._record.finishes += 1
        if self._record.outcome is None:  # the first call counts, as in the real tracer
            self._record.outcome, self._record.answer = outcome, answer


class RecordingTracer(Tracer):
    def __init__(
        self,
        turns: list[RecordedTurn] | None = None,
        metadata: Mapping[str, str] | None = None,
        tags: Sequence[str] = (),
    ):
        self.turns = turns if turns is not None else []
        self._metadata = dict(metadata or {})
        self._tags = list(tags)
        self.flushed = 0

    def start_turn(self, *, customer_id: int, session_id: str, text: str) -> TurnTrace:
        record = RecordedTurn(customer_id, session_id, text, self._metadata, self._tags)
        self.turns.append(record)
        return _Turn(record)

    def tagged(self, metadata: Mapping[str, str], tags: Sequence[str] = ()) -> Tracer:
        return RecordingTracer(self.turns, {**self._metadata, **metadata}, [*self._tags, *tags])

    def flush(self) -> None:
        self.flushed += 1
