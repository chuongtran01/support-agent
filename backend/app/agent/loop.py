"""One turn of the agent: a customer message in, a stream of events out.

The loop calls the model, runs the tools it asks for, and repeats until the model answers.
It imports no web framework and no model provider, so it is tested with a scripted model.
No database transaction is open while the model is being called: every message is saved in
its own short transaction, and ``dispatch`` gives each tool call one more.
"""

import logging
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.agent import store
from app.agent.citations import check_citations, retrieved_from_result
from app.agent.events import Citation, DoneEvent, ErrorEvent, TextEvent, ToolEvent, TurnEvent
from app.agent.llm import Done, LLMClient, LLMError, TextDelta, ToolCall, Usage
from app.agent.messages import (
    AssistantContent,
    FunctionCall,
    StoredToolCall,
    ToolContent,
    UserContent,
)
from app.agent.prompt import build_system_prompt
from app.auth import CurrentCustomer
from app.config import settings
from app.retrieval.embeddings import Embedder
from app.tools.context import ToolContext, utc_now
from app.tools.registry import ToolOutput, run_tool, tool_schemas
from app.tools.tickets import open_ticket

logger = logging.getLogger(__name__)

TOOL_SCHEMAS = tool_schemas()

STEP_LIMIT_MESSAGE = (
    "I wasn't able to finish working on this, so I've passed it to our support team. "
    "A person will follow up with you."
)
MODEL_UNAVAILABLE_MESSAGE = "The assistant is unavailable right now. Please try again in a moment."
NO_ANSWER_MESSAGE = "The assistant did not produce an answer. Please try again."


@dataclass(frozen=True)
class AgentDeps:
    """What a turn needs from outside: the database, the model, and the embedder for search."""

    sessions: store.Sessions
    llm: LLMClient
    embedder: Embedder | None = None
    clock: Callable[[], datetime] = utc_now
    max_steps: int = field(default_factory=lambda: settings.agent_max_steps)


@dataclass(frozen=True)
class _Reply:
    """One whole model reply, once its stream has ended."""

    text: str
    tool_calls: list[ToolCall]
    usage: Usage


async def _model_step(
    deps: AgentDeps, messages: list[dict[str, Any]]
) -> AsyncIterator[TextEvent | _Reply]:
    """Call the model once. Text goes out as it arrives; the whole reply comes last."""
    text: list[str] = []
    calls: list[ToolCall] = []
    usage = Usage()
    async for event in deps.llm.stream(messages, TOOL_SCHEMAS):
        if isinstance(event, TextDelta):
            text.append(event.text)
            yield TextEvent(event.text)
        elif isinstance(event, ToolCall):
            calls.append(event)
        elif isinstance(event, Done):
            usage = event.usage
    yield _Reply("".join(text), calls, usage)


async def _run_tool(deps: AgentDeps, customer: CurrentCustomer, call: ToolCall) -> ToolOutput:
    """Run one tool call as the logged-in customer. The model never supplies who that is."""
    async with deps.sessions() as session:
        ctx = ToolContext(session, customer.id, deps.clock, deps.embedder)
        return await run_tool(ctx, call.name, call.arguments)


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
) -> None:
    """The agent ran out of steps: open an escalation ticket from code, not from the model."""
    done = "\n".join(f"- {line}" for line in calls) or "- none"
    async with deps.sessions.begin() as session:
        ctx = ToolContext(session, customer.id, deps.clock, deps.embedder)
        await open_ticket(
            ctx,
            "escalation",
            "The support agent ran out of steps",
            f"The agent made {deps.max_steps} model calls in one turn without answering "
            f"(chat {session_id}). The customer's message was:\n\n{customer_text}\n\n"
            f"Tool calls made in this turn:\n{done}",
        )


async def run_turn(
    deps: AgentDeps, customer: CurrentCustomer, session_id: uuid.UUID, text: str
) -> AsyncIterator[TurnEvent]:
    """Handle one customer message. Raises ``SessionNotFound`` before doing anything else if
    the session is not this customer's."""
    stored = await store.load_history(deps.sessions, session_id, customer.id)
    system = {
        "role": "system",
        "content": build_system_prompt(customer.name, customer.membership, deps.clock().date()),
    }
    question = UserContent(content=text)
    await store.save_message(deps.sessions, session_id, question)
    history = [*store.to_openai(stored), question.as_openai()]

    retrieved: dict[str, Citation] = {}
    calls_made: list[str] = []  # "name: status", for the ticket if the agent runs out of steps
    receipts: list[str] = []  # what the tools did, in their own words, for the same case
    model_calls = prompt_tokens = completion_tokens = 0

    for _ in range(deps.max_steps):
        reply: _Reply | None = None
        try:
            async for item in _model_step(deps, [system, *history]):
                if isinstance(item, _Reply):
                    reply = item
                else:
                    yield item
        except LLMError:
            logger.exception("model call failed")
            yield ErrorEvent(MODEL_UNAVAILABLE_MESSAGE)
            return
        assert reply is not None
        model_calls += 1
        prompt_tokens += reply.usage.prompt_tokens
        completion_tokens += reply.usage.completion_tokens

        if not reply.tool_calls:
            if not reply.text.strip():
                yield ErrorEvent(NO_ANSWER_MESSAGE)
                return
            answer = AssistantContent(content=reply.text)
            message_id = await store.save_message(deps.sessions, session_id, answer)
            citations, dropped = check_citations(reply.text, retrieved)
            yield DoneEvent(
                message_id, citations, dropped, model_calls, prompt_tokens, completion_tokens
            )
            return

        # The assistant message and every result are saved before the next model call, so a
        # stored conversation can always be replayed to the model
        assistant = _assistant_content(reply)
        await store.save_message(deps.sessions, session_id, assistant)
        history.append(assistant.as_openai())
        for call in reply.tool_calls:
            yield ToolEvent(call.name, "started")
            output = await _run_tool(deps, customer, call)
            calls_made.append(f"{call.name}: {output.status}")
            if output.receipt:
                receipts.append(output.receipt)
            if call.name == "search_knowledge":
                for document_id, citation in retrieved_from_result(output.text).items():
                    retrieved.setdefault(document_id, citation)
            outcome = ToolContent(tool_call_id=call.id, content=output.text)
            await store.save_message(deps.sessions, session_id, outcome)
            history.append(outcome.as_openai())
            yield ToolEvent(call.name, "finished", output.status)

    # The model is out of steps, but its tools may have changed things in the last round.
    # The message is written here from the tools' own receipts, never by the model.
    await _hand_over(deps, customer, session_id, text, calls_made)
    message = " ".join([STEP_LIMIT_MESSAGE, *receipts])
    message_id = await store.save_message(
        deps.sessions, session_id, AssistantContent(content=message)
    )
    yield TextEvent(message)
    yield DoneEvent(message_id, (), 0, model_calls, prompt_tokens, completion_tokens)
