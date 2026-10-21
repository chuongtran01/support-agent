"""The tools the agent can call: name, arguments, handler, and one safe way to run them.

The argument models use ``extra="forbid"``, so a model that invents a ``customer_id`` or an
``amount`` gets an error back instead of a refund. The customer always comes from the
``ToolContext``. ``decide_refund`` is not here on purpose: the agent cannot call it.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.policy.engine import ReasonCode
from app.tools.context import ToolContext
from app.tools.knowledge import MAX_QUERY, search_knowledge
from app.tools.orders import lookup_order
from app.tools.refunds import issue_refund
from app.tools.results import Result
from app.tools.tickets import create_ticket, escalate

logger = logging.getLogger(__name__)


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchKnowledgeArguments(Arguments):
    query: str = Field(
        min_length=1,
        max_length=MAX_QUERY,
        description="What to look up, in plain words. Include the product or topic.",
    )


class LookupOrderArguments(Arguments):
    order_id: int = Field(description="The order number the customer gave.")


class IssueRefundArguments(Arguments):
    order_item_id: int = Field(description="The order_item_id of the item, from lookup_order.")
    quantity: int = Field(ge=1, description="How many units to refund.")
    reason_code: ReasonCode = Field(description="Why the customer wants the refund.")


class CreateTicketArguments(Arguments):
    subject: str = Field(description="A short summary of the problem.")
    body: str = Field(description="What the customer needs, with the details a person will need.")


class EscalateArguments(Arguments):
    reason: str = Field(description="Why a person needs to take over this conversation.")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: type[Arguments]
    handler: Callable[..., Awaitable[Result]]


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "search_knowledge",
            "Search the help articles. Use it for any question about policies, shipping, "
            "returns, warranty, membership or how things work, and answer only from what it "
            "returns. Each passage carries its document_id, status and date.",
            SearchKnowledgeArguments,
            search_knowledge,
        ),
        ToolSpec(
            "lookup_order",
            "Look up one of the customer's orders: its items, delivery date, and any refunds "
            "already made. Call this before issuing a refund to get the order_item_id.",
            LookupOrderArguments,
            lookup_order,
        ),
        ToolSpec(
            "issue_refund",
            "Ask for a refund of an item the customer bought. Company policy decides the "
            "outcome and the amount; read the status and message in the result and tell the "
            "customer exactly that. Never promise a refund before the result says it was made.",
            IssueRefundArguments,
            issue_refund,
        ),
        ToolSpec(
            "create_ticket",
            "Open a support ticket for a problem you cannot solve yourself.",
            CreateTicketArguments,
            create_ticket,
        ),
        ToolSpec(
            "escalate",
            "Hand the conversation to a person, for example when sources conflict or the "
            "knowledge base has no answer.",
            EscalateArguments,
            escalate,
        ),
    )
}


def _clean(schema: Any) -> Any:
    """Drop the ``title`` keys Pydantic adds; they only cost the model tokens."""
    if isinstance(schema, dict):
        return {k: _clean(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_clean(v) for v in schema]
    return schema


def tool_schemas(tools: dict[str, ToolSpec] = TOOLS) -> list[dict[str, Any]]:
    """The tool list in the shape OpenAI function calling expects."""
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": _clean(spec.arguments.model_json_schema()),
            },
        }
        for spec in tools.values()
    ]


@dataclass(frozen=True)
class ToolOutput:
    """One finished tool call: the JSON for the model, the result's status, and the receipt
    for the customer if the call changed something."""

    text: str
    status: str
    receipt: str | None = None


def _error(status: str, message: str) -> ToolOutput:
    return ToolOutput(json.dumps({"status": status, "message": message}), status)


def _describe(error: ValidationError) -> str:
    problems = [
        f"{'.'.join(str(part) for part in e['loc']) or 'arguments'}: {e['msg']}"
        for e in error.errors()
    ]
    return "The arguments are not valid. " + "; ".join(problems) + "."


async def run_tool(
    ctx: ToolContext,
    name: str,
    raw_arguments: str,
    tools: dict[str, ToolSpec] = TOOLS,
) -> ToolOutput:
    """Run one tool call and return its output.

    Each call gets its own transaction, committed when the tool returns and rolled back if
    it raises, so a tool that fails halfway writes nothing. The session must not already be
    in a transaction. Bad calls (unknown tool, malformed JSON, invalid arguments) come back
    as ``invalid_request`` without touching the database, so the model can try again.
    """
    spec = tools.get(name)
    if spec is None:
        return _error("invalid_request", f"There is no tool called {name}.")
    try:
        arguments = spec.arguments.model_validate_json(raw_arguments or "{}")
    except ValidationError as error:
        return _error("invalid_request", _describe(error))

    try:
        async with ctx.session.begin():
            result = await spec.handler(ctx, **arguments.model_dump())
    except Exception:
        logger.exception("tool %s failed", name)
        return _error("error", "The tool failed and nothing was changed. Tell the customer.")
    return ToolOutput(result.model_dump_json(exclude_none=True), result.status, result.receipt())


async def dispatch(
    ctx: ToolContext,
    name: str,
    raw_arguments: str,
    tools: dict[str, ToolSpec] = TOOLS,
) -> str:
    """Run one tool call and return only the JSON text for the model."""
    return (await run_tool(ctx, name, raw_arguments, tools)).text
