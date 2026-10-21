"""Run a turn apart from whoever started it, one at a time per session.

The turn is a task that writes its events to a queue; the caller reads the queue. If the caller
goes away, the task still finishes and saves its messages, so a refund that was made is never
missing from the conversation.
"""

import asyncio
import logging
import uuid

from app.agent.events import ErrorEvent, TurnEvent
from app.agent.loop import AgentDeps, run_turn
from app.auth import CurrentCustomer

logger = logging.getLogger(__name__)

TURN_FAILED_MESSAGE = "Something went wrong on our side. Please try again."


class TurnInProgress(Exception):
    """A turn is already running in this session."""


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
