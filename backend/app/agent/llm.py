"""The model behind one small interface.

The loop talks to ``LLMClient`` and sees three kinds of event, whatever the provider. This file
imports no provider: ``openai_client.py`` calls the Responses API and turns its stream into
these events, and the tests use a scripted fake (``tests/scripted_llm.py``), so the loop runs
with no API key.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.agent.messages import MessageContent
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
    """The last event of every reply. ``model`` is the one the provider says it used, if it
    says."""

    usage: Usage = field(default_factory=Usage)
    model: str | None = None


LLMEvent = TextDelta | ToolCall | Done


class LLMError(Exception):
    """The model could not be reached or refused the request. The only error the loop
    expects from a client; anything else is a bug and is allowed to surface."""


class LLMClient(Protocol):
    def stream(
        self, system: str, messages: Sequence[MessageContent], tools: Sequence[ToolSchema]
    ) -> AsyncIterator[LLMEvent]:
        """Stream one reply: text deltas as they arrive and each finished tool call, in the order
        the model produced them, then ``Done``. ``system`` is the system prompt and ``messages``
        the conversation so far."""
        ...
