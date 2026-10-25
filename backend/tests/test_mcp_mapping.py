import json

import pytest
from mcp_types import CallToolResult, ImageContent, TextContent

from app.mcp_server.mapping import (
    ERROR_STATUSES,
    RECEIPT_KEY,
    STATUS_KEY,
    from_call_result,
    to_call_result,
    to_tool,
)
from app.tools.registry import TOOLS, ToolOutput, tool_schemas

OUTPUTS = [
    ToolOutput('{"status": "ok", "orders": []}', "ok"),
    ToolOutput('{"status": "no_orders"}', "no_orders"),
    ToolOutput(
        '{"status": "allowed", "refund_id": 7}', "allowed", "A refund of $40.00 has been made."
    ),
    ToolOutput('{"status": "denied"}', "denied"),
    ToolOutput('{"status": "invalid_request"}', "invalid_request"),
    ToolOutput('{"status": "error"}', "error"),
    ToolOutput('{"status": "ok", "text": "héllo \\u2603 \\n"}', "ok"),
]


@pytest.mark.parametrize("output", OUTPUTS, ids=lambda o: o.status)
def test_a_result_round_trips(output):
    assert from_call_result(to_call_result(output)) == output


@pytest.mark.parametrize("output", OUTPUTS, ids=lambda o: o.status)
def test_the_content_is_exactly_the_text(output):
    result = to_call_result(output)

    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == output.text


def test_the_receipt_is_in_meta_and_never_in_the_content():
    output = ToolOutput('{"status": "allowed"}', "allowed", "A refund of $40.00 has been made.")

    result = to_call_result(output)

    assert result.meta[RECEIPT_KEY] == output.receipt
    assert output.receipt not in json.dumps([c.model_dump() for c in result.content])


def test_no_receipt_leaves_the_key_out():
    result = to_call_result(ToolOutput('{"status": "ok"}', "ok"))

    assert RECEIPT_KEY not in result.meta
    assert result.meta[STATUS_KEY] == "ok"


@pytest.mark.parametrize("status", ["ok", "no_results", "denied", "not_found", "needs_approval"])
def test_an_answer_is_not_an_error(status):
    assert to_call_result(ToolOutput("{}", status)).is_error is False


@pytest.mark.parametrize("status", sorted(ERROR_STATUSES))
def test_a_bad_call_is_flagged_as_an_error(status):
    assert to_call_result(ToolOutput("{}", status)).is_error is True


def test_meta_goes_out_under_the_protocol_names():
    wire = to_call_result(ToolOutput("{}", "error")).model_dump(by_alias=True, exclude_none=True)

    assert wire["_meta"] == {STATUS_KEY: "error"}
    assert wire["isError"] is True


def test_a_result_with_no_status_is_ok():
    result = CallToolResult(content=[TextContent(type="text", text="hi")])

    assert from_call_result(result) == ToolOutput("hi", "ok")


def test_a_flagged_result_with_no_status_is_an_error():
    result = CallToolResult(content=[TextContent(type="text", text="boom")], is_error=True)

    assert from_call_result(result) == ToolOutput("boom", "error")


def test_a_result_with_no_text_reads_as_empty():
    result = CallToolResult(
        content=[ImageContent(type="image", data="AA==", mime_type="image/png")]
    )

    assert from_call_result(result).text == ""


@pytest.mark.parametrize("schema", tool_schemas(), ids=lambda s: s.name)
def test_a_tool_keeps_the_registry_schema(schema):
    tool = to_tool(schema)

    assert tool.name == schema.name
    assert tool.description == schema.description
    assert tool.input_schema == schema.parameters
    assert tool.name in TOOLS
