"""What the endpoints need from outside the request, as dependencies so tests can swap them."""

from functools import cache

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent.llm import LLMClient, OpenAIClient
from app.api.streaming import TurnRunner
from app.db import SessionLocal
from app.retrieval.embeddings import Embedder, OpenAIEmbedder

_runner = TurnRunner()


def get_sessionmaker() -> async_sessionmaker:
    """A turn outlives the request that started it, so it opens its own sessions."""
    return SessionLocal


@cache
def get_llm() -> LLMClient:
    return OpenAIClient()


@cache
def get_embedder() -> Embedder:
    return OpenAIEmbedder()


def get_turns() -> TurnRunner:
    return _runner
