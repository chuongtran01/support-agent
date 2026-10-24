"""Traces of the agent's turns, sent to Langfuse.

The loop talks to ``Tracer`` and its small objects and never imports Langfuse. The base classes
do nothing, so a turn without tracing runs the same code; ``get_tracer`` returns the Langfuse
version only when both keys are set. Tracing must never change a turn: every call into the SDK
is inside ``_safe``, which logs a failure once and carries on.

A trace is one turn. It holds one generation per model call and one span per tool call, made
by hand (``start_observation`` and ``end``) rather than as context managers, because a turn is
an async generator and a context kept open across its ``yield``s would be fragile.
"""

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any

from app.agent.llm import ToolCall, Usage
from app.agent.messages import MessageContent
from app.config import settings

logger = logging.getLogger(__name__)

# How a turn ended
ANSWERED = "answered"
HANDED_OVER = "handed_over"  # out of steps; a ticket was opened
MODEL_ERROR = "model_error"
NO_ANSWER = "no_answer"
FAILED = "failed"  # an exception that was not the model's
INTERRUPTED = "interrupted"  # the turn was closed before it ended

# Tool statuses that show up marked in the trace. Most statuses are ordinary outcomes: ``ok``,
# ``allowed`` (a refund made), ``needs_approval``, and the empty ``no_orders`` and ``no_results``
WARNING_STATUSES = frozenset({"not_found", "invalid_request", "denied", "manual_review"})
ERROR_STATUS = "error"  # the tool itself failed


class ModelSpan:
    """One model call. The base class does nothing."""

    def first_output(self) -> None:
        """The first event of the reply arrived (time to first token)."""

    def finish(
        self, *, text: str, tool_calls: Sequence[ToolCall], usage: Usage, model: str | None
    ) -> None:
        """The reply is whole."""

    def fail(self, message: str) -> None:
        """The call raised."""


class ToolSpan:
    """One tool call. The base class does nothing."""

    def finish(self, *, status: str, output: str) -> None:
        """``output`` is the JSON text the model was given."""


class TurnTrace:
    """One turn. The base class does nothing."""

    def model_call(self, step: int, system: str, history: Sequence[MessageContent]) -> ModelSpan:
        return ModelSpan()

    def tool_call(self, name: str, arguments: str) -> ToolSpan:
        return ToolSpan()

    def finish(self, outcome: str, answer: str | None = None) -> None:
        """End the turn. Calling it again changes nothing."""


class Tracer:
    """Starts the trace for a turn. The base class does nothing."""

    def start_turn(self, *, customer_id: int, session_id: str, text: str) -> TurnTrace:
        return TurnTrace()

    def tagged(self, metadata: Mapping[str, str], tags: Sequence[str] = ()) -> "Tracer":
        """The same tracer with more metadata and tags on every trace it starts."""
        return self

    def flush(self) -> None:
        """Send what is waiting. Call before the process exits."""


_warned = False


def _warn(error: Exception) -> None:
    """Log a tracing failure, once. Not on every call: a Langfuse outage would flood the log."""
    global _warned
    if not _warned:
        _warned = True
        logger.warning("tracing failed; the turn goes on without it: %r", error)


def _safe(call: Callable[[], Any]) -> None:
    try:
        call()
    except Exception as error:  # noqa: BLE001  (tracing must never change a turn)
        _warn(error)


def _messages(system: str, history: Sequence[MessageContent]) -> list[dict[str, Any]]:
    # A stored message holds everything but its role, which Langfuse needs to show a chat
    return [
        {"role": "system", "content": system},
        *({"role": m.role, **m.as_json()} for m in history),
    ]


class _LangfuseModelSpan(ModelSpan):
    def __init__(self, generation: Any):
        self._generation = generation
        self._first_output = False

    def first_output(self) -> None:
        if self._first_output:
            return
        self._first_output = True
        _safe(lambda: self._generation.update(completion_start_time=datetime.now(UTC)))

    def finish(
        self, *, text: str, tool_calls: Sequence[ToolCall], usage: Usage, model: str | None
    ) -> None:
        output: dict[str, Any] = {"text": text}
        if tool_calls:
            output["tool_calls"] = [
                {"name": c.name, "arguments": _json_or_text(c.arguments)} for c in tool_calls
            ]
        _safe(
            lambda: self._generation.update(
                output=output,
                model=model,
                usage_details={"input": usage.prompt_tokens, "output": usage.completion_tokens},
            )
        )
        _safe(self._generation.end)

    def fail(self, message: str) -> None:
        _safe(lambda: self._generation.update(level="ERROR", status_message=message))
        _safe(self._generation.end)


class _LangfuseToolSpan(ToolSpan):
    def __init__(self, span: Any):
        self._span = span

    def finish(self, *, status: str, output: str) -> None:
        if status == ERROR_STATUS:
            level = "ERROR"
        else:
            level = "WARNING" if status in WARNING_STATUSES else "DEFAULT"
        _safe(
            lambda: self._span.update(
                output=_json_or_text(output), level=level, status_message=status
            )
        )
        _safe(self._span.end)


@dataclass(frozen=True)
class _Attributes:
    """What Langfuse copies onto every observation of a trace, so that cost and latency can be
    grouped by customer, chat or tag. It copies from the context a span is made in, and a child
    made later is not in the root's context, so each observation is made inside this."""

    customer_id: int
    session_id: str
    tags: tuple[str, ...]
    metadata: Mapping[str, str]

    def apply(self):
        from langfuse import propagate_attributes

        return propagate_attributes(
            user_id=str(self.customer_id),
            session_id=self.session_id,
            trace_name="support turn",
            tags=list(self.tags) or None,
            metadata=dict(self.metadata) or None,
        )


class _LangfuseTurnTrace(TurnTrace):
    def __init__(self, root: Any, attributes: _Attributes, model_parameters: dict[str, str]):
        self._root = root
        self._attributes = attributes
        self._model_parameters = model_parameters
        self._finished = False

    def model_call(self, step: int, system: str, history: Sequence[MessageContent]) -> ModelSpan:
        try:
            with self._attributes.apply():
                generation = self._root.start_observation(
                    name=f"model call {step}",
                    as_type="generation",
                    input=_messages(system, history),
                    model_parameters=self._model_parameters,
                )
        except Exception as error:  # noqa: BLE001
            _warn(error)
            return ModelSpan()
        return _LangfuseModelSpan(generation)

    def tool_call(self, name: str, arguments: str) -> ToolSpan:
        try:
            with self._attributes.apply():
                span = self._root.start_observation(
                    name=name, as_type="tool", input=_json_or_text(arguments)
                )
        except Exception as error:  # noqa: BLE001
            _warn(error)
            return ToolSpan()
        return _LangfuseToolSpan(span)

    def finish(self, outcome: str, answer: str | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        level = "ERROR" if outcome in (MODEL_ERROR, NO_ANSWER, FAILED) else "DEFAULT"
        _safe(
            lambda: self._root.update(
                output=answer, level=level, status_message=outcome, metadata={"outcome": outcome}
            )
        )
        _safe(self._root.end)


def _json_or_text(text: str) -> Any:
    """Langfuse shows a parsed value as a tree; text that is not JSON stays as it is."""
    try:
        return json.loads(text)
    except ValueError:
        return text


class LangfuseTracer(Tracer):
    def __init__(
        self, client: Any, metadata: Mapping[str, str] | None = None, tags: Sequence[str] = ()
    ):
        self._client = client
        self._metadata = dict(metadata or {})
        self._tags = list(tags)

    def tagged(self, metadata: Mapping[str, str], tags: Sequence[str] = ()) -> Tracer:
        return LangfuseTracer(self._client, {**self._metadata, **metadata}, [*self._tags, *tags])

    def start_turn(self, *, customer_id: int, session_id: str, text: str) -> TurnTrace:
        attributes = _Attributes(customer_id, session_id, tuple(self._tags), self._metadata)
        try:
            with attributes.apply():
                root = self._client.start_observation(
                    name="support turn", as_type="agent", input=text
                )
        except Exception as error:  # noqa: BLE001
            _warn(error)
            return TurnTrace()
        parameters = {"reasoning_effort": settings.agent_reasoning_effort}
        return _LangfuseTurnTrace(root, attributes, parameters)

    def flush(self) -> None:
        _safe(self._client.flush)


@cache
def get_tracer() -> Tracer:
    """The Langfuse tracer if both keys are set, otherwise one that does nothing."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return Tracer()
    from langfuse import Langfuse

    try:
        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url or None,
        )
    except Exception:
        logger.warning("could not start Langfuse; tracing is off", exc_info=True)
        return Tracer()
    return LangfuseTracer(client)
