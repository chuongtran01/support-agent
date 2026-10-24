"""The Langfuse tracer, against a fake client: what it sends, and that a failing client never
reaches the turn. Nothing here needs Langfuse or the network."""

import logging

import pytest

from app import tracing
from app.agent.llm import ToolCall, Usage
from app.agent.messages import UserContent
from app.tracing import LangfuseTracer, Tracer, get_tracer


class FakeObservation:
    """Stands in for a Langfuse span or generation: records what is done to it."""

    def __init__(self, client, kind, name, kwargs):
        self.client, self.kind, self.name, self.kwargs = client, kind, name, kwargs
        self.updates: list[dict] = []
        self.ended = False
        self.children: list[FakeObservation] = []

    def start_observation(self, *, name, as_type="span", **kwargs):
        child = FakeObservation(self.client, as_type, name, kwargs)
        self.children.append(child)
        return child

    def update(self, **kwargs):
        if self.client.broken:
            raise ConnectionError("langfuse is down")
        self.updates.append(kwargs)

    def end(self):
        self.ended = True

    @property
    def merged(self) -> dict:
        merged = dict(self.kwargs)
        for update in self.updates:
            merged.update(update)
        return merged


class FakeClient:
    def __init__(self, broken=False):
        self.broken = broken
        self.roots: list[FakeObservation] = []
        self.flushed = 0

    def start_observation(self, *, name, as_type="span", **kwargs):
        if self.broken:
            raise ConnectionError("langfuse is down")
        root = FakeObservation(self, as_type, name, kwargs)
        self.roots.append(root)
        return root

    def flush(self):
        self.flushed += 1
        if self.broken:
            raise ConnectionError("langfuse is down")


@pytest.fixture(autouse=True)
def fresh_warning():
    tracing._warned = False


def start(client, **kwargs):
    return LangfuseTracer(client, **kwargs).start_turn(
        customer_id=7, session_id="chat-1", text="where is my order?"
    )


def test_a_turn_becomes_a_root_with_a_generation_and_a_tool_span_beneath_it():
    client = FakeClient()
    trace = start(client)
    model = trace.model_call(1, "system text", [UserContent(content="hi")])
    model.first_output()
    model.finish(
        text="",
        tool_calls=[ToolCall("c1", "lookup_order", '{"order_id": 3}')],
        usage=Usage(120, 15),
        model="m-1",
    )
    tool = trace.tool_call("lookup_order", '{"order_id": 3}')
    tool.finish(status="ok", output='{"status": "ok"}')
    trace.finish("answered", "It shipped.")

    (root,) = client.roots
    assert root.kind == "agent" and root.kwargs["input"] == "where is my order?"
    assert root.merged["output"] == "It shipped." and root.ended
    generation, span = root.children
    assert generation.kind == "generation" and generation.ended
    assert generation.kwargs["input"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "hi"},
    ]
    assert generation.merged["usage_details"] == {"input": 120, "output": 15}
    assert generation.merged["model"] == "m-1"
    assert generation.merged["output"]["tool_calls"] == [
        {"name": "lookup_order", "arguments": {"order_id": 3}}
    ]
    assert "completion_start_time" in generation.merged
    assert span.kind == "tool" and span.kwargs["input"] == {"order_id": 3}
    assert span.merged["output"] == {"status": "ok"} and span.ended


def test_the_first_output_time_is_set_once():
    model = start(FakeClient()).model_call(1, "s", [])
    model.first_output()
    model.first_output()
    assert sum("completion_start_time" in u for u in model._generation.updates) == 1


@pytest.mark.parametrize(
    ("status", "level"),
    [
        ("ok", "DEFAULT"),
        ("allowed", "DEFAULT"),  # a refund that was made is not a problem
        ("needs_approval", "DEFAULT"),
        ("no_results", "DEFAULT"),
        ("denied", "WARNING"),
        ("not_found", "WARNING"),
        ("invalid_request", "WARNING"),
        ("manual_review", "WARNING"),
        ("error", "ERROR"),
    ],
)
def test_a_tool_span_is_marked_only_when_its_status_is_a_problem(status, level):
    client = FakeClient()
    start(client).tool_call("issue_refund", "{}").finish(status=status, output="{}")
    assert client.roots[0].children[0].merged["level"] == level


def test_a_failed_turn_is_an_error():
    client = FakeClient()
    start(client).finish("model_error")
    assert client.roots[0].merged["level"] == "ERROR"


def test_a_failed_model_call_is_an_error_observation():
    client = FakeClient()
    start(client).model_call(1, "s", []).fail("rate limited")
    generation = client.roots[0].children[0]
    assert generation.merged["level"] == "ERROR" and generation.ended


def test_finishing_a_turn_twice_keeps_the_first_outcome():
    client = FakeClient()
    trace = start(client)
    trace.finish("answered", "first")
    trace.finish("interrupted")
    assert client.roots[0].merged["output"] == "first"
    assert len(client.roots[0].updates) == 1


def test_tagged_adds_metadata_and_tags_without_changing_the_original():
    base = LangfuseTracer(FakeClient())
    tagged = base.tagged({"case": "a001"}, ["eval"])
    assert tagged._metadata == {"case": "a001"} and tagged._tags == ["eval"]
    assert base._metadata == {} and base._tags == []
    assert tagged.tagged({"run": "2"})._metadata == {"case": "a001", "run": "2"}


def test_a_client_that_fails_never_reaches_the_turn(caplog):
    client = FakeClient(broken=True)
    with caplog.at_level(logging.WARNING):
        trace = start(client)  # the root cannot be made
        model = trace.model_call(1, "s", [])
        model.first_output()
        model.finish(text="hi", tool_calls=[], usage=Usage(1, 1), model=None)
        trace.tool_call("t", "{}").finish(status="ok", output="{}")
        trace.finish("answered", "hi")
        LangfuseTracer(client).flush()
    assert len([r for r in caplog.records if "tracing failed" in r.message]) == 1  # once


def test_a_client_that_fails_after_the_root_is_made_never_reaches_the_turn():
    client = FakeClient()
    trace = start(client)
    client.broken = True
    model = trace.model_call(1, "s", [])
    model.first_output()
    model.finish(text="hi", tool_calls=[], usage=Usage(1, 1), model=None)
    trace.finish("answered", "hi")  # no exception


def test_the_base_tracer_does_nothing_and_returns_itself_when_tagged():
    tracer = Tracer()
    trace = tracer.start_turn(customer_id=1, session_id="s", text="t")
    trace.model_call(1, "s", []).finish(text="", tool_calls=[], usage=Usage(), model=None)
    trace.tool_call("t", "{}").finish(status="ok", output="{}")
    trace.finish("answered")
    assert tracer.tagged({"a": "b"}) is tracer
    tracer.flush()


def test_no_keys_means_no_tracing(monkeypatch):
    monkeypatch.setattr(tracing.settings, "langfuse_public_key", "pk")
    monkeypatch.setattr(tracing.settings, "langfuse_secret_key", "")
    get_tracer.cache_clear()
    assert type(get_tracer()) is Tracer


def test_both_keys_start_the_langfuse_client_with_the_host(monkeypatch):
    made = []
    monkeypatch.setattr("langfuse.Langfuse", lambda **kw: made.append(kw) or FakeClient())
    monkeypatch.setattr(tracing.settings, "langfuse_public_key", "pk")
    monkeypatch.setattr(tracing.settings, "langfuse_secret_key", "sk")
    monkeypatch.setattr(tracing.settings, "langfuse_host", "https://us.cloud.langfuse.com")
    get_tracer.cache_clear()
    assert isinstance(get_tracer(), LangfuseTracer)
    assert made == [
        {"public_key": "pk", "secret_key": "sk", "base_url": "https://us.cloud.langfuse.com"}
    ]


def test_an_empty_host_leaves_the_choice_to_the_sdk(monkeypatch):
    made = []
    monkeypatch.setattr("langfuse.Langfuse", lambda **kw: made.append(kw) or FakeClient())
    monkeypatch.setattr(tracing.settings, "langfuse_public_key", "pk")
    monkeypatch.setattr(tracing.settings, "langfuse_secret_key", "sk")
    monkeypatch.setattr(tracing.settings, "langfuse_host", "")
    get_tracer.cache_clear()
    get_tracer()
    assert made[0]["base_url"] is None


def test_a_client_that_will_not_start_leaves_tracing_off(monkeypatch):
    def refuse(**_):
        raise ValueError("bad config")

    monkeypatch.setattr("langfuse.Langfuse", refuse)
    monkeypatch.setattr(tracing.settings, "langfuse_public_key", "pk")
    monkeypatch.setattr(tracing.settings, "langfuse_secret_key", "sk")
    get_tracer.cache_clear()
    assert type(get_tracer()) is Tracer
