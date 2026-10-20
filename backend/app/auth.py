"""Who is calling: password hashing, signed tokens and the dependencies that check them.

A token holds only the customer id and an expiry. The customer's role and membership are
read from the database on every request, so demoting an approver takes effect at once.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.config import settings
from app.db import get_session

ALGORITHM = "HS256"

_hasher = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)


class CurrentCustomer(BaseModel):
    """The logged-in customer as plain data, so nothing depends on an open database session."""

    model_config = ConfigDict(frozen=True)

    id: int
    email: str
    name: str
    role: str
    membership: str


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@cache
def _decoy_hash() -> str:
    return hash_password("decoy")


def create_token(customer_id: int, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(minutes=settings.token_ttl_minutes)
    claims = {"sub": str(customer_id), "iat": issued, "exp": expires}
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def read_token(token: str) -> int | None:
    """The customer id in a valid, unexpired token, or None for anything else."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
        return int(claims["sub"])
    except (jwt.PyJWTError, ValueError):
        return None


def _to_current(row: models.Customer) -> CurrentCustomer:
    return CurrentCustomer(
        id=row.id, email=row.email, name=row.name, role=row.role, membership=row.membership
    )


async def authenticate(session: AsyncSession, email: str, password: str) -> CurrentCustomer | None:
    """The customer for this email and password, or None. An unknown email does the same
    amount of hashing work as a wrong password, so the two cannot be told apart by timing.
    """
    row = await session.scalar(
        select(models.Customer).where(func.lower(models.Customer.email) == email.strip().lower())
    )
    password_hash = row.password_hash if row else _decoy_hash()
    # Hashing takes tens of milliseconds of CPU; keep it off the event loop
    matches = await asyncio.to_thread(verify_password, password, password_hash)
    if row is None or not matches:
        return None
    return _to_current(row)


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
    customer = _to_current(row)
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
