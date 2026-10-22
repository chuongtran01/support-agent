import json
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models
from app.agent import store
from app.agent.events import (
    Citation,
    DoneEvent,
    ErrorEvent,
    TextEvent,
    ToolEvent,
)
from app.agent.llm import Done, LLMError, TextDelta, ToolCall, Usage
from app.agent.loop import (
    MODEL_UNAVAILABLE_MESSAGE,
    NO_ANSWER_MESSAGE,
    STEP_LIMIT_MESSAGE,
    AgentDeps,
    run_turn,
)
from app.agent.messages import (
    AssistantContent,
    FunctionCall,
    StoredToolCall,
    ToolContent,
    UserContent,
)
from app.auth import CurrentCustomer
from app.retrieval.search import SearchResult
from app.seed import seed
from app.tools import knowledge
from tests.scripted_llm import ScriptedLLM, call_tools, say

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


class FakeEmbedder:
    async def embed(self, texts):
        return [[0.0] for _ in texts]


class World:
    def __init__(self, engine, sessions, seeded):
        self.engine = engine
        self.sessions = sessions
        self.seeded = seeded
        self.customers = {
            key: CurrentCustomer(
                id=row.id, email=row.email, name=row.name, role=row.role, membership=row.membership
            )
            for key, row in seeded.customers.items()
        }

    def deps(self, llm, **overrides):
        return AgentDeps(
            self.sessions, llm, embedder=FakeEmbedder(), clock=lambda: NOW, **overrides
        )

    async def chat(self, customer="alex"):
        return (await store.create_session(self.sessions, self.customers[customer].id)).id

    async def turn(self, llm, text, customer="alex", session_id=None, **overrides):
        """Run one turn and return (session id, events)."""
        session_id = session_id or await self.chat(customer)
        deps = self.deps(llm, **overrides)
        events = [e async for e in run_turn(deps, self.customers[customer], session_id, text)]
        return session_id, events

    def order_id(self, key):
        return self.seeded.orders[key].id

    def item_id(self, sku):
        return self.seeded.items[sku].id

    async def count(self, model):
        async with self.sessions() as session:
            return await session.scalar(select(func.count()).select_from(model))


@pytest_asyncio.fixture
async def world(session, test_database_url):
    async with session.begin():
        seeded = await seed(session, NOW, "demo")
    engine = create_async_engine(test_database_url)
    world = World(engine, async_sessionmaker(engine, expire_on_commit=False), seeded)
    yield world
    await engine.dispose()


async def saved(world, customer, session_id):
    return await store.load_history(world.sessions, session_id, world.customers[customer].id)


def call(call_id, name, **arguments):
    return ToolCall(call_id, name, json.dumps(arguments))


def texts(events):
    return "".join(e.text for e in events if isinstance(e, TextEvent))


def only(events, kind):
    return [e for e in events if isinstance(e, kind)]


# A plain answer


async def test_a_plain_answer_streams_saves_and_finishes(world):
    llm = ScriptedLLM([say("Hello Alex, how can I help?")])
    session_id, events = await world.turn(llm, "hi")
    assert texts(events) == "Hello Alex, how can I help?"
    assert len(only(events, TextEvent)) == 2  # it arrived in pieces
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.model_calls == 1
    stored = await saved(world, "alex", session_id)
    assert [(m.role, m.content) for m in stored] == [
        ("user", UserContent(content="hi")),
        ("assistant", AssistantContent(content="Hello Alex, how can I help?")),
    ]
    assert stored[-1].id == done.message_id


async def test_the_model_gets_the_system_prompt_the_history_and_all_the_tools(world):
    llm = ScriptedLLM([say("ok")])
    await world.turn(llm, "hi")
    sent = llm.calls[0]
    assert "Alex Rivera" in sent["system"]
    assert "Basic membership" in sent["system"]
    assert sent["messages"] == [UserContent(content="hi")]
    assert [t.name for t in sent["tools"]] == [
        "search_knowledge",
        "lookup_order",
        "issue_refund",
        "create_ticket",
        "escalate",
    ]


async def test_the_system_prompt_is_not_stored(world):
    session_id, _ = await world.turn(ScriptedLLM([say("ok")]), "hi")
    assert {m.role for m in await saved(world, "alex", session_id)} == {"user", "assistant"}


async def test_usage_is_added_up_across_model_calls(world):
    lookup = call("c1", "lookup_order", order_id=world.order_id("alex-5d"))
    llm = ScriptedLLM(
        [
            [lookup, Done(Usage(100, 10))],
            [TextDelta("done"), Done(Usage(150, 5))],
        ]
    )
    _, events = await world.turn(llm, "status of my order?")
    done = events[-1]
    assert (done.model_calls, done.prompt_tokens, done.completion_tokens) == (2, 250, 15)


# Tools


async def test_a_tool_call_is_run_saved_and_fed_back_to_the_model(world):
    order_id = world.order_id("alex-5d")
    llm = ScriptedLLM(
        [
            [TextDelta("Let me check. "), call("c1", "lookup_order", order_id=order_id), Done()],
            say("Your boots were delivered."),
        ]
    )
    session_id, events = await world.turn(llm, f"Where is order {order_id}?")

    assert [type(e).__name__ for e in events] == [
        "TextEvent",
        "ToolEvent",
        "ToolEvent",
        "TextEvent",
        "TextEvent",
        "DoneEvent",
    ]
    assert events[1] == ToolEvent("lookup_order", "started")
    assert events[2] == ToolEvent("lookup_order", "finished", "ok")

    stored = await saved(world, "alex", session_id)
    assert [m.role for m in stored] == ["user", "assistant", "tool", "assistant"]
    assert stored[1].content.content == "Let me check. "
    assert stored[1].content.tool_calls[0].function.name == "lookup_order"
    assert stored[2].content.tool_call_id == "c1"
    assert json.loads(stored[2].content.content)["order"]["id"] == order_id

    second = llm.calls[1]["messages"]
    assert [m.role for m in second] == ["user", "assistant", "tool"]
    assert second[2].tool_call_id == "c1"


async def test_several_tool_calls_in_one_reply_run_in_order(world):
    a, b = world.order_id("alex-5d"), world.order_id("alex-29d")
    llm = ScriptedLLM(
        [
            call_tools(
                call("c1", "lookup_order", order_id=a), call("c2", "lookup_order", order_id=b)
            ),
            say("Both found."),
        ]
    )
    session_id, events = await world.turn(llm, "compare my orders")
    assert [e.phase for e in only(events, ToolEvent)] == ["started", "finished"] * 2
    stored = await saved(world, "alex", session_id)
    assert [m.content.tool_call_id for m in stored if m.role == "tool"] == ["c1", "c2"]


async def test_a_refund_is_made_by_the_policy_engine_and_reported_as_it_decided(world):
    item = world.item_id("BOOT-TR")
    llm = ScriptedLLM(
        [
            call_tools(
                call(
                    "c1", "issue_refund", order_item_id=item, quantity=1, reason_code="changed_mind"
                )
            ),
            say("Refunded."),
        ]
    )
    _, events = await world.turn(llm, "refund my boots, I changed my mind")
    assert ToolEvent("issue_refund", "finished", "allowed") in events
    async with world.sessions() as session:
        refunds = (
            await session.scalars(select(models.Refund).where(models.Refund.order_item_id == item))
        ).all()
    new = [r for r in refunds if r.created_at >= NOW]
    assert len(new) == 1
    assert (new[0].status, new[0].customer_id) == ("completed", world.customers["alex"].id)


async def test_a_refund_the_model_names_an_amount_for_is_refused(world):
    item = world.item_id("BOOT-TR")
    before = await world.count(models.Refund)
    llm = ScriptedLLM(
        [
            call_tools(
                call(
                    "c1",
                    "issue_refund",
                    order_item_id=item,
                    quantity=1,
                    reason_code="changed_mind",
                    amount_cents=50000,
                )
            ),
            say("Sorry, I could not do that."),
        ]
    )
    _, events = await world.turn(llm, "refund $500")
    assert ToolEvent("issue_refund", "finished", "invalid_request") in events
    assert await world.count(models.Refund) == before


async def test_another_customers_order_looks_like_it_does_not_exist(world):
    priya_order = world.order_id("priya-45d")
    llm = ScriptedLLM([call_tools(call("c1", "lookup_order", order_id=priya_order)), say("None.")])
    session_id, events = await world.turn(llm, f"show order {priya_order}")
    assert ToolEvent("lookup_order", "finished", "not_found") in events
    stored = await saved(world, "alex", session_id)
    assert "Priya" not in json.dumps([m.content.as_json() for m in stored])


async def test_tool_calls_run_as_the_session_customer_not_whoever_the_model_names(world):
    llm = ScriptedLLM(
        [
            call_tools(call("c1", "create_ticket", subject="s", body="b", customer_id=999)),
            call_tools(call("c2", "create_ticket", subject="Zip", body="Stuck zipper")),
            say("Ticket opened."),
        ]
    )
    _, events = await world.turn(llm, "open a ticket")
    assert ToolEvent("create_ticket", "finished", "invalid_request") in events
    async with world.sessions() as session:
        tickets = (await session.scalars(select(models.Ticket))).all()
    assert [(t.customer_id, t.subject) for t in tickets] == [(world.customers["alex"].id, "Zip")]


async def test_a_bad_tool_call_goes_back_to_the_model_which_can_recover(world):
    llm = ScriptedLLM(
        [
            call_tools(
                ToolCall("c1", "delete_everything", "{}"), ToolCall("c2", "lookup_order", "{oops")
            ),
            say("Sorry, let me try again."),
        ]
    )
    _, events = await world.turn(llm, "hi")
    statuses = [e.status for e in only(events, ToolEvent) if e.phase == "finished"]
    assert statuses == ["invalid_request", "invalid_request"]
    assert isinstance(events[-1], DoneEvent)


# Citations


def found(document_id, heading="Returns > Window", title="Returns policy"):
    return SearchResult(
        chunk_id=1,
        document_id=document_id,
        title=title,
        status="current",
        updated_at=date(2026, 1, 2),
        heading_path=heading,
        content="30 days.",
        score=0.03,
        keyword_score=0.2,
        vector_score=0.8,
    )


@pytest.fixture
def knowledge_returns(monkeypatch):
    def set_results(*results):
        async def fake_search(session, query, mode, limit, embedder):
            return list(results)

        monkeypatch.setattr(knowledge, "search", fake_search)

    return set_results


async def test_citations_match_what_was_retrieved_and_invented_ones_are_dropped(
    world, knowledge_returns
):
    knowledge_returns(found("returns-policy"), found("exchanges", "Exchanges", "Exchanges"))
    llm = ScriptedLLM(
        [
            call_tools(call("c1", "search_knowledge", query="return window")),
            say("You have 30 days [returns-policy]. Fees are waived [made-up-article]."),
        ]
    )
    _, events = await world.turn(llm, "how long to return?")
    done = events[-1]
    assert done.citations == (Citation("returns-policy", "Returns policy", "Returns > Window"),)
    assert done.dropped_citations == 1


async def test_a_citation_without_any_search_is_dropped(world):
    _, events = await world.turn(ScriptedLLM([say("It is 30 days [returns-policy].")]), "hi")
    assert events[-1].citations == () and events[-1].dropped_citations == 1


async def test_footnote_markers_are_not_citations(world):
    _, events = await world.turn(ScriptedLLM([say("See note [1] and [2].")]), "hi")
    assert events[-1].dropped_citations == 0


async def test_documents_retrieved_in_an_earlier_turn_do_not_count(world, knowledge_returns):
    knowledge_returns(found("returns-policy"))
    first = ScriptedLLM([call_tools(call("c1", "search_knowledge", query="q")), say("ok")])
    session_id, _ = await world.turn(first, "question")
    second = ScriptedLLM([say("Still 30 days [returns-policy].")])
    _, events = await world.turn(second, "and again?", session_id=session_id)
    assert events[-1].citations == () and events[-1].dropped_citations == 1


# History


async def test_the_next_turn_sees_everything_said_before(world):
    order_id = world.order_id("alex-5d")
    first = ScriptedLLM(
        [call_tools(call("c1", "lookup_order", order_id=order_id)), say("Found it.")]
    )
    session_id, _ = await world.turn(first, "look up my order")
    second = ScriptedLLM([say("Anything else?")])
    await world.turn(second, "thanks", session_id=session_id)
    roles = [m.role for m in second.calls[0]["messages"]]
    assert roles == ["user", "assistant", "tool", "assistant", "user"]


async def test_a_session_that_is_not_the_customers_is_refused_and_untouched(world):
    session_id = await world.chat("priya")
    llm = ScriptedLLM([say("never")])
    with pytest.raises(store.SessionNotFound):
        await world.turn(llm, "hello", customer="alex", session_id=session_id)
    assert llm.calls == []
    assert await saved(world, "priya", session_id) == []


async def test_no_database_connection_is_held_while_the_model_is_called(world):
    in_use = []

    class Probe(ScriptedLLM):
        async def stream(self, system, messages, tools):
            in_use.append(world.engine.sync_engine.pool.checkedout())
            async for event in super().stream(system, messages, tools):
                yield event

    lookup = call("c1", "lookup_order", order_id=world.order_id("alex-5d"))
    await world.turn(Probe([call_tools(lookup), say("ok")]), "hi")
    assert in_use == [0, 0]


# Things going wrong


async def test_the_step_limit_opens_a_ticket_from_code_and_tells_the_customer(world):
    order_id = world.order_id("alex-5d")
    forever = [call_tools(call(f"c{i}", "lookup_order", order_id=order_id)) for i in range(3)]
    llm = ScriptedLLM(forever)
    session_id, events = await world.turn(llm, "keep looking", max_steps=3)

    assert len(llm.calls) == 3
    assert texts(events) == STEP_LIMIT_MESSAGE
    assert isinstance(events[-1], DoneEvent) and events[-1].model_calls == 3
    async with world.sessions() as session:
        tickets = (await session.scalars(select(models.Ticket))).all()
    assert [(t.kind, t.customer_id) for t in tickets] == [
        ("escalation", world.customers["alex"].id)
    ]
    assert "keep looking" in tickets[0].body
    assert tickets[0].body.endswith(
        "Tool calls made in this turn:\n- lookup_order: ok\n- lookup_order: ok\n- lookup_order: ok"
    )
    stored = await saved(world, "alex", session_id)
    assert stored[-1].role == "assistant" and stored[-1].content.content == STEP_LIMIT_MESSAGE


async def test_a_model_failure_ends_the_turn_with_an_error_and_keeps_the_question(world):
    class Down:
        async def stream(self, system, messages, tools):
            raise LLMError("rate limited")
            yield

    session_id, events = await world.turn(Down(), "hello")
    assert events == [ErrorEvent(MODEL_UNAVAILABLE_MESSAGE)]
    stored = await saved(world, "alex", session_id)
    assert [m.role for m in stored] == ["user"]


async def test_a_failure_after_some_text_still_ends_with_an_error(world):
    class Breaks:
        async def stream(self, system, messages, tools):
            yield TextDelta("Let me")
            raise LLMError("connection dropped")

    session_id, events = await world.turn(Breaks(), "hello")
    assert events == [TextEvent("Let me"), ErrorEvent(MODEL_UNAVAILABLE_MESSAGE)]
    assert [m.role for m in await saved(world, "alex", session_id)] == ["user"]


async def test_a_bug_in_the_client_is_not_swallowed(world):
    class Broken:
        async def stream(self, system, messages, tools):
            raise ZeroDivisionError
            yield

    with pytest.raises(ZeroDivisionError):
        await world.turn(Broken(), "hello")


async def test_an_empty_reply_is_an_error_not_a_blank_answer(world):
    session_id, events = await world.turn(ScriptedLLM([[Done()]]), "hello")
    assert events == [ErrorEvent(NO_ANSWER_MESSAGE)]
    assert [m.role for m in await saved(world, "alex", session_id)] == ["user"]


# Rebuilding a stored conversation


def stored(*contents):
    return [store.StoredMessage(i, c.role, c) for i, c in enumerate(contents, 1)]


def user(text):
    return UserContent(content=text)


def assistant_calling(*ids):
    return AssistantContent(
        content=None,
        tool_calls=tuple(
            StoredToolCall(id=i, function=FunctionCall(name="lookup_order", arguments="{}"))
            for i in ids
        ),
    )


def result(call_id):
    return ToolContent(tool_call_id=call_id, content="{}")


def test_a_complete_conversation_is_replayed_as_it_was_saved():
    messages = store.to_messages(
        stored(user("hi"), assistant_calling("a"), result("a"), AssistantContent(content="yes"))
    )
    assert [m.role for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[0] == user("hi")
    assert messages[3] == AssistantContent(content="yes")


def test_a_tool_call_whose_result_was_lost_gets_a_stand_in():
    messages = store.to_messages(stored(user("hi"), assistant_calling("a", "b"), result("a")))
    assert [m.role for m in messages] == ["user", "assistant", "tool", "tool"]
    assert messages[3].tool_call_id == "b"
    assert "did not finish" in messages[3].content


def test_a_lost_result_is_filled_in_before_the_next_message():
    messages = store.to_messages(stored(assistant_calling("a"), user("hello?")))
    assert [m.role for m in messages] == ["assistant", "tool", "user"]


def test_a_result_with_no_call_is_dropped():
    messages = store.to_messages(stored(user("hi"), result("ghost")))
    assert [m.role for m in messages] == ["user"]


async def test_a_stored_row_that_does_not_fit_its_role_fails_when_loaded(world):
    session_id = await world.chat()
    async with world.sessions.begin() as session:
        session.add(
            models.ChatMessage(session_id=session_id, role="user", content={"text": "wrong key"})
        )
    with pytest.raises(ValidationError):
        await saved(world, "alex", session_id)


async def test_a_refund_made_in_the_last_round_is_reported_to_the_customer_and_the_ticket(world):
    item = world.item_id("BOOT-TR")
    refund = call("c2", "issue_refund", order_item_id=item, quantity=1, reason_code="changed_mind")
    lookup = call("c1", "lookup_order", order_id=world.order_id("alex-5d"))
    llm = ScriptedLLM([call_tools(lookup), call_tools(refund)])
    session_id, events = await world.turn(llm, "refund my boots", max_steps=2)

    expected = f"{STEP_LIMIT_MESSAGE} A refund of $121.05 has been made."
    assert texts(events) == expected
    stored = await saved(world, "alex", session_id)
    assert stored[-1].content.content == expected
    async with world.sessions() as session:
        ticket = (await session.scalars(select(models.Ticket))).one()
    assert "- lookup_order: ok\n- issue_refund: allowed" in ticket.body


async def test_every_receipt_from_the_last_rounds_is_added_in_order(world):
    item = world.item_id("BOOT-TR")
    ticket_call = call("c1", "create_ticket", subject="Zip", body="Stuck zipper")
    refund = call("c2", "issue_refund", order_item_id=item, quantity=1, reason_code="changed_mind")
    llm = ScriptedLLM([call_tools(ticket_call, refund)])
    _, events = await world.turn(llm, "two things", max_steps=1)
    message = texts(events)
    assert message.startswith(STEP_LIMIT_MESSAGE)
    assert message.index("support ticket") < message.index("refund of $121.05")
