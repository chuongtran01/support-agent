"""The model behind one small interface.

The loop talks to ``LLMClient`` and sees three kinds of event, whatever the provider. The
OpenAI client turns Chat Completions chunks into those events; ``ScriptedLLM`` plays back a
fixed script, which is how the loop is tested with no API key.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from openai import AsyncOpenAI, OpenAIError

from app.config import settings


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
    def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]:
        """Stream one reply: text deltas as they arrive, then each finished tool call, then
        ``Done``. ``messages`` includes the system message."""
        ...


class _PendingCall:
    """A tool call being assembled from pieces."""

    def __init__(self) -> None:
        self.id = ""
        self.name = ""
        self.arguments = ""


async def events_from_chunks(chunks: AsyncIterator[Any]) -> AsyncIterator[LLMEvent]:
    """Turn Chat Completions stream chunks into events.

    A tool call arrives in pieces keyed by ``index``: the id and name first, then the
    arguments a few characters at a time. They are held back until the stream ends, so the
    loop only ever sees whole calls, in the order the model made them.
    """
    pending: dict[int, _PendingCall] = {}
    usage = Usage()
    async for chunk in chunks:
        if getattr(chunk, "usage", None):
            usage = Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
        for choice in chunk.choices:
            delta = choice.delta
            if delta.content:
                yield TextDelta(delta.content)
            for piece in delta.tool_calls or []:
                call = pending.setdefault(piece.index, _PendingCall())
                call.id = piece.id or call.id
                if piece.function:
                    call.name = piece.function.name or call.name
                    call.arguments += piece.function.arguments or ""
    for index in sorted(pending):
        call = pending[index]
        yield ToolCall(call.id, call.name, call.arguments)
    yield Done(usage)


class OpenAIClient:
    def __init__(self, api_key: str = "", model: str = ""):
        api_key = api_key or settings.openai_api_key
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set; add it to .env")
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model or settings.agent_model

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]:
        try:
            chunks = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=tools,
                reasoning_effort=settings.agent_reasoning_effort,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for event in events_from_chunks(chunks):
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

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]:
        self.calls.append({"messages": list(messages), "tools": tools})
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
