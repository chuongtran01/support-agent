"""Turn a turn's events into server-sent events."""

import asyncio
import json
from collections.abc import AsyncIterator

from app.agent.events import DoneEvent, ErrorEvent, TextEvent, ToolEvent, TurnEvent


def sse_frame(event: TurnEvent) -> str:
    """One server-sent event. The JSON has no raw newlines, so a frame is always three lines."""
    match event:
        case TextEvent(text):
            name, data = "text", {"text": text}
        case ToolEvent(tool, phase, status):
            name, data = "tool", {"name": tool, "phase": phase, "status": status}
        case DoneEvent():
            name = "done"
            data = {
                "message_id": event.message_id,
                "citations": [
                    {"document_id": c.document_id, "title": c.title, "heading_path": c.heading_path}
                    for c in event.citations
                ],
                "dropped_citations": event.dropped_citations,
            }
        case ErrorEvent(message):
            name, data = "error", {"message": message}
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


async def frames(queue: asyncio.Queue[TurnEvent | None]) -> AsyncIterator[str]:
    """The frames of a turn, until it ends."""
    while (event := await queue.get()) is not None:
        yield sse_frame(event)
