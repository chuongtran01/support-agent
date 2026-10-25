"""Where the agent's tool calls run.

The loop asks a backend to run a call as a customer and gets a ``ToolOutput`` back. It does not
know whether the tool ran in this process or somewhere else.
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from app.agent import store
from app.agent.llm import ToolCall
from app.auth import CurrentCustomer, create_token
from app.mcp_server.mapping import from_call_result
from app.retrieval.embeddings import Embedder
from app.tools.context import ToolContext, utc_now
from app.tools.registry import ToolOutput, run_tool

logger = logging.getLogger(__name__)

# Long enough for one call, short enough that a token found in a log is already dead
TOKEN_LIFETIME = timedelta(minutes=2)

# After a call was sent the server may have acted, so this never says "nothing was changed"
TRANSPORT_FAILED_MESSAGE = (
    "The tool server could not be reached or did not answer, so this step may or may not have "
    "been done. Do not assume either. Tell the customer you could not confirm it."
)


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


def _output(status: str, message: str) -> ToolOutput:
    """The same shape ``run_tool`` gives for a call that went wrong."""
    return ToolOutput(json.dumps({"status": status, "message": message}), status)


def _plain_client(headers: dict[str, str]) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(headers=headers)


class McpBackend:
    """The tools called through the MCP server, as the customer.

    Each call mints a short-lived token for the customer, connects, calls and disconnects. The
    customer comes from the loop's ``CurrentCustomer``, never from the model's arguments.
    ``http_client`` builds the HTTP client for a set of headers; tests give it one that talks
    to the app directly.
    """

    def __init__(
        self,
        url: str,
        *,
        http_client: Callable[[dict[str, str]], httpx2.AsyncClient] = _plain_client,
        lifetime: timedelta = TOKEN_LIFETIME,
    ):
        self.url = url
        self.http_client = http_client
        self.lifetime = lifetime

    async def run(self, customer: CurrentCustomer, call: ToolCall) -> ToolOutput:
        try:
            arguments = json.loads(call.arguments or "{}")
        except ValueError:
            arguments = None
        if not isinstance(arguments, dict):
            return _output("invalid_request", "The arguments must be a JSON object.")

        token = create_token(customer.id, lifetime=self.lifetime)
        try:
            async with (
                self.http_client({"Authorization": f"Bearer {token}"}) as http,
                Client(streamable_http_client(self.url, http_client=http)) as client,
            ):
                return from_call_result(await client.call_tool(call.name, arguments))
        except Exception:
            logger.exception("tool call %s over MCP failed", call.name)
            return _output("error", TRANSPORT_FAILED_MESSAGE)
