"""An ``McpBackend`` that reaches the real MCP app without a socket.

The app has to be started for each call and in the task that makes it (the SDK's session
manager starts once per app, and a fixture's task is not the test's), so each call gets a fresh
app, started and stopped around the backend's own ``async with``.
"""

from contextlib import AsyncExitStack

import httpx2

from app.agent.backends import McpBackend
from app.mcp_server.auth import make_authenticate
from app.mcp_server.server import MCP_PATH, create_app

# A port is needed: on localhost the app only accepts a Host header that has one
BASE = "http://localhost:8765"
URL = f"{BASE}{MCP_PATH}"


class _AppClient(httpx2.AsyncClient):
    """An HTTP client to an app that it starts on entry and stops on exit."""

    def __init__(self, app, headers):
        super().__init__(transport=httpx2.ASGITransport(app=app), base_url=BASE, headers=headers)
        self._app = app
        self._running = AsyncExitStack()

    async def __aenter__(self):
        await self._running.enter_async_context(self._app.router.lifespan_context(self._app))
        return await super().__aenter__()

    async def __aexit__(self, *exc):
        try:
            return await super().__aexit__(*exc)
        finally:
            await self._running.aclose()


def mcp_backend(sessions, *, embedder=None, clock) -> McpBackend:
    def client(headers: dict[str, str]) -> httpx2.AsyncClient:
        app = create_app(
            sessions, make_authenticate(sessions), embedder=embedder, clock=clock, host="localhost"
        )
        return _AppClient(app, headers)

    return McpBackend(URL, http_client=client)
