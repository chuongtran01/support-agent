"""A fake model for tests: plays back a script, so the loop runs with no API key."""

from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.agent.llm import Done, LLMEvent, TextDelta, ToolCall
from app.agent.messages import MessageContent
from app.tools.registry import ToolSchema


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
