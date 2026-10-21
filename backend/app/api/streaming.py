"""Run a turn apart from the HTTP request and turn its events into server-sent events.

The turn is a task that writes events to a queue; the response reads the queue. If the browser
goes away, the task still finishes and saves its messages, so a refund that was made is never
missing from the conversation.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from app.agent.events import DoneEvent, ErrorEvent, TextEvent, ToolEvent, TurnEvent
from app.agent.loop import AgentDeps, run_turn
from app.auth import CurrentCustomer

logger = logging.getLogger(__name__)

TURN_FAILED_MESSAGE = "Something went wrong on our side. Please try again."


class TurnInProgress(Exception):
    """A turn is already running in this session."""


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


class TurnRunner:
    """Starts turns and allows one at a time per session.

    The set of running sessions lives in this process, so it assumes one server worker.
    """

    def __init__(self) -> None:
        self._running: set[uuid.UUID] = set()
        self._tasks: set[asyncio.Task] = set()

    def is_running(self, session_id: uuid.UUID) -> bool:
        return session_id in self._running

    def start(
        self, deps: AgentDeps, customer: CurrentCustomer, session_id: uuid.UUID, text: str
    ) -> asyncio.Queue[TurnEvent | None]:
        """Start a turn and return the queue its events arrive on; ``None`` ends it."""
        if session_id in self._running:
            raise TurnInProgress(str(session_id))
        self._running.add(session_id)
        queue: asyncio.Queue[TurnEvent | None] = asyncio.Queue()
        task = asyncio.create_task(self._run(deps, customer, session_id, text, queue))
        self._tasks.add(task)  # the event loop only keeps a weak reference to a task
        task.add_done_callback(self._tasks.discard)
        return queue

    async def _run(self, deps, customer, session_id, text, queue) -> None:
        try:
            async for event in run_turn(deps, customer, session_id, text):
                queue.put_nowait(event)
        except Exception:
            logger.exception("turn failed")
            queue.put_nowait(ErrorEvent(TURN_FAILED_MESSAGE))
        finally:
            # Free the session before the stream ends, so a client that sees the end of
            # one turn can send the next straight away
            self._running.discard(session_id)
            queue.put_nowait(None)

    async def drain(self) -> None:
        """Wait for every running turn to finish. For tests and shutdown."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
