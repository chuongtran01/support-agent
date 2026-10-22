from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    database_url: str = "postgresql+psycopg://support:support@localhost:5433/support"
    openai_api_key: str = ""
    agent_model: str = "gpt-6-luna"
    judge_model: str = "gpt-6-luna"
    embedding_model: str = "text-embedding-3-small"
    refund_approval_threshold: int = 200
    # Model calls in one turn before the agent gives up and hands the chat to a person
    agent_max_steps: int = 6
    # search_knowledge flags an answer as shaky when no passage is at least this similar
    low_confidence_score: float = 0.45

    # The default secret is for local development only; set JWT_SECRET anywhere else
    jwt_secret: str = "dev-only-secret-change-me-before-deploying"
    token_ttl_minutes: int = 480
    # Browser origins allowed to call the API, separated by commas
    cors_origins: str = "http://localhost:3000"
    # Every seeded demo customer logs in with this
    seed_password: str = "demo"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
