"""One turn of the agent: a customer message in, a stream of events out.

The loop calls the model, runs the tools it asks for, and repeats until the model answers.
It imports no web framework and no model provider, so it is tested with a scripted model.
No database transaction is open while the model is being called: every message is saved in
its own short transaction, and ``dispatch`` gives each tool call one more.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.agent import store
from app.agent.backends import LocalBackend, ToolBackend
from app.agent.citations import add_retrieved, check_citations
from app.agent.events import Citation, DoneEvent, ErrorEvent, TextEvent, ToolEvent, TurnEvent
from app.agent.llm import Done, LLMClient, LLMError, TextDelta, ToolCall, Usage
from app.agent.messages import (
    AssistantContent,
    FunctionCall,
    MessageContent,
    StoredToolCall,
    ToolContent,
    UserContent,
)
from app.agent.prompt import build_system_prompt
from app.auth import CurrentCustomer
from app.config import settings
from app.retrieval.embeddings import Embedder
from app.tools.context import ToolContext, utc_now
from app.tools.registry import ToolOutput, tool_schemas
from app.tools.tickets import open_ticket
from app.tracing import (
    ANSWERED,
    FAILED,
    HANDED_OVER,
    INTERRUPTED,
    MODEL_ERROR,
    NO_ANSWER,
    ModelSpan,
    Tracer,
    TurnTrace,
)

logger = logging.getLogger(__name__)

TOOL_SCHEMAS = tool_schemas()

STEP_LIMIT_MESSAGE = (
    "I wasn't able to finish working on this, so I've passed it to our support team. "
    "A person will follow up with you."
)
MODEL_UNAVAILABLE_MESSAGE = "The assistant is unavailable right now. Please try again in a moment."
# The model failed after a tool had already changed something, so "try again" alone would hide it
MODEL_FAILED_AFTER_ACTION_MESSAGE = (
    "The assistant stopped before it could finish, but part of your request was already done."
)
TRY_AGAIN_MESSAGE = "Please try again in a moment for anything else."
NO_ANSWER_MESSAGE = "The assistant did not produce an answer. Please try again."


@dataclass(frozen=True)
class AgentDeps:
    """What a turn needs from outside: the database, the model, and the embedder for search."""

    sessions: store.Sessions
    llm: LLMClient
    embedder: Embedder | None = None
    clock: Callable[[], datetime] = utc_now
    max_steps: int = field(default_factory=lambda: settings.agent_max_steps)
    deadline_seconds: float = field(default_factory=lambda: settings.agent_turn_deadline_seconds)
    model_retries: int = field(default_factory=lambda: settings.model_retries)
    retry_delay_seconds: float = field(default_factory=lambda: settings.model_retry_delay_seconds)
    monotonic: Callable[[], float] = time.monotonic
    # Does nothing unless the caller passes one: tests and scripts do not trace by accident
    tracer: Tracer = field(default_factory=Tracer)
    # Where tool calls run. None runs them in this process
    tools: ToolBackend | None = None


@dataclass(frozen=True)
class _Reply:
    """One whole model reply, once its stream has ended."""

    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    model: str | None = None


async def _model_step(
    deps: AgentDeps, system: str, history: list[MessageContent], span: ModelSpan
) -> AsyncIterator[TextEvent | _Reply]:
    """Call the model once. Text goes out as it arrives; the whole reply comes last."""
    text: list[str] = []
    calls: list[ToolCall] = []
    usage = Usage()
    model: str | None = None
    async for event in deps.llm.stream(system, history, TOOL_SCHEMAS):
        span.first_output()
        if isinstance(event, TextDelta):
            text.append(event.text)
            yield TextEvent(event.text)
        elif isinstance(event, ToolCall):
            calls.append(event)
        elif isinstance(event, Done):
            usage = event.usage
            model = event.model
    yield _Reply("".join(text), calls, usage, model)


async def _model_step_with_retries(
    deps: AgentDeps, system: str, history: list[MessageContent], trace: TurnTrace, step: int
) -> AsyncIterator[TextEvent | _Reply]:
    """``_model_step`` that tries again after a failure. Raises ``LLMError`` if it failed for good.

    A call is tried again only if it had shown the customer no text yet: it changed nothing, and
    the second try is then invisible. Once text has gone out a second try would repeat it, so the
    failure stands. Each try is its own span in the trace.
    """
    for attempt in range(deps.model_retries + 1):
        span = trace.model_call(step, system, history)
        shown = False
        try:
            async for item in _model_step(deps, system, history, span):
                if isinstance(item, _Reply):
                    span.finish(
                        text=item.text,
                        tool_calls=item.tool_calls,
                        usage=item.usage,
                        model=item.model,
                    )
                    yield item
                    return
                shown = True
                yield item
        except LLMError as error:
            span.fail(str(error))
            if shown or attempt == deps.model_retries:
                raise
            logger.warning("model call failed, trying again: %s", error)
            await asyncio.sleep(deps.retry_delay_seconds)


async def _run_tool(deps: AgentDeps, customer: CurrentCustomer, call: ToolCall) -> ToolOutput:
    """Run one tool call as the logged-in customer, on the deps' backend or in this process."""
    tools = deps.tools or LocalBackend(deps.sessions, deps.embedder, deps.clock)
    return await tools.run(customer, call)


def _assistant_content(reply: _Reply) -> AssistantContent:
    return AssistantContent(
        content=reply.text or None,
        tool_calls=tuple(
            StoredToolCall(id=c.id, function=FunctionCall(name=c.name, arguments=c.arguments))
            for c in reply.tool_calls
        ),
    )


async def _hand_over(
    deps: AgentDeps,
    customer: CurrentCustomer,
    session_id: uuid.UUID,
    customer_text: str,
    calls: list[str],
    model_calls: int,
) -> None:
    """The agent ran out of steps or time: open an escalation ticket from code, not from the model."""
    done = "\n".join(f"- {line}" for line in calls) or "- none"
    async with deps.sessions.begin() as session:
        ctx = ToolContext(session, customer.id, deps.clock, deps.embedder)
        await open_ticket(
            ctx,
            "escalation",
            "The support agent ran out of steps or time",
            f"The agent made {model_calls} model calls in one turn without answering "
            f"(chat {session_id}). The customer's message was:\n\n{customer_text}\n\n"
            f"Tool calls made in this turn:\n{done}",
        )


async def run_turn(
    deps: AgentDeps, customer: CurrentCustomer, session_id: uuid.UUID, text: str
) -> AsyncIterator[TurnEvent]:
    """Handle one customer message. Raises ``SessionNotFound`` before doing anything else if
    the session is not this customer's."""
    stored = await store.load_history(deps.sessions, session_id, customer.id)
    trace = deps.tracer.start_turn(customer_id=customer.id, session_id=str(session_id), text=text)
    try:
        async for event in _run_turn(deps, customer, session_id, text, stored, trace):
            yield event
    except Exception:
        trace.finish(FAILED)
        raise
    finally:
        trace.finish(INTERRUPTED)  # changes nothing if the turn already ended


async def _run_turn(
    deps: AgentDeps,
    customer: CurrentCustomer,
    session_id: uuid.UUID,
    text: str,
    stored: list[store.StoredMessage],
    trace: TurnTrace,
) -> AsyncIterator[TurnEvent]:
    system = build_system_prompt(customer.name, customer.membership, deps.clock().date())
    question = UserContent(content=text)
    await store.save_message(deps.sessions, session_id, question)
    history = [*store.to_messages(stored), question]

    retrieved: dict[str, Citation] = {}
    calls_made: list[str] = []  # "name: status", for the ticket if the agent runs out of steps
    receipts: list[str] = []  # what the tools did, in their own words, for the same case
    model_calls = prompt_tokens = completion_tokens = 0
    started = deps.monotonic()

    for step in range(1, deps.max_steps + 1):
        if deps.monotonic() - started > deps.deadline_seconds:
            break  # out of time, which is handled like out of steps
        reply: _Reply | None = None
        try:
            async for item in _model_step_with_retries(deps, system, history, trace, step):
                if isinstance(item, _Reply):
                    reply = item
                else:
                    yield item
        except LLMError:
            logger.exception("model call failed")
            trace.finish(MODEL_ERROR)
            message = MODEL_UNAVAILABLE_MESSAGE
            if receipts:
                # The tools' own words, as in the step limit: the model gets no turn to explain
                message = " ".join(
                    [MODEL_FAILED_AFTER_ACTION_MESSAGE, *receipts, TRY_AGAIN_MESSAGE]
                )
                await store.save_message(
                    deps.sessions, session_id, AssistantContent(content=message)
                )
            yield ErrorEvent(message)
            return
        assert reply is not None
        model_calls += 1
        prompt_tokens += reply.usage.prompt_tokens
        completion_tokens += reply.usage.completion_tokens

        if not reply.tool_calls:
            if not reply.text.strip():
                trace.finish(NO_ANSWER)
                yield ErrorEvent(NO_ANSWER_MESSAGE)
                return
            answer = AssistantContent(content=reply.text)
            message_id = await store.save_message(deps.sessions, session_id, answer)
            citations, dropped = check_citations(reply.text, retrieved)
            trace.finish(ANSWERED, reply.text)
            yield DoneEvent(
                message_id, citations, dropped, model_calls, prompt_tokens, completion_tokens
            )
            return

        # The assistant message and every result are saved before the next model call, so a
        # stored conversation can always be replayed to the model
        assistant = _assistant_content(reply)
        await store.save_message(deps.sessions, session_id, assistant)
        history.append(assistant)
        for call in reply.tool_calls:
            yield ToolEvent(call.name, "started")
            tool_span = trace.tool_call(call.name, call.arguments)
            output = await _run_tool(deps, customer, call)
            tool_span.finish(status=output.status, output=output.text)
            calls_made.append(f"{call.name}: {output.status}")
            if output.receipt:
                receipts.append(output.receipt)
            if call.name == "search_knowledge":
                add_retrieved(retrieved, output.text)
            outcome = ToolContent(tool_call_id=call.id, content=output.text)
            await store.save_message(deps.sessions, session_id, outcome)
            history.append(outcome)
            yield ToolEvent(call.name, "finished", output.status)

    # The model is out of steps or time, but its tools may have changed things in the last round.
    # The message is written here from the tools' own receipts, never by the model.
    await _hand_over(deps, customer, session_id, text, calls_made, model_calls)
    message = " ".join([STEP_LIMIT_MESSAGE, *receipts])
    message_id = await store.save_message(
        deps.sessions, session_id, AssistantContent(content=message)
    )
    trace.finish(HANDED_OVER, message)
    yield TextEvent(message)
    yield DoneEvent(message_id, (), 0, model_calls, prompt_tokens, completion_tokens)
