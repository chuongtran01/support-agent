"""Chat messages in the database, and the conversion to what the model is sent.

``chat_messages.content`` holds everything about a message except its role, as the OpenAI
message would carry it: the text, an assistant's ``tool_calls``, or a tool's ``tool_call_id``.
Every write here is its own short transaction, so nothing is held open across a model call.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import models
from app.agent.messages import AssistantContent, MessageContent, ToolContent, parse_content

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


async def create_session(sessions: Sessions, customer_id: int) -> uuid.UUID:
    async with sessions.begin() as session:
        chat = models.ChatSession(customer_id=customer_id)
        session.add(chat)
        await session.flush()
        return chat.id


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
        owned = await session.scalar(
            select(models.ChatSession.id).where(
                models.ChatSession.id == session_id, models.ChatSession.customer_id == customer_id
            )
        )
        if owned is None:
            raise SessionNotFound(str(session_id))
        rows = await session.scalars(
            select(models.ChatMessage)
            .where(models.ChatMessage.session_id == session_id)
            .order_by(models.ChatMessage.id)
        )
        return [StoredMessage(m.id, m.role, parse_content(m.role, m.content)) for m in rows]


def to_openai(stored: list[StoredMessage]) -> list[dict[str, Any]]:
    """The stored messages as the model expects them, with the system prompt left to the caller.

    The model rejects a conversation where a tool call has no result. That can happen if the
    server stopped between running a tool and saving its result, so a call without a result
    gets a stand-in, and a result with no call is dropped.
    """
    messages: list[dict[str, Any]] = []
    waiting: list[str] = []  # ids of tool calls that have no result yet

    def settle() -> None:
        for call_id in waiting:
            messages.append(ToolContent(tool_call_id=call_id, content=LOST_RESULT).as_openai())
        waiting.clear()

    for message in stored:
        content = message.content
        if isinstance(content, ToolContent):
            if content.tool_call_id not in waiting:
                continue
            waiting.remove(content.tool_call_id)
        else:
            settle()
        messages.append(content.as_openai())
        if isinstance(content, AssistantContent):
            waiting.extend(call.id for call in content.tool_calls)
    settle()
    return messages
