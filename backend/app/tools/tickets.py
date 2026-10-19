from app import models
from app.tools.context import ToolContext
from app.tools.results import TicketResult

MAX_SUBJECT = 200
MAX_BODY = 4000


async def open_ticket(ctx: ToolContext, kind: str, subject: str, body: str) -> models.Ticket:
    """Write a ticket for the session's customer. The caller has already checked the text."""
    ticket = models.Ticket(
        customer_id=ctx.customer_id, kind=kind, subject=subject, body=body, created_at=ctx.clock()
    )
    ctx.session.add(ticket)
    await ctx.session.flush()
    return ticket


def _invalid(message: str) -> TicketResult:
    return TicketResult(status="invalid_request", message=message)


async def create_ticket(ctx: ToolContext, subject: str, body: str) -> TicketResult:
    """Open a support ticket for the session's customer."""
    subject, body = subject.strip(), body.strip()
    if not subject or len(subject) > MAX_SUBJECT:
        return _invalid(f"The subject must be 1 to {MAX_SUBJECT} characters.")
    if not body or len(body) > MAX_BODY:
        return _invalid(f"The body must be 1 to {MAX_BODY} characters.")
    ticket = await open_ticket(ctx, "support", subject, body)
    return TicketResult(status="ok", ticket_id=ticket.id)


async def escalate(ctx: ToolContext, reason: str) -> TicketResult:
    """Hand the conversation to a person, for conflicting sources or no answer."""
    reason = reason.strip()
    if not reason or len(reason) > MAX_BODY:
        return _invalid(f"The reason must be 1 to {MAX_BODY} characters.")
    ticket = await open_ticket(ctx, "escalation", "Escalation from the support agent", reason)
    return TicketResult(status="ok", ticket_id=ticket.id)
