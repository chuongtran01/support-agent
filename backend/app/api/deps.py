"""What the endpoints need from outside the request, as dependencies so tests can swap them.

This is where the caller is identified too: ``current_customer`` reads the bearer token and
``require_approver`` checks the role.
"""

from functools import cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import models
from app.agent.llm import LLMClient
from app.agent.openai_client import OpenAIClient
from app.agent.runner import TurnRunner
from app.auth import CurrentCustomer, read_token, to_current
from app.db import SessionLocal, get_session
from app.retrieval.embeddings import Embedder, OpenAIEmbedder
from app.tracing import Tracer
from app.tracing import get_tracer as _get_tracer

_runner = TurnRunner()
_bearer = HTTPBearer(auto_error=False)


def get_sessionmaker() -> async_sessionmaker:
    """A turn outlives the request that started it, so it opens its own sessions."""
    return SessionLocal


@cache
def get_llm() -> LLMClient:
    return OpenAIClient()


@cache
def get_embedder() -> Embedder:
    return OpenAIEmbedder()


def get_tracer() -> Tracer:
    return _get_tracer()


def get_turns() -> TurnRunner:
    return _runner


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_customer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentCustomer:
    """The customer a request is for, from its bearer token. Any problem is the same 401."""
    if credentials is None:
        raise _unauthorized()
    customer_id = read_token(credentials.credentials)
    if customer_id is None:
        raise _unauthorized()
    row = await session.get(models.Customer, customer_id)
    if row is None:
        raise _unauthorized()
    customer = to_current(row)
    # End the read transaction now: the tools open their own, and a session that is already
    # in one cannot start another
    await session.rollback()
    return customer


async def require_approver(
    customer: Annotated[CurrentCustomer, Depends(current_customer)],
) -> CurrentCustomer:
    if customer.role != "approver":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Approver access required")
    return customer
