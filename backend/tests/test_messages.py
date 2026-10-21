import pytest
from pydantic import ValidationError

from app.agent.messages import (
    AssistantContent,
    FunctionCall,
    StoredToolCall,
    ToolContent,
    UserContent,
    parse_content,
)

CALL = StoredToolCall(
    id="c1", function=FunctionCall(name="lookup_order", arguments='{"order_id": 1}')
)


def test_the_stored_json_is_the_openai_message_without_its_role():
    assert UserContent(content="hi").as_json() == {"content": "hi"}
    assert AssistantContent(content="yes").as_json() == {"content": "yes"}
    assert ToolContent(tool_call_id="c1", content="{}").as_json() == {
        "tool_call_id": "c1",
        "content": "{}",
    }


def test_an_assistant_message_with_tool_calls_keeps_a_null_content():
    assert AssistantContent(content=None, tool_calls=(CALL,)).as_json() == {
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "lookup_order", "arguments": '{"order_id": 1}'},
            }
        ],
    }


def test_an_empty_tool_call_list_is_left_out_because_the_api_rejects_it():
    assert "tool_calls" not in AssistantContent(content="yes").as_openai()


def test_as_openai_adds_the_role():
    assert UserContent(content="hi").as_openai() == {"role": "user", "content": "hi"}
    assert ToolContent(tool_call_id="c1", content="{}").as_openai()["role"] == "tool"


@pytest.mark.parametrize(
    "content",
    [
        UserContent(content="hi"),
        AssistantContent(content="yes"),
        AssistantContent(content=None, tool_calls=(CALL,)),
        ToolContent(tool_call_id="c1", content="{}"),
    ],
)
def test_what_is_saved_is_read_back_unchanged(content):
    assert parse_content(content.role, content.as_json()) == content


@pytest.mark.parametrize(
    ("role", "raw"),
    [
        ("user", {"text": "wrong key"}),
        ("user", {"content": "hi", "extra": 1}),
        ("tool", {"content": "{}"}),
        ("assistant", {"content": "x", "tool_calls": [{"id": "c1"}]}),
    ],
)
def test_a_row_that_does_not_fit_its_role_is_rejected(role, raw):
    with pytest.raises(ValidationError):
        parse_content(role, raw)


def test_messages_cannot_be_changed_after_they_are_made():
    with pytest.raises(ValidationError):
        UserContent(content="hi").content = "changed"  # type: ignore[misc]
