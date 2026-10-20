from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.embeddings import Embedder


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ToolContext:
    """What a tool may rely on. The customer comes from the authenticated session, never from
    the model. Tools run inside the caller's transaction and never commit."""

    session: AsyncSession
    customer_id: int
    clock: Callable[[], datetime] = field(default=utc_now)
    # Only search_knowledge uses it; the other tools never embed anything
    embedder: Embedder | None = None
