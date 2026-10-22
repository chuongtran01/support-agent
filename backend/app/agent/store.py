"""Chat messages in the database, and the conversion to what the model is sent.

``chat_messages.content`` holds everything about a message except its role, as the OpenAI
message would carry it: the text, an assistant's ``tool_calls``, or a tool's ``tool_call_id``.
Every write here is its own short transaction, so nothing is held open across a model call.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import models
from app.agent.citations import add_retrieved, check_citations
from app.agent.events import Citation
from app.agent.messages import (
    AssistantContent,
    MessageContent,
    ToolContent,
    UserContent,
    parse_content,
)

Sessions = async_sessionmaker

LOST_RESULT = json.dumps(
    {
        "status": "error",
        "message": "This call did not finish and its result was lost. Do not assume it "
        "worked or failed; check the order before trying again.",
    }
)


class SessionNotFound(Exception):
    """No chat session with this id belongs to this customer."""


@dataclass(frozen=True)
class StoredMessage:
    id: int
    role: str
    content: MessageContent


@dataclass(frozen=True)
class SessionSummary:
    id: uuid.UUID
    created_at: datetime
    preview: str | None = None  # the customer's first message


@dataclass(frozen=True)
class DisplayMessage:
    """A message as the customer sees it: what they said and the answers they were given."""

    id: int
    role: Literal["user", "assistant"]
    text: str
    created_at: datetime
    citations: tuple[Citation, ...] = ()
    dropped_citations: int = 0


async def _require_owned(session: AsyncSession, session_id: uuid.UUID, customer_id: int) -> None:
    owned = await session.scalar(
        select(models.ChatSession.id).where(
            models.ChatSession.id == session_id, models.ChatSession.customer_id == customer_id
        )
    )
    if owned is None:
        raise SessionNotFound(str(session_id))


async def create_session(sessions: Sessions, customer_id: int) -> SessionSummary:
    async with sessions.begin() as session:
        chat = models.ChatSession(customer_id=customer_id)
        session.add(chat)
        await session.flush()
        await session.refresh(chat)  # the database sets created_at
        return SessionSummary(chat.id, chat.created_at)


async def list_sessions(
    sessions: Sessions, customer_id: int, limit: int = 50
) -> list[SessionSummary]:
    """The customer's chats, newest first."""
    first_question = (
        select(models.ChatMessage.content["content"].astext)
        .where(
            models.ChatMessage.session_id == models.ChatSession.id,
            models.ChatMessage.role == "user",
        )
        .order_by(models.ChatMessage.id)
        .limit(1)
        .scalar_subquery()
    )
    async with sessions() as session:
        rows = await session.execute(
            select(models.ChatSession.id, models.ChatSession.created_at, first_question)
            .where(models.ChatSession.customer_id == customer_id)
            .order_by(models.ChatSession.created_at.desc(), models.ChatSession.id)
            .limit(limit)
        )
        return [SessionSummary(row[0], row[1], row[2]) for row in rows]


async def count_messages(sessions: Sessions, session_id: uuid.UUID, customer_id: int) -> int:
    """How many messages a session holds. Another customer's session is not found."""
    async with sessions() as session:
        await _require_owned(session, session_id, customer_id)
        return await session.scalar(
            select(func.count())
            .select_from(models.ChatMessage)
            .where(models.ChatMessage.session_id == session_id)
        )


async def load_display(
    sessions: Sessions, session_id: uuid.UUID, customer_id: int
) -> list[DisplayMessage]:
    """What the customer said and the answers they got, each answer with its citations.

    Tool calls and their results, and the text the model wrote before calling a tool, are
    left out. The citations are worked out again from the search results saved in the same
    turn with the check the live turn used, so they match what the customer saw streamed.
    """
    async with sessions() as session:
        await _require_owned(session, session_id, customer_id)
        rows = (
            await session.scalars(
                select(models.ChatMessage)
                .where(models.ChatMessage.session_id == session_id)
                .order_by(models.ChatMessage.id)
            )
        ).all()
    shown = []
    tool_names: dict[str, str] = {}  # tool call id -> tool name
    retrieved: dict[str, Citation] = {}  # the documents found since the customer last wrote
    for row in rows:
        content = parse_content(row.role, row.content)
        if isinstance(content, UserContent):
            retrieved = {}
            shown.append(DisplayMessage(row.id, "user", content.content, row.created_at))
        elif isinstance(content, AssistantContent):
            tool_names.update({call.id: call.function.name for call in content.tool_calls})
            if content.content and not content.tool_calls:
                citations, dropped = check_citations(content.content, retrieved)
                shown.append(
                    DisplayMessage(
                        row.id, "assistant", content.content, row.created_at, citations, dropped
                    )
                )
        elif tool_names.get(content.tool_call_id) == "search_knowledge":
            add_retrieved(retrieved, content.content)
    return shown


async def save_message(sessions: Sessions, session_id: uuid.UUID, content: MessageContent) -> int:
    async with sessions.begin() as session:
        message = models.ChatMessage(
            session_id=session_id, role=content.role, content=content.as_json()
        )
        session.add(message)
        await session.flush()
        return message.id


async def load_history(
    sessions: Sessions, session_id: uuid.UUID, customer_id: int
) -> list[StoredMessage]:
    """A session's messages, oldest first. Another customer's session is not found."""
    async with sessions() as session:
        await _require_owned(session, session_id, customer_id)
        rows = await session.scalars(
            select(models.ChatMessage)
            .where(models.ChatMessage.session_id == session_id)
            .order_by(models.ChatMessage.id)
        )
        return [StoredMessage(m.id, m.role, parse_content(m.role, m.content)) for m in rows]


def to_messages(stored: list[StoredMessage]) -> list[MessageContent]:
    """The stored messages to replay to the model, with the system prompt left to the caller.

    The model rejects a conversation where a tool call has no result. That can happen if the
    server stopped between running a tool and saving its result, so a call without a result
    gets a stand-in, and a result with no call is dropped.
    """
    messages: list[MessageContent] = []
    waiting: list[str] = []  # ids of tool calls that have no result yet

    def settle() -> None:
        for call_id in waiting:
            messages.append(ToolContent(tool_call_id=call_id, content=LOST_RESULT))
        waiting.clear()

    for message in stored:
        content = message.content
        if isinstance(content, ToolContent):
            if content.tool_call_id not in waiting:
                continue
            waiting.remove(content.tool_call_id)
        else:
            settle()
        messages.append(content)
        if isinstance(content, AssistantContent):
            waiting.extend(call.id for call in content.tool_calls)
    settle()
    return messages
