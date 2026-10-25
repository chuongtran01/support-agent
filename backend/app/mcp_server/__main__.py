"""Start the MCP tool server: ``python -m app.mcp_server`` (``make mcp``).

A separate process from the API. It shares the database and ``JWT_SECRET`` with it, and has its
own connection pool and embedder.
"""

import logging

import uvicorn
from starlette.applications import Starlette

from app.config import Settings, settings
from app.db import SessionLocal
from app.mcp_server.auth import make_authenticate
from app.mcp_server.server import create_app
from app.retrieval.embeddings import OpenAIEmbedder

logger = logging.getLogger(__name__)

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_JWT_SECRET = Settings.model_fields["jwt_secret"].default


def check_secret(host: str, jwt_secret: str) -> None:
    """Refuse to listen beyond this machine with the secret that is in the source code, which
    would let anyone who has read it sign a token for any customer."""
    if jwt_secret == DEFAULT_JWT_SECRET and host not in LOCAL_HOSTS:
        raise SystemExit(
            f"Refusing to listen on {host} with the default JWT_SECRET. "
            "Set JWT_SECRET to a long random value (the API must use the same one)."
        )


def build_app() -> Starlette:
    embedder = OpenAIEmbedder() if settings.openai_api_key else None
    if embedder is None:
        logger.warning("OPENAI_API_KEY is not set: search_knowledge will fail, the rest work")
    return create_app(
        SessionLocal, make_authenticate(SessionLocal), embedder=embedder, host=settings.mcp_host
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    check_secret(settings.mcp_host, settings.jwt_secret)
    uvicorn.run(build_app(), host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()
