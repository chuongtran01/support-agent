from types import SimpleNamespace as NS

import pytest

from app.agent.llm import (
    Done,
    OpenAIClient,
    ScriptedLLM,
    TextDelta,
    ToolCall,
    Usage,
    call_tools,
    events_from_chunks,
    say,
)


def text_chunk(text):
    return NS(usage=None, choices=[NS(delta=NS(content=text, tool_calls=None))])


def tool_chunk(index, id=None, name=None, arguments=None):
    function = NS(name=name, arguments=arguments)
    piece = NS(index=index, id=id, function=function)
    return NS(usage=None, choices=[NS(delta=NS(content=None, tool_calls=[piece]))])


def usage_chunk(prompt, completion):
    return NS(usage=NS(prompt_tokens=prompt, completion_tokens=completion), choices=[])


async def stream_of(*chunks):
    for chunk in chunks:
        yield chunk


async def collect(events):
    return [event async for event in events]


async def test_text_arrives_piece_by_piece_then_done_with_usage():
    events = await collect(
        events_from_chunks(stream_of(text_chunk("Hel"), text_chunk("lo"), usage_chunk(120, 4)))
    )
    assert events == [TextDelta("Hel"), TextDelta("lo"), Done(Usage(120, 4))]


async def test_a_tool_call_is_put_together_from_its_pieces():
    events = await collect(
        events_from_chunks(
            stream_of(
                tool_chunk(0, id="call_1", name="lookup_order", arguments=""),
                tool_chunk(0, arguments='{"order'),
                tool_chunk(0, arguments='_id": 7}'),
            )
        )
    )
    assert events == [ToolCall("call_1", "lookup_order", '{"order_id": 7}'), Done()]


async def test_parallel_tool_calls_come_out_in_order_and_after_the_text():
    events = await collect(
        events_from_chunks(
            stream_of(
                tool_chunk(1, id="call_b", name="escalate", arguments='{"reason":'),
                tool_chunk(0, id="call_a", name="lookup_order", arguments='{"order_id": 1}'),
                text_chunk("One moment."),
                tool_chunk(1, arguments=' "x"}'),
            )
        )
    )
    assert events == [
        TextDelta("One moment."),
        ToolCall("call_a", "lookup_order", '{"order_id": 1}'),
        ToolCall("call_b", "escalate", '{"reason": "x"}'),
        Done(),
    ]


async def test_a_chunk_with_no_choices_and_no_usage_is_ignored():
    empty = NS(usage=None, choices=[])
    assert await collect(events_from_chunks(stream_of(empty))) == [Done()]


def test_the_openai_client_needs_a_key(monkeypatch):
    monkeypatch.setattr("app.agent.llm.settings.openai_api_key", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIClient()


async def test_the_script_plays_one_reply_per_call_and_records_the_calls():
    llm = ScriptedLLM([say("Hi there"), call_tools(ToolCall("c1", "escalate", "{}"))])
    first = await collect(llm.stream([{"role": "user", "content": "hi"}], [{"t": 1}]))
    second = await collect(llm.stream([{"role": "user", "content": "again"}], []))
    assert "".join(e.text for e in first if isinstance(e, TextDelta)) == "Hi there"
    assert second == [ToolCall("c1", "escalate", "{}"), Done()]
    assert [c["messages"][0]["content"] for c in llm.calls] == ["hi", "again"]
    assert llm.calls[0]["tools"] == [{"t": 1}]


async def test_a_reply_without_done_gets_one():
    llm = ScriptedLLM([[TextDelta("x")]])
    assert (await collect(llm.stream([], [])))[-1] == Done()


async def test_calling_the_model_more_often_than_scripted_fails():
    llm = ScriptedLLM([say("only one")])
    await collect(llm.stream([], []))
    with pytest.raises(AssertionError):
        await collect(llm.stream([], []))
