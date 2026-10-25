from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    database_url: str = "postgresql+psycopg://support:support@localhost:5433/support"
    openai_api_key: str = ""
    agent_model: str = "gpt-6-luna"
    judge_model: str = "gpt-6-luna"
    # How hard the agent model thinks before answering: none, low, medium or high
    agent_reasoning_effort: str = "medium"
    embedding_model: str = "text-embedding-3-small"
    refund_approval_threshold: int = 200
    # Model calls in one turn before the agent gives up and hands the chat to a person
    agent_max_steps: int = 6
    # A turn does not start another model call after this many seconds; it hands over instead
    agent_turn_deadline_seconds: float = 120
    # A model call fails if the next piece of its reply takes longer than this
    model_timeout_seconds: float = 60
    # A failed model call that had shown the customer nothing is tried again this many times
    model_retries: int = 1
    model_retry_delay_seconds: float = 1.0
    # Reorder the search candidates with a local cross-encoder (needs the "rerank" extra)
    rerank_enabled: bool = False
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    # search_knowledge flags an answer as shaky when no passage is at least this similar
    low_confidence_score: float = 0.45

    # Tracing to Langfuse. Both keys empty means no tracing. The base URL is the one for your
    # project's region, such as https://us.cloud.langfuse.com; empty uses the SDK's default
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""

    # The default secret is for local development only; set JWT_SECRET anywhere else
    jwt_secret: str = "dev-only-secret-change-me-before-deploying"
    token_ttl_minutes: int = 480
    # Browser origins allowed to call the API, separated by commas
    cors_origins: str = "http://localhost:3000"
    # Every seeded demo customer logs in with this
    seed_password: str = "demo"

    # Where the agent runs its tool calls: "local" in this process, or "mcp" through the MCP tool
    # server at MCP_SERVER_URL (start it with `make mcp`)
    tool_backend: Literal["local", "mcp"] = "local"
    # The MCP tool server (python -m app.mcp_server). The client uses the URL; the server the rest.
    # It listens on this machine only unless MCP_HOST says otherwise, and then it will not start
    # on the default JWT secret
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8765
    mcp_server_url: str = "http://localhost:8765/mcp"
    # The most one tool call over MCP may take, connecting included. A call that runs out of it
    # after being sent is reported as unknown, not as failed
    mcp_call_timeout_seconds: float = Field(default=30, gt=0)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
