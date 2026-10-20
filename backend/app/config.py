from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    database_url: str = "postgresql+psycopg://support:support@localhost:5433/support"
    openai_api_key: str = ""
    agent_model: str = "gpt-5"
    judge_model: str = "gpt-5-mini"
    embedding_model: str = "text-embedding-3-small"
    refund_approval_threshold: int = 200

    # The default secret is for local development only; set JWT_SECRET anywhere else
    jwt_secret: str = "dev-only-secret-change-me-before-deploying"
    token_ttl_minutes: int = 480
    # Every seeded demo customer logs in with this
    seed_password: str = "demo"


settings = Settings()
