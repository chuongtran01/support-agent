"""The whole API over the test database, with the model and embedder replaced.

Import the ``api`` fixture into a test module with ``from tests.api_world import api``.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.runner import TurnRunner
from app.api.deps import get_embedder, get_llm, get_sessionmaker, get_tracer, get_turns
from app.auth import create_token
from app.db import get_session
from app.main import app
from app.seed import seed
from tests.recording_tracer import RecordingTracer
from tests.scripted_llm import ScriptedLLM

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


class FakeEmbedder:
    async def embed(self, texts):
        return [[0.0] for _ in texts]


class Api:
    def __init__(self, client, sessions, seeded, turns):
        self.client = client
        self.sessions = sessions
        self.seeded = seeded
        self.turns = turns
        self.llm = ScriptedLLM([])
        self.tracer = RecordingTracer()

    def headers(self, customer: str) -> dict[str, str]:
        token = create_token(self.seeded.customers[customer].id)
        return {"Authorization": f"Bearer {token}"}

    def customer_id(self, customer: str) -> int:
        return self.seeded.customers[customer].id

    async def new_chat(self, customer="alex") -> str:
        response = await self.client.post("/sessions", headers=self.headers(customer))
        assert response.status_code == 201
        return response.json()["id"]

    async def send(self, session_id, text, customer="alex"):
        return await self.client.post(
            f"/sessions/{session_id}/messages", json={"text": text}, headers=self.headers(customer)
        )


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """The ``(event, data)`` pairs of a server-sent event stream."""
    events = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        name, data = frame.split("\n")
        assert name.startswith("event: ") and data.startswith("data: ")
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


@pytest_asyncio.fixture
async def api(session, test_database_url):
    async with session.begin():
        seeded = await seed(session, NOW, "demo")
    engine = create_async_engine(test_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    turns = TurnRunner()

    async def request_session():
        async with sessions() as request:
            yield request

    holder: dict[str, Api] = {}
    app.dependency_overrides[get_session] = request_session
    app.dependency_overrides[get_sessionmaker] = lambda: sessions
    app.dependency_overrides[get_llm] = lambda: holder["api"].llm
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    app.dependency_overrides[get_turns] = lambda: turns
    app.dependency_overrides[get_tracer] = lambda: holder["api"].tracer

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        holder["api"] = Api(client, sessions, seeded, turns)
        yield holder["api"]
    await turns.drain()
    app.dependency_overrides.clear()
    await engine.dispose()
