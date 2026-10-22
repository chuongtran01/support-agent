from types import SimpleNamespace as NS

import pytest

from app.agent.llm import (
    Done,
    LLMError,
    OpenAIClient,
    ScriptedLLM,
    TextDelta,
    ToolCall,
    Usage,
    call_tools,
    events_from_responses,
    say,
    to_responses_input,
    to_responses_tools,
)
from app.agent.messages import (
    AssistantContent,
    FunctionCall,
    StoredToolCall,
    ToolContent,
    UserContent,
)
from app.tools.registry import ToolSchema


def text_event(text):
    return NS(type="response.output_text.delta", delta=text)


def call_event(call_id, name, arguments):
    item = NS(type="function_call", call_id=call_id, name=name, arguments=arguments)
    return NS(type="response.output_item.done", item=item)


def other_item_event(kind):
    return NS(type="response.output_item.done", item=NS(type=kind))


def completed_event(prompt, completion, kind="response.completed"):
    usage = NS(input_tokens=prompt, output_tokens=completion)
    return NS(type=kind, response=NS(usage=usage))


async def stream_of(*events):
    for event in events:
        yield event


async def collect(events):
    return [event async for event in events]


async def test_text_arrives_piece_by_piece_then_done_with_usage():
    events = await collect(
        events_from_responses(
            stream_of(text_event("Hel"), text_event("lo"), completed_event(120, 4))
        )
    )
    assert events == [TextDelta("Hel"), TextDelta("lo"), Done(Usage(120, 4))]


async def test_calls_come_out_in_order_and_after_the_text():
    events = await collect(
        events_from_responses(
            stream_of(
                call_event("call_a", "lookup_order", '{"order_id": 1}'),
                text_event("One moment."),
                call_event("call_b", "escalate", '{"reason": "x"}'),
            )
        )
    )
    assert events == [
        TextDelta("One moment."),
        ToolCall("call_a", "lookup_order", '{"order_id": 1}'),
        ToolCall("call_b", "escalate", '{"reason": "x"}'),
        Done(),
    ]


async def test_reasoning_and_message_items_are_not_calls():
    events = await collect(
        events_from_responses(stream_of(other_item_event("reasoning"), other_item_event("message")))
    )
    assert events == [Done()]


async def test_an_incomplete_response_still_reports_its_usage():
    events = await collect(
        events_from_responses(stream_of(completed_event(50, 900, "response.incomplete")))
    )
    assert events == [Done(Usage(50, 900))]


async def test_a_failed_response_raises_llm_error():
    failed = NS(type="response.failed", response=NS(error=NS(message="server_error")))
    with pytest.raises(LLMError, match="server_error"):
        await collect(events_from_responses(stream_of(text_event("Hi"), failed)))


async def test_an_error_event_raises_llm_error():
    with pytest.raises(LLMError, match="bad request"):
        await collect(events_from_responses(stream_of(NS(type="error", message="bad request"))))


def test_tool_schemas_become_responses_api_tools():
    tools = [ToolSchema("t", "d", {"type": "object"})]
    assert to_responses_tools(tools) == [
        {"type": "function", "name": "t", "description": "d", "parameters": {"type": "object"}}
    ]


def test_a_conversation_becomes_responses_input_items():
    call = StoredToolCall(
        id="call_1", function=FunctionCall(name="lookup_order", arguments='{"order_id": 7}')
    )
    messages = [
        UserContent(content="Order 7?"),
        AssistantContent(content=None, tool_calls=(call,)),
        ToolContent(tool_call_id="call_1", content='{"status": "ok"}'),
        AssistantContent(content="It shipped."),
    ]
    assert to_responses_input(messages) == [
        {"role": "user", "content": "Order 7?"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "lookup_order",
            "arguments": '{"order_id": 7}',
        },
        {"type": "function_call_output", "call_id": "call_1", "output": '{"status": "ok"}'},
        {"role": "assistant", "content": "It shipped."},
    ]


def test_an_assistant_message_with_text_and_calls_keeps_both_in_order():
    call = StoredToolCall(id="c", function=FunctionCall(name="n", arguments="{}"))
    items = to_responses_input([AssistantContent(content="Checking.", tool_calls=(call,))])
    assert [i.get("type", "message") for i in items] == ["message", "function_call"]


def test_the_openai_client_needs_a_key(monkeypatch):
    monkeypatch.setattr("app.agent.llm.settings.openai_api_key", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIClient()


async def test_the_script_plays_one_reply_per_call_and_records_the_calls():
    llm = ScriptedLLM([say("Hi there"), call_tools(ToolCall("c1", "escalate", "{}"))])
    tool = ToolSchema("t", "d", {})
    first = await collect(llm.stream("be brief", [UserContent(content="hi")], [tool]))
    second = await collect(llm.stream("be brief", [UserContent(content="again")], []))
    assert "".join(e.text for e in first if isinstance(e, TextDelta)) == "Hi there"
    assert second == [ToolCall("c1", "escalate", "{}"), Done()]
    assert [c["messages"][0].content for c in llm.calls] == ["hi", "again"]
    assert llm.calls[0]["system"] == "be brief"
    assert llm.calls[0]["tools"] == [tool]


async def test_a_reply_without_done_gets_one():
    llm = ScriptedLLM([[TextDelta("x")]])
    assert (await collect(llm.stream("", [], [])))[-1] == Done()


async def test_calling_the_model_more_often_than_scripted_fails():
    llm = ScriptedLLM([say("only one")])
    await collect(llm.stream("", [], []))
    with pytest.raises(AssertionError):
        await collect(llm.stream("", [], []))
