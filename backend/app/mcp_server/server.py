"""The MCP server: lists the registry's tools and runs a call as the customer who made it.

It adds no rules. ``run_tool`` validates the arguments, runs the handler in one transaction and
turns every failure into a result; this file works out who is calling, builds the context the
agent used to pass in, and maps the output (see ``mapping``).
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INVALID_REQUEST,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
)

from app.agent import store
from app.auth import CurrentCustomer
from app.mcp_server.mapping import to_call_result, to_tool
from app.retrieval.embeddings import Embedder
from app.tools.context import ToolContext, utc_now
from app.tools.registry import TOOLS, ToolSpec, run_tool, tool_schemas

# Gets the request's headers (None when the transport has none) and returns the customer they
# stand for, or None. The server never learns the customer from a tool argument.
Authenticate = Callable[[Mapping[str, str] | None], Awaitable[CurrentCustomer | None]]


def create_server(
    sessions: store.Sessions,
    authenticate: Authenticate,
    *,
    embedder: Embedder | None = None,
    clock: Callable[[], datetime] = utc_now,
    tools: dict[str, ToolSpec] = TOOLS,
) -> Server:
    listed = [to_tool(schema) for schema in tool_schemas(tools)]

    async def caller(ctx: ServerRequestContext[Any, Any]) -> CurrentCustomer:
        headers = ctx.request.headers if ctx.request is not None else None
        customer = await authenticate(headers)
        if customer is None:
            raise MCPError(INVALID_REQUEST, "Not signed in.")
        return customer

    async def list_tools(
        ctx: ServerRequestContext[Any, Any], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        await caller(ctx)
        return ListToolsResult(tools=listed)

    async def call_tool(
        ctx: ServerRequestContext[Any, Any], params: CallToolRequestParams
    ) -> CallToolResult:
        customer = await caller(ctx)
        async with sessions() as session:
            tool_context = ToolContext(session, customer.id, clock, embedder)
            output = await run_tool(
                tool_context, params.name, json.dumps(params.arguments or {}), tools
            )
        return to_call_result(output)

    return Server("northwind-support", on_list_tools=list_tools, on_call_tool=call_tool)
