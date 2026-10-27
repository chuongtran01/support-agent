"""ORM models for the nine tables. Alembic migrations are generated from these."""

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIMENSIONS = 1536


class Base(DeclarativeBase):
    pass


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# Business data ---------------------------------------------------------


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("role IN ('customer', 'approver')", name="customers_role_check"),
        CheckConstraint(
            "membership IN ('none', 'basic', 'plus')", name="customers_membership_check"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, server_default="customer")
    membership: Mapped[str] = mapped_column(Text, server_default="none")
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'shipped', 'delivered', 'cancelled')",
            name="orders_status_check",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(Text)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_cents: Mapped[int] = mapped_column(Integer)


ITEM_CATEGORIES = ("gift_card", "tent", "pack", "footwear", "apparel", "consumable", "other")
TICKET_KINDS = ("support", "escalation", "manual_review")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint(_in("category", ITEM_CATEGORIES), name="order_items_category_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    sku: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    price_cents: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, server_default="1")
    is_final_sale: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    category: Mapped[str] = mapped_column(Text, server_default="other")


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'completed')",
            name="refunds_status_check",
        ),
        CheckConstraint("quantity > 0", name="refunds_quantity_check"),
        Index("refunds_customer_id_created_at_idx", "customer_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    quantity: Mapped[int] = mapped_column(Integer, server_default="1")
    amount_cents: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed')", name="tickets_status_check"),
        CheckConstraint(_in("kind", TICKET_KINDS), name="tickets_kind_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    kind: Mapped[str] = mapped_column(Text, server_default="support")
    created_at: Mapped[datetime] = _created_at()


class IdempotencyKey(Base):
    """A write call that has already run, so the same call sent again is answered, not repeated.

    The key is the client's, and unique per customer. The first call's output is kept exactly as
    it was given, so a repeat gets the same text, status and receipt for every outcome.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("customer_id", "key", name="idempotency_keys_customer_id_key_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    key: Mapped[str] = mapped_column(Text)
    tool: Mapped[str] = mapped_column(Text)
    # A hash of the tool and its validated arguments: the same key must mean the same request
    fingerprint: Mapped[str] = mapped_column(Text)
    output: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    receipt: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


# Conversations ---------------------------------------------------------


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    created_at: Mapped[datetime] = _created_at()


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'tool')", name="chat_messages_role_check"),
        Index("chat_messages_session_id_id_idx", "session_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_sessions.id"))
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[Any] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


# Knowledge base --------------------------------------------------------


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("status IN ('current', 'stale', 'draft')", name="documents_status_check"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. "returns-policy"
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="current")
    updated_at: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)  # full Markdown body, without front matter
    content_hash: Mapped[str] = mapped_column(Text)  # lets ingestion skip unchanged documents


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("chunks_tsv_idx", "tsv", postgresql_using="gin"),
        Index(
            "chunks_embedding_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    heading_path: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', heading_path || ' ' || content)", persisted=True),
    )
