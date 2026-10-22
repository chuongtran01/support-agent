"""The model behind one small interface.

The loop talks to ``LLMClient`` and sees three kinds of event, whatever the provider. The
OpenAI client calls the Responses API and turns its stream into those events; ``ScriptedLLM`` plays back a
fixed script, which is how the loop is tested with no API key.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import AsyncOpenAI, OpenAIError

from app.agent.messages import AssistantContent, MessageContent, ToolContent, UserContent
from app.config import settings
from app.tools.registry import ToolSchema


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    """A finished call: ``arguments`` is the JSON text exactly as the model wrote it."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class Done:
    """The last event of every reply."""

    usage: Usage = field(default_factory=Usage)


LLMEvent = TextDelta | ToolCall | Done


class LLMError(Exception):
    """The model could not be reached or refused the request. The only error the loop
    expects from a client; anything else is a bug and is allowed to surface."""


class LLMClient(Protocol):
    def stream(
        self, system: str, messages: Sequence[MessageContent], tools: Sequence[ToolSchema]
    ) -> AsyncIterator[LLMEvent]:
        """Stream one reply: text deltas as they arrive, then each finished tool call, then
        ``Done``. ``system`` is the system prompt and ``messages`` the conversation so far."""
        ...


def to_responses_tools(tools: list[ToolSchema]) -> list[dict]:
    """The tools in the shape the Responses API takes."""
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
        for tool in tools
    ]


def to_responses_input(messages: Sequence[MessageContent]) -> list[dict]:
    """The conversation as Responses API input items.

    An assistant message becomes its text (if any) and one ``function_call`` item per tool
    call, and a tool message becomes the matching ``function_call_output``. The reasoning the
    model did on an earlier step is not sent back: the API accepts a conversation without it,
    and it is not stored.
    """
    items: list[dict] = []
    for message in messages:
        match message:
            case UserContent():
                items.append({"role": "user", "content": message.content})
            case AssistantContent():
                if message.content:
                    items.append({"role": "assistant", "content": message.content})
                for call in message.tool_calls:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": call.id,
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        }
                    )
            case ToolContent():
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content,
                    }
                )
    return items


async def events_from_responses(stream: AsyncIterator[Any]) -> AsyncIterator[LLMEvent]:
    """Turn Responses API stream events into ``LLMEvent``s.

    Text is sent as it arrives. A function call is whole by the time the API reports its item
    done, but is held back until the stream ends, so the loop sees the text first and then
    every call in the order the model made them. A failed response raises ``LLMError``.
    """
    calls: list[ToolCall] = []
    usage = Usage()
    async for event in stream:
        match event.type:
            case "response.output_text.delta":
                if event.delta:
                    yield TextDelta(event.delta)
            case "response.output_item.done" if event.item.type == "function_call":
                item = event.item
                calls.append(ToolCall(item.call_id, item.name, item.arguments))
            case "response.completed" | "response.incomplete":
                if event.response.usage:
                    usage = Usage(
                        event.response.usage.input_tokens, event.response.usage.output_tokens
                    )
            case "response.failed":
                error = event.response.error
                raise LLMError(error.message if error else "the response failed")
            case "error":
                raise LLMError(event.message)
    for call in calls:
        yield call
    yield Done(usage)


class OpenAIClient:
    def __init__(self, api_key: str = "", model: str = ""):
        api_key = api_key or settings.openai_api_key
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set; add it to .env")
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model or settings.agent_model

    async def stream(
        self, system: str, messages: Sequence[MessageContent], tools: Sequence[ToolSchema]
    ) -> AsyncIterator[LLMEvent]:
        try:
            stream = await self._client.responses.create(
                model=self._model,
                instructions=system,
                input=to_responses_input(messages),
                tools=to_responses_tools(tools),
                reasoning={"effort": settings.agent_reasoning_effort},
                store=False,  # the conversation lives in our database, not OpenAI's
                stream=True,
            )
            async for event in events_from_responses(stream):
                yield event
        except OpenAIError as error:
            raise LLMError(str(error)) from error


class ScriptedLLM:
    """Plays back one scripted reply per call and records what it was asked.

    Each reply is a list of events; ``Done`` is added if the script leaves it out. Running out
    of replies raises, so a loop that calls the model more often than the test expects fails.
    """

    def __init__(self, replies: Sequence[Sequence[LLMEvent]]):
        self._replies = [list(reply) for reply in replies]
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self, system: str, messages: Sequence[MessageContent], tools: Sequence[ToolSchema]
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append({"system": system, "messages": list(messages), "tools": list(tools)})
        if len(self.calls) > len(self._replies):
            raise AssertionError("the script has no reply for this model call")
        reply = self._replies[len(self.calls) - 1]
        for event in reply:
            yield event
        if not reply or not isinstance(reply[-1], Done):
            yield Done()


def say(text: str) -> list[LLMEvent]:
    """A scripted reply that is only text, sent in a few pieces like a real stream."""
    middle = len(text) // 2
    return [TextDelta(text[:middle]), TextDelta(text[middle:]), Done()]


def call_tools(*calls: ToolCall) -> list[LLMEvent]:
    """A scripted reply that asks for tools."""
    return [*calls, Done()]
