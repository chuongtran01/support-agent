import pytest

from app.agent.llm import Done, TextDelta, ToolCall
from app.agent.messages import UserContent
from app.tools.registry import ToolSchema
from tests.scripted_llm import ScriptedLLM, call_tools, say


async def collect(events):
    return [event async for event in events]


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
