"""Where the agent's tool calls run.

The loop asks a backend to run a call as a customer and gets a ``ToolOutput`` back. It does not
know whether the tool ran in this process or somewhere else.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from app.agent import store
from app.agent.llm import ToolCall
from app.auth import CurrentCustomer
from app.retrieval.embeddings import Embedder
from app.tools.context import ToolContext, utc_now
from app.tools.registry import ToolOutput, run_tool


class ToolBackend(Protocol):
    async def run(self, customer: CurrentCustomer, call: ToolCall) -> ToolOutput:
        """Run one tool call as the customer. The model never supplies who that is."""
        ...


class LocalBackend:
    """The tools called in this process, each call in its own database transaction."""

    def __init__(
        self,
        sessions: store.Sessions,
        embedder: Embedder | None = None,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.sessions = sessions
        self.embedder = embedder
        self.clock = clock

    async def run(self, customer: CurrentCustomer, call: ToolCall) -> ToolOutput:
        async with self.sessions() as session:
            ctx = ToolContext(session, customer.id, self.clock, self.embedder)
            return await run_tool(ctx, call.name, call.arguments)
