"""Who is calling the MCP server: the bearer token on every request.

The token is the one the API issues at login, read the same way: ``read_token`` gives the
customer id and the customer is loaded from the database, so a deleted customer or a changed
role takes effect on the next call. Every problem is the same refusal.
"""

from collections.abc import Awaitable, Callable, Mapping

from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app import models
from app.agent import store
from app.auth import CurrentCustomer, read_token, to_current

# Gets the request's headers (None when the transport has none) and returns the customer they
# stand for, or None. The server never learns the customer from a tool argument.
Authenticate = Callable[[Mapping[str, str] | None], Awaitable[CurrentCustomer | None]]


def bearer_token(headers: Mapping[str, str] | None) -> str | None:
    """The token in an ``Authorization: Bearer <token>`` header, or None for anything else."""
    if headers is None:
        return None
    value = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
    scheme, _, token = value.partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None


def make_authenticate(sessions: store.Sessions) -> Authenticate:
    async def authenticate(headers: Mapping[str, str] | None) -> CurrentCustomer | None:
        token = bearer_token(headers)
        customer_id = read_token(token) if token else None
        if customer_id is None:
            return None
        async with sessions() as session:
            row = await session.get(models.Customer, customer_id)
        return to_current(row) if row else None

    return authenticate


class RequireCustomer:
    """Turn away a request that has no valid token with a 401, before the MCP app sees it.

    Each call is its own HTTP request, so a token that expires mid-session stops working at
    once. Anything that is not an HTTP request (the app's start-up and shutdown) goes through.
    """

    def __init__(self, app: ASGIApp, authenticate: Authenticate):
        self.app = app
        self.authenticate = authenticate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and await self.authenticate(Request(scope).headers) is None:
            refusal = PlainTextResponse(
                "Not authenticated", 401, headers={"WWW-Authenticate": "Bearer"}
            )
            await refusal(scope, receive, send)
            return
        await self.app(scope, receive, send)
