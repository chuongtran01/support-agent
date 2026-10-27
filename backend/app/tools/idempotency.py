"""Run a write once per key.

A client that lost the answer to a call sends it again with the same key. The first call stores
what it answered; a repeat gets that back and changes nothing. The key belongs to one customer
and to one request: the same key with another tool or other arguments is refused.
"""

import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import models

MAX_KEY_LENGTH = 200


def valid_key(key: str) -> bool:
    return 0 < len(key) <= MAX_KEY_LENGTH


def fingerprint(tool: str, arguments: BaseModel) -> str:
    """The same for the same tool and arguments, whatever order the model wrote them in."""
    request = json.dumps({"tool": tool, "arguments": arguments.model_dump()}, sort_keys=True)
    return hashlib.sha256(request.encode()).hexdigest()


@dataclass(frozen=True)
class Claim:
    """Who holds a key. ``row`` is the claim this call just made, or the earlier one it found."""

    row: models.IdempotencyKey
    first: bool


async def claim(
    session: AsyncSession, customer_id: int, key: str, tool: str, request: str
) -> Claim:
    """Take the key, or find who holds it. Call inside the tool's transaction.

    A second call for a key a first call is still working on waits here until that call commits
    (then it finds the stored answer) or rolls back (then it becomes the first call).
    """
    made = await session.scalar(
        insert(models.IdempotencyKey)
        .values(
            customer_id=customer_id, key=key, tool=tool, fingerprint=request, output="", status=""
        )
        .on_conflict_do_nothing(constraint="idempotency_keys_customer_id_key_key")
        .returning(models.IdempotencyKey.id)
    )
    row = await session.scalar(
        select(models.IdempotencyKey)
        .where(models.IdempotencyKey.customer_id == customer_id, models.IdempotencyKey.key == key)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return Claim(row, first=made is not None)


def store(row: models.IdempotencyKey, output: str, status: str, receipt: str | None) -> None:
    """Keep the first call's answer on its claim. Committed with the call's own changes."""
    row.output = output
    row.status = status
    row.receipt = receipt
