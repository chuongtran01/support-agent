import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent import store
from app.agent.llm import LLMClient
from app.agent.loop import AgentDeps
from app.agent.runner import TurnInProgress, TurnRunner
from app.api.deps import current_customer, get_embedder, get_llm, get_sessionmaker, get_turns
from app.api.sse import frames
from app.auth import CurrentCustomer
from app.retrieval.embeddings import Embedder

MAX_MESSAGE_CHARACTERS = 2000
MAX_SESSION_MESSAGES = 40
PREVIEW_CHARACTERS = 80

router = APIRouter()

Customer = Annotated[CurrentCustomer, Depends(current_customer)]
Sessions = Annotated[async_sessionmaker, Depends(get_sessionmaker)]


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    preview: str | None = None


class CitationOut(BaseModel):
    document_id: str
    title: str
    heading_path: str


class MessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    text: str
    created_at: datetime
    citations: list[CitationOut] = []
    dropped_citations: int = 0


class SendMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=MAX_MESSAGE_CHARACTERS)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("The message is empty.")
        return text


def _summary(summary: store.SessionSummary) -> SessionOut:
    preview = summary.preview
    if preview and len(preview) > PREVIEW_CHARACTERS:
        preview = preview[: PREVIEW_CHARACTERS - 1] + "…"
    return SessionOut(id=summary.id, created_at=summary.created_at, preview=preview)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, {"code": code, "message": message})


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(customer: Customer, sessions: Sessions) -> SessionOut:
    return _summary(await store.create_session(sessions, customer.id))


@router.get("/sessions")
async def list_sessions(customer: Customer, sessions: Sessions) -> list[SessionOut]:
    return [_summary(s) for s in await store.list_sessions(sessions, customer.id)]


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: uuid.UUID, customer: Customer, sessions: Sessions
) -> list[MessageOut]:
    try:
        shown = await store.load_display(sessions, session_id, customer.id)
    except store.SessionNotFound:
        raise _not_found() from None
    return [
        MessageOut(
            id=m.id,
            role=m.role,
            text=m.text,
            created_at=m.created_at,
            citations=[CitationOut(**vars(c)) for c in m.citations],
            dropped_citations=m.dropped_citations,
        )
        for m in shown
    ]


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: uuid.UUID,
    body: SendMessage,
    customer: Customer,
    sessions: Sessions,
    llm: Annotated[LLMClient, Depends(get_llm)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    turns: Annotated[TurnRunner, Depends(get_turns)],
) -> StreamingResponse:
    """Send a message and stream the turn back as server-sent events."""
    try:
        held = await store.count_messages(sessions, session_id, customer.id)
    except store.SessionNotFound:
        raise _not_found() from None
    if held >= MAX_SESSION_MESSAGES:
        raise _conflict("chat_full", "This chat is full. Please start a new chat.")
    try:
        queue = turns.start(AgentDeps(sessions, llm, embedder), customer, session_id, body.text)
    except TurnInProgress:
        raise _conflict("turn_in_progress", "Wait for the answer before sending another.") from None
    return StreamingResponse(
        frames(queue),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
