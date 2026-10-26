"""Where the agent's tool calls run.

The loop asks a backend to run a call as a customer and gets a ``ToolOutput`` back. It does not
know whether the tool ran in this process or somewhere else.
"""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

import anyio
import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from app.agent import store
from app.agent.llm import ToolCall
from app.auth import CurrentCustomer, create_token
from app.config import settings
from app.mcp_server.mapping import from_call_result
from app.retrieval.embeddings import Embedder
from app.tools.context import ToolContext, utc_now
from app.tools.registry import TOOLS, ToolOutput, ToolSpec, run_tool

logger = logging.getLogger(__name__)

# Long enough for one call, short enough that a token found in a log is already dead
TOKEN_LIFETIME = timedelta(minutes=2)

# The call never left this process, so nothing was done and it is safe to say so
NOT_DONE_MESSAGE = (
    "The tool server could not be reached, so this step was not done. "
    "Tell the customer you could not complete it just now."
)
# The call was sent, so the server may have acted. Never say "nothing was changed" here
UNKNOWN_WRITE_MESSAGE = (
    "The result of this step is unknown: it may or may not have been done. Do not assume "
    "either, and do not repeat it. Tell the customer you could not confirm it."
)
UNKNOWN_READ_MESSAGE = (
    "The result of this step is unknown, so nothing was looked up. "
    "You may try again, or tell the customer you could not look it up just now."
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

    A call that fails is either ``error`` or ``unknown``. If it failed before the request that
    runs the tool was sent (connecting, initializing, a refused token), nothing was done: it is
    an ``error`` that says so. After that request was sent the server may have acted, whatever
    went wrong next, so the step is ``unknown``. The whole call, connecting included, is
    limited to ``timeout`` seconds.
    """

    def __init__(
        self,
        url: str,
        *,
        http_client: Callable[[dict[str, str]], httpx2.AsyncClient] = _plain_client,
        lifetime: timedelta = TOKEN_LIFETIME,
        timeout: float | None = None,
        tools: dict[str, ToolSpec] = TOOLS,
    ):
        self.url = url
        self.http_client = http_client
        self.lifetime = lifetime
        self.timeout = settings.mcp_call_timeout_seconds if timeout is None else timeout
        self.tools = tools

    async def run(self, customer: CurrentCustomer, call: ToolCall) -> ToolOutput:
        try:
            arguments = json.loads(call.arguments or "{}")
        except ValueError:
            arguments = None
        if not isinstance(arguments, dict):
            return _output("invalid_request", "The arguments must be a JSON object.")

        token = create_token(customer.id, lifetime=self.lifetime)
        sent = False
        result = None
        try:
            with anyio.fail_after(self.timeout):
                async with (
                    self.http_client({"Authorization": f"Bearer {token}"}) as http,
                    Client(streamable_http_client(self.url, http_client=http)) as client,
                ):
                    sent = True  # from here the server may run the tool
                    result = await client.call_tool(call.name, arguments)
        except Exception:
            if result is None:
                logger.exception("tool call %s over MCP failed (sent: %s)", call.name, sent)
                return self._failed(call, sent)
            # The answer is in; only closing the connection went wrong
            logger.warning("closing the MCP connection for %s failed", call.name, exc_info=True)
        return from_call_result(result)

    def _failed(self, call: ToolCall, sent: bool) -> ToolOutput:
        if not sent:
            return _output("error", NOT_DONE_MESSAGE)
        spec = self.tools.get(call.name)
        action = spec.action if spec else None
        if action is None:
            return _output("unknown", UNKNOWN_READ_MESSAGE)
        output = _output("unknown", UNKNOWN_WRITE_MESSAGE)
        return ToolOutput(
            output.text, output.status, f"Whether {action} went through could not be confirmed."
        )
