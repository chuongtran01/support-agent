"""``ToolOutput`` to an MCP result and back, and a tool's schema to an MCP tool.

Plain functions with no server state: the server uses them to answer a call and the client
uses them to read the answer. The result's text is the tool's JSON for the model, untouched,
because the loop parses it for citations. The status and the receipt travel in ``_meta``; the
receipt is never in the content, because the model must not see it.
"""

from mcp_types import CallToolResult, TextContent, Tool

from app.tools.registry import ToolOutput, ToolSchema

# Prefixed so they cannot collide with keys the protocol reserves in ``_meta``
STATUS_KEY = "northwind/status"
RECEIPT_KEY = "northwind/receipt"

# The call itself went wrong. Every other status ("denied", "not_found", ...) is an answer
ERROR_STATUSES = frozenset({"invalid_request", "error"})


def to_tool(schema: ToolSchema) -> Tool:
    return Tool(name=schema.name, description=schema.description, input_schema=schema.parameters)


def to_call_result(output: ToolOutput) -> CallToolResult:
    meta = {STATUS_KEY: output.status}
    if output.receipt:
        meta[RECEIPT_KEY] = output.receipt
    return CallToolResult(
        content=[TextContent(type="text", text=output.text)],
        is_error=output.status in ERROR_STATUSES,
        meta=meta,
    )


def from_call_result(result: CallToolResult) -> ToolOutput:
    """Read a result back. One from a server that sets no status (not this one) is ``ok``, or
    ``error`` if it flagged an error, and has no receipt."""
    meta = result.meta or {}
    text = next((c.text for c in result.content if isinstance(c, TextContent)), "")
    status = meta.get(STATUS_KEY) or ("error" if result.is_error else "ok")
    return ToolOutput(text, status, meta.get(RECEIPT_KEY))
