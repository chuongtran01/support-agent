"""What a turn tells its caller. The API turns these into server-sent events."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Citation:
    document_id: str
    title: str
    heading_path: str


@dataclass(frozen=True)
class TextEvent:
    """A piece of the answer, sent as soon as the model writes it."""

    text: str


@dataclass(frozen=True)
class ToolEvent:
    """A tool started or finished. The arguments are left out on purpose."""

    name: str
    phase: Literal["started", "finished"]
    status: str | None = None


@dataclass(frozen=True)
class DoneEvent:
    message_id: int
    citations: tuple[Citation, ...] = ()
    dropped_citations: int = 0
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(frozen=True)
class ErrorEvent:
    message: str


TurnEvent = TextEvent | ToolEvent | DoneEvent | ErrorEvent
