import asyncio
import json
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app import models
from app.agent import store
from app.agent.events import Citation, DoneEvent, ErrorEvent, TextEvent, ToolEvent
from app.agent.llm import LLMError, TextDelta, ToolCall
from app.agent.loop import MODEL_UNAVAILABLE_MESSAGE, AgentDeps
from app.agent.messages import (
    AssistantContent,
    FunctionCall,
    StoredToolCall,
    ToolContent,
    UserContent,
)
from app.agent.runner import TURN_FAILED_MESSAGE, TurnInProgress
from app.api.chat import MAX_MESSAGE_CHARACTERS, MAX_SESSION_MESSAGES
from app.api.sse import sse_frame
from app.auth import CurrentCustomer
from app.main import app
from app.retrieval.search import SearchResult
from app.tools import knowledge
from tests.api_world import NOW, FakeEmbedder, parse_sse
from tests.scripted_llm import ScriptedLLM, call_tools, say


def lookup(api_, key, call_id="c1"):
    order_id = api_.seeded.orders[key].id
    return ToolCall(call_id, "lookup_order", json.dumps({"order_id": order_id}))


# Server-sent events, without a database


def test_each_event_is_one_frame_of_two_lines_and_a_blank_line():
    frame = sse_frame(TextEvent("Hello"))
    assert frame == 'event: text\ndata: {"text": "Hello"}\n\n'


def test_text_with_newlines_and_unicode_stays_on_one_data_line():
    frame = sse_frame(TextEvent('Line one\nLine "two" — café'))
    assert frame.count("\n") == 3
    assert json.loads(frame.split("data: ")[1])["text"] == 'Line one\nLine "two" — café'


def test_the_other_events_carry_what_the_browser_needs():
    tool = sse_frame(ToolEvent("lookup_order", "finished", "ok"))
    assert (
        tool
        == 'event: tool\ndata: {"name": "lookup_order", "phase": "finished", "status": "ok"}\n\n'
    )
    done = sse_frame(DoneEvent(7, (Citation("returns-policy", "Returns", "Returns > Window"),), 1))
    data = json.loads(done.split("data: ")[1])
    assert data == {
        "message_id": 7,
        "citations": [
            {
                "document_id": "returns-policy",
                "title": "Returns",
                "heading_path": "Returns > Window",
            }
        ],
        "dropped_citations": 1,
    }
    assert sse_frame(ErrorEvent("no")) == 'event: error\ndata: {"message": "no"}\n\n'


# Who may call what


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/sessions"),
        ("GET", "/sessions"),
        ("GET", f"/sessions/{uuid.uuid4()}/messages"),
        ("POST", f"/sessions/{uuid.uuid4()}/messages"),
    ],
)
async def test_every_chat_endpoint_needs_a_login(api, method, path):
    response = await api.client.request(method, path, json={"text": "hi"})
    assert response.status_code == 401


async def test_no_endpoint_takes_or_returns_a_customer_id():
    assert "customer_id" not in json.dumps(app.openapi())


# Sessions


async def test_a_new_chat_is_listed_for_its_owner_only(api):
    session_id = await api.new_chat("alex")
    mine = await api.client.get("/sessions", headers=api.headers("alex"))
    theirs = await api.client.get("/sessions", headers=api.headers("priya"))
    assert [s["id"] for s in mine.json()] == [session_id]
    assert theirs.json() == []


async def test_chats_are_listed_newest_first_with_the_first_question_as_a_preview(api):
    api.llm = ScriptedLLM([say("ok"), say("ok")])
    older = await api.new_chat()
    await api.send(older, "Where is my order? " + "x" * 200)
    newer = await api.new_chat()
    listed = (await api.client.get("/sessions", headers=api.headers("alex"))).json()
    assert [s["id"] for s in listed] == [newer, older]
    assert listed[0]["preview"] is None
    assert listed[1]["preview"].startswith("Where is my order?")
    assert len(listed[1]["preview"]) == 80 and listed[1]["preview"].endswith("…")


# Sending a message


async def test_a_message_streams_text_tool_progress_and_a_done_event(api):
    api.llm = ScriptedLLM([call_tools(lookup(api, "alex-5d")), say("Your boots arrived.")])
    session_id = await api.new_chat()
    response = await api.send(session_id, "where are my boots?")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["tool", "tool", "text", "text", "done"]
    assert events[0][1] == {"name": "lookup_order", "phase": "started", "status": None}
    assert events[1][1] == {"name": "lookup_order", "phase": "finished", "status": "ok"}
    assert "".join(data["text"] for name, data in events if name == "text") == "Your boots arrived."
    assert events[-1][1]["citations"] == [] and isinstance(events[-1][1]["message_id"], int)


async def test_the_conversation_can_be_read_back_without_tool_messages(api):
    api.llm = ScriptedLLM(
        [
            [TextDelta("Let me check. "), lookup(api, "alex-5d"), *say("")[-1:]],
            say("Your boots arrived."),
        ]
    )
    session_id = await api.new_chat()
    await api.send(session_id, "where are my boots?")
    shown = (
        await api.client.get(f"/sessions/{session_id}/messages", headers=api.headers("alex"))
    ).json()
    assert [(m["role"], m["text"]) for m in shown] == [
        ("user", "where are my boots?"),
        ("assistant", "Your boots arrived."),
    ]
    assert all(
        set(m) == {"id", "role", "text", "created_at", "citations", "dropped_citations"}
        for m in shown
    )


async def test_the_next_message_in_a_chat_sees_the_earlier_ones(api):
    api.llm = ScriptedLLM([say("First."), say("Second.")])
    session_id = await api.new_chat()
    await api.send(session_id, "one")
    await api.send(session_id, "two")
    roles = [m.role for m in api.llm.calls[1]["messages"]]
    assert roles == ["user", "assistant", "user"]


async def test_citations_in_the_done_event_match_what_was_retrieved(api, monkeypatch):
    async def fake_search(session, query, mode, limit, embedder):
        return [
            SearchResult(
                1,
                "returns-policy",
                "Returns policy",
                "current",
                date(2026, 1, 2),
                "Returns > Window",
                "30 days.",
                0.03,
                0.2,
                0.8,
            )
        ]

    monkeypatch.setattr(knowledge, "search", fake_search)
    search = ToolCall("c1", "search_knowledge", json.dumps({"query": "return window"}))
    api.llm = ScriptedLLM([call_tools(search), say("30 days [returns-policy] [made-up].")])
    session_id = await api.new_chat()
    events = parse_sse((await api.send(session_id, "how long?")).text)
    assert events[-1][1]["citations"] == [
        {
            "document_id": "returns-policy",
            "title": "Returns policy",
            "heading_path": "Returns > Window",
        }
    ]
    assert events[-1][1]["dropped_citations"] == 1


async def test_tools_act_for_the_token_holder_whatever_the_model_asks(api):
    boots = api.seeded.items["BOOT-TR"].id
    refund = ToolCall(
        "c1",
        "issue_refund",
        json.dumps({"order_item_id": boots, "quantity": 1, "reason_code": "changed_mind"}),
    )
    api.llm = ScriptedLLM([call_tools(refund), say("Done.")])
    session_id = await api.new_chat("priya")  # Priya's token, Alex's order item
    events = parse_sse((await api.send(session_id, "refund the boots", "priya")).text)
    assert ("tool", {"name": "issue_refund", "phase": "finished", "status": "not_found"}) in events
    async with api.sessions() as session:
        refunds = (
            await session.scalars(select(models.Refund).where(models.Refund.order_item_id == boots))
        ).all()
    assert [r for r in refunds if r.created_at >= NOW] == []  # only the seeded ones


# Citations when a chat is read back


def search_returns(monkeypatch, *documents):
    """Make the knowledge search return one passage for each ``(id, title, heading)``."""

    async def fake_search(session, query, mode, limit, embedder):
        return [
            SearchResult(
                i, doc, title, "current", date(2026, 1, 2), heading, "text", 0.03, 0.2, 0.8
            )
            for i, (doc, title, heading) in enumerate(documents, 1)
        ]

    monkeypatch.setattr(knowledge, "search", fake_search)


def searching(call_id="c1"):
    return call_tools(ToolCall(call_id, "search_knowledge", json.dumps({"query": "anything"})))


async def read_back(api_, session_id, customer="alex"):
    response = await api_.client.get(
        f"/sessions/{session_id}/messages", headers=api_.headers(customer)
    )
    return response.json()


async def test_a_chat_read_back_shows_the_same_citations_as_the_live_stream(api, monkeypatch):
    search_returns(
        monkeypatch,
        ("returns-policy", "Returns policy", "Returns > Window"),
        ("exchanges", "Exchanges", "Exchanges"),
    )
    api.llm = ScriptedLLM([searching(), say("30 days [returns-policy]. Fees vary [made-up].")])
    session_id = await api.new_chat()
    live = parse_sse((await api.send(session_id, "how long?")).text)[-1][1]

    answer = (await read_back(api, session_id))[-1]
    assert answer["role"] == "assistant"
    assert answer["citations"] == live["citations"] != []
    assert answer["dropped_citations"] == live["dropped_citations"] == 1


async def test_the_customers_own_messages_have_no_citations(api, monkeypatch):
    search_returns(monkeypatch, ("returns-policy", "Returns policy", "Returns"))
    api.llm = ScriptedLLM([searching(), say("30 days [returns-policy].")])
    session_id = await api.new_chat()
    await api.send(session_id, "how long? [returns-policy]")
    question = (await read_back(api, session_id))[0]
    assert (question["role"], question["citations"], question["dropped_citations"]) == (
        "user",
        [],
        0,
    )


async def test_a_source_found_in_an_earlier_turn_does_not_count_for_a_later_answer(
    api, monkeypatch
):
    search_returns(monkeypatch, ("returns-policy", "Returns policy", "Returns"))
    api.llm = ScriptedLLM(
        [searching(), say("30 days [returns-policy]."), say("Still 30 [returns-policy].")]
    )
    session_id = await api.new_chat()
    await api.send(session_id, "how long?")
    live = parse_sse((await api.send(session_id, "and still?")).text)[-1][1]

    first, second = [m for m in await read_back(api, session_id) if m["role"] == "assistant"]
    assert [c["document_id"] for c in first["citations"]] == ["returns-policy"]
    assert second["citations"] == live["citations"] == []
    assert second["dropped_citations"] == live["dropped_citations"] == 1


async def test_each_answer_uses_the_searches_of_its_own_turn(api, monkeypatch):
    search_returns(monkeypatch, ("returns-policy", "Returns policy", "Returns"))
    api.llm = ScriptedLLM(
        [searching(), say("A [returns-policy]."), searching("c2"), say("B [returns-policy].")]
    )
    session_id = await api.new_chat()
    await api.send(session_id, "one")
    await api.send(session_id, "two")
    answers = [m for m in await read_back(api, session_id) if m["role"] == "assistant"]
    assert [len(m["citations"]) for m in answers] == [1, 1]


async def test_only_search_results_count_as_sources(api, monkeypatch):
    """A tool result that happens to look like search output is not a source unless it came
    from search_knowledge."""
    fake = '{"status": "ok", "passages": [{"document_id": "returns-policy", "title": "T", "heading_path": "H"}]}'
    session_id = uuid.UUID(await api.new_chat())
    alex = api.customer_id("alex")
    await store.save_message(api.sessions, session_id, UserContent(content="hi"))
    call = StoredToolCall(id="c1", function=FunctionCall(name="lookup_order", arguments="{}"))
    await store.save_message(
        api.sessions, session_id, AssistantContent(content=None, tool_calls=(call,))
    )
    await store.save_message(api.sessions, session_id, ToolContent(tool_call_id="c1", content=fake))
    await store.save_message(
        api.sessions, session_id, AssistantContent(content="See [returns-policy].")
    )
    answer = (await store.load_display(api.sessions, session_id, alex))[-1]
    assert answer.citations == () and answer.dropped_citations == 1


async def test_a_chat_with_no_search_has_every_citation_dropped(api):
    api.llm = ScriptedLLM([say("It is 30 days [returns-policy].")])
    session_id = await api.new_chat()
    await api.send(session_id, "how long?")
    answer = (await read_back(api, session_id))[-1]
    assert (answer["citations"], answer["dropped_citations"]) == ([], 1)


# Other people's chats


async def test_another_customers_chat_is_not_found_for_reading_and_sending(api):
    session_id = await api.new_chat("alex")
    api.llm = ScriptedLLM([say("never")])
    read = await api.client.get(f"/sessions/{session_id}/messages", headers=api.headers("priya"))
    sent = await api.send(session_id, "hello", "priya")
    assert (read.status_code, sent.status_code) == (404, 404)
    assert api.llm.calls == []
    assert (
        await store.count_messages(api.sessions, uuid.UUID(session_id), api.customer_id("alex"))
        == 0
    )


async def test_an_unknown_chat_is_not_found_and_a_malformed_id_is_rejected(api):
    headers = api.headers("alex")
    assert (
        await api.client.get(f"/sessions/{uuid.uuid4()}/messages", headers=headers)
    ).status_code == 404
    assert (
        await api.client.get("/sessions/not-a-uuid/messages", headers=headers)
    ).status_code == 422


# What a message may be


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": ""},
        {"text": "   \n"},
        {"text": "x" * (MAX_MESSAGE_CHARACTERS + 1)},
        {"text": "hi", "customer_id": 2},
        {"message": "hi"},
    ],
)
async def test_a_bad_message_is_rejected_before_anything_happens(api, body):
    api.llm = ScriptedLLM([say("never")])
    session_id = await api.new_chat()
    response = await api.client.post(
        f"/sessions/{session_id}/messages", json=body, headers=api.headers("alex")
    )
    assert response.status_code == 422
    assert api.llm.calls == []


async def test_a_message_of_exactly_the_limit_is_accepted(api):
    api.llm = ScriptedLLM([say("ok")])
    session_id = await api.new_chat()
    assert (await api.send(session_id, "x" * MAX_MESSAGE_CHARACTERS)).status_code == 200


async def fill(api, session_id, count):
    async with api.sessions.begin() as session:
        for i in range(count):
            session.add(
                models.ChatMessage(
                    session_id=uuid.UUID(session_id), role="user", content={"content": f"m{i}"}
                )
            )


async def test_a_full_chat_asks_for_a_new_one(api):
    api.llm = ScriptedLLM([say("ok"), say("never")])
    session_id = await api.new_chat()
    await fill(api, session_id, MAX_SESSION_MESSAGES - 1)
    assert (await api.send(session_id, "one more fits")).status_code == 200

    full = await api.send(session_id, "this does not")
    assert full.status_code == 409
    assert full.json()["detail"]["code"] == "chat_full"
    assert len(api.llm.calls) == 1


# One turn at a time, and turns that outlive the request


class Gate(ScriptedLLM):
    """A model that waits to be let go, so a turn can be held in the middle."""

    def __init__(self, replies):
        super().__init__(replies)
        self.open = asyncio.Event()

    async def stream(self, system, messages, tools):
        await self.open.wait()
        async for event in super().stream(system, messages, tools):
            yield event


async def test_a_second_message_during_a_turn_is_refused_and_the_next_one_is_fine(api):
    api.llm = gate = Gate([say("First answer."), say("Second answer.")])
    session_id = await api.new_chat()
    first = asyncio.create_task(api.send(session_id, "one"))
    while not api.turns.is_running(uuid.UUID(session_id)):
        await asyncio.sleep(0)

    blocked = await api.send(session_id, "two, too soon")
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "turn_in_progress"

    gate.open.set()
    assert parse_sse((await first).text)[-1][0] == "done"
    assert (await api.send(session_id, "three")).status_code == 200
    assert len(gate.calls) == 2  # the refused message never reached the model


async def test_a_turn_finishes_and_saves_even_if_nobody_reads_it(api):
    api.llm = ScriptedLLM([call_tools(lookup(api, "alex-5d")), say("Found it.")])
    session_id = uuid.UUID(await api.new_chat())
    alex = api.seeded.customers["alex"]
    me = CurrentCustomer(
        id=alex.id, email=alex.email, name=alex.name, role=alex.role, membership=alex.membership
    )
    queue = api.turns.start(AgentDeps(api.sessions, api.llm, FakeEmbedder()), me, session_id, "hi")
    with pytest.raises(TurnInProgress):
        api.turns.start(AgentDeps(api.sessions, api.llm, FakeEmbedder()), me, session_id, "again")
    await api.turns.drain()  # the queue was never read: the browser went away

    assert not api.turns.is_running(session_id)
    stored = await store.load_history(api.sessions, session_id, alex.id)
    assert [m.role for m in stored] == ["user", "assistant", "tool", "assistant"]
    assert queue.qsize() > 0


async def test_a_model_outage_is_an_error_event_and_the_chat_stays_usable(api):
    class Down:
        async def stream(self, system, messages, tools):
            raise LLMError("rate limited")
            yield

    api.llm = Down()
    session_id = await api.new_chat()
    events = parse_sse((await api.send(session_id, "hi")).text)
    assert events == [("error", {"message": MODEL_UNAVAILABLE_MESSAGE})]
    api.llm = ScriptedLLM([say("Back again.")])
    assert parse_sse((await api.send(session_id, "hello?")).text)[-1][0] == "done"


async def test_a_bug_in_a_turn_is_an_error_event_not_a_hung_stream(api):
    class Broken:
        async def stream(self, system, messages, tools):
            raise ZeroDivisionError
            yield

    api.llm = Broken()
    session_id = await api.new_chat()
    events = parse_sse((await api.send(session_id, "hi")).text)
    assert events == [("error", {"message": TURN_FAILED_MESSAGE})]
    assert not api.turns.is_running(uuid.UUID(session_id))


# Browsers


async def test_the_frontend_origin_may_call_the_api_and_others_may_not(api):
    ask = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization",
    }
    allowed = await api.client.options(
        "/sessions", headers={"Origin": "http://localhost:3000", **ask}
    )
    denied = await api.client.options(
        "/sessions", headers={"Origin": "https://evil.example", **ask}
    )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-origin" not in denied.headers
