"""The chat endpoint's tool backend follows ``TOOL_BACKEND``, and a turn uses what it is given."""

import json

from app.agent.backends import LocalBackend, McpBackend
from app.agent.llm import ToolCall
from app.api.deps import get_tool_backend
from app.config import settings
from app.main import app
from app.tools.registry import ToolOutput
from tests.api_world import FakeEmbedder, api  # noqa: F401  (fixture)
from tests.scripted_llm import ScriptedLLM, call_tools, say


class RecordingBackend:
    def __init__(self):
        self.calls: list[tuple[int, ToolCall]] = []

    async def run(self, customer, call):
        self.calls.append((customer.id, call))
        return ToolOutput(json.dumps({"status": "ok"}), "ok")


def test_local_gives_the_in_process_backend_with_the_endpoints_sessions_and_embedder():
    sessions, embedder = object(), FakeEmbedder()

    backend = get_tool_backend(sessions, embedder)

    assert isinstance(backend, LocalBackend)
    assert (backend.sessions, backend.embedder) == (sessions, embedder)


def test_mcp_gives_a_backend_that_calls_the_configured_server(monkeypatch):
    monkeypatch.setattr(settings, "tool_backend", "mcp")
    monkeypatch.setattr(settings, "mcp_server_url", "http://tools.internal:9000/mcp")

    backend = get_tool_backend(object(), FakeEmbedder())

    assert isinstance(backend, McpBackend)
    assert backend.url == "http://tools.internal:9000/mcp"


def test_the_tests_run_on_local_whatever_the_dotenv_says():
    assert settings.tool_backend == "local"


async def test_a_chat_turn_runs_its_tool_calls_on_the_endpoints_backend(api):  # noqa: F811
    backend = RecordingBackend()
    app.dependency_overrides[get_tool_backend] = lambda: backend
    call = ToolCall("call-1", "list_orders", "{}")
    api.llm = ScriptedLLM([call_tools(call), say("You have orders.")])
    chat = await api.new_chat("priya")

    response = await api.send(chat, "What did I order?", "priya")

    assert response.status_code == 200
    assert backend.calls == [(api.customer_id("priya"), call)]
