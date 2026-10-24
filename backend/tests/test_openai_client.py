import pytest
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseError,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionToolCall,
    ResponseIncompleteEvent,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseReasoningItem,
    ResponseTextDeltaEvent,
    ResponseUsage,
)

from app.agent.llm import Done, LLMError, TextDelta, ToolCall, Usage
from app.agent.messages import (
    AssistantContent,
    FunctionCall,
    StoredToolCall,
    ToolContent,
    UserContent,
)
from app.agent.openai_client import (
    OpenAIClient,
    events_from_responses,
    to_responses_input,
    to_responses_tools,
)
from app.tools.registry import ToolSchema

# The SDK's events have many required fields; ``model_construct`` fills in only what is read


def text_event(text):
    return ResponseTextDeltaEvent.model_construct(delta=text)


def call_event(call_id, name, arguments):
    item = ResponseFunctionToolCall.model_construct(call_id=call_id, name=name, arguments=arguments)
    return ResponseOutputItemDoneEvent.model_construct(item=item)


def other_item_event(item_type):
    return ResponseOutputItemDoneEvent.model_construct(item=item_type.model_construct())


def completed_event(prompt, completion, event_type=ResponseCompletedEvent, model=None):
    usage = ResponseUsage.model_construct(input_tokens=prompt, output_tokens=completion)
    return event_type.model_construct(response=Response.model_construct(usage=usage, model=model))


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


async def test_done_carries_the_model_the_provider_says_it_used():
    events = await collect(
        events_from_responses(
            stream_of(text_event("Hi"), completed_event(10, 2, model="gpt-6-luna-2026-09-01"))
        )
    )
    assert events[-1] == Done(Usage(10, 2), "gpt-6-luna-2026-09-01")


async def test_text_and_calls_come_out_in_the_order_they_were_streamed():
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
        ToolCall("call_a", "lookup_order", '{"order_id": 1}'),
        TextDelta("One moment."),
        ToolCall("call_b", "escalate", '{"reason": "x"}'),
        Done(),
    ]


async def test_reasoning_and_message_items_are_not_calls():
    events = await collect(
        events_from_responses(
            stream_of(
                other_item_event(ResponseReasoningItem), other_item_event(ResponseOutputMessage)
            )
        )
    )
    assert events == [Done()]


async def test_an_incomplete_response_still_reports_its_usage():
    events = await collect(
        events_from_responses(stream_of(completed_event(50, 900, ResponseIncompleteEvent)))
    )
    assert events == [Done(Usage(50, 900))]


async def test_a_failed_response_raises_llm_error():
    error = ResponseError.model_construct(message="server_error")
    failed = ResponseFailedEvent.model_construct(response=Response.model_construct(error=error))
    with pytest.raises(LLMError, match="server_error"):
        await collect(events_from_responses(stream_of(text_event("Hi"), failed)))


async def test_an_error_event_raises_llm_error():
    with pytest.raises(LLMError, match="bad request"):
        await collect(
            events_from_responses(
                stream_of(ResponseErrorEvent.model_construct(message="bad request"))
            )
        )


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
    monkeypatch.setattr("app.agent.openai_client.settings.openai_api_key", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIClient()
