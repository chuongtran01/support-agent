"""Where the agent's tool calls run.

The loop asks a backend to run a call as a customer and gets a ``ToolOutput`` back. It does not
know whether the tool ran in this process or somewhere else.
"""

import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

import anyio
import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import Tool

from app.agent import store
from app.agent.llm import ToolCall
from app.auth import CurrentCustomer, create_token
from app.config import settings
from app.mcp_server.mapping import IDEMPOTENCY_KEY, from_call_result
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
    went wrong next, so the step is ``unknown``. Each attempt, connecting included, is limited
    to ``timeout`` seconds.

    An ``unknown`` call is tried once more, with the same idempotency key, if repeating it is
    safe: a tool that only reads, or one that runs once per key. The server then either runs it
    for the first time or answers with what the first attempt did, so the step happens once and
    the customer learns how it ended. If the second attempt is unknown too, so is the result.
    """

    def __init__(
        self,
        url: str,
        *,
        http_client: Callable[[dict[str, str]], httpx2.AsyncClient] = _plain_client,
        lifetime: timedelta = TOKEN_LIFETIME,
        timeout: float | None = None,
        tools: dict[str, ToolSpec] = TOOLS,
        new_key: Callable[[], str] = lambda: uuid.uuid4().hex,
    ):
        self.url = url
        self.http_client = http_client
        self.lifetime = lifetime
        self.timeout = settings.mcp_call_timeout_seconds if timeout is None else timeout
        self.tools = tools
        self.new_key = new_key

    async def run(self, customer: CurrentCustomer, call: ToolCall) -> ToolOutput:
        try:
            arguments = json.loads(call.arguments or "{}")
        except ValueError:
            arguments = None
        if not isinstance(arguments, dict):
            return _output("invalid_request", "The arguments must be a JSON object.")

        # One key for this call, kept for the retry. A tool that is not idempotent ignores it
        key = self.new_key()
        output = await self._attempt(customer, call, arguments, key)
        if output.status == "unknown" and self._safe_to_repeat(call.name):
            logger.warning("tool call %s over MCP is unknown, trying once more", call.name)
            output = await self._attempt(customer, call, arguments, key)
        return output

    async def list_tools(self, customer: CurrentCustomer) -> list[Tool]:
        """The tools the server offers this customer. Raises if it cannot be reached."""
        token = create_token(customer.id, lifetime=self.lifetime)
        with anyio.fail_after(self.timeout):
            async with (
                self.http_client({"Authorization": f"Bearer {token}"}) as http,
                Client(streamable_http_client(self.url, http_client=http)) as client,
            ):
                return (await client.list_tools()).tools

    def _safe_to_repeat(self, name: str) -> bool:
        spec = self.tools.get(name)
        return spec is not None and (spec.action is None or spec.idempotent)

    async def _attempt(
        self, customer: CurrentCustomer, call: ToolCall, arguments: dict, key: str
    ) -> ToolOutput:
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
                    result = await client.call_tool(
                        call.name, arguments, meta={IDEMPOTENCY_KEY: key}
                    )
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
