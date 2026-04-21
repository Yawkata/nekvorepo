from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "identity-service"
    API_V1_STR: str = "/v1"

    # AWS / Cognito
    AWS_REGION: str = "us-east-1"
    COGNITO_USER_POOL_ID: str
    COGNITO_CLIENT_ID: str
    COGNITO_CLIENT_SECRET: str

    # Database
    DATABASE_URL: str

    # Internal Passport JWT — no default; service refuses to start without it
    PASSPORT_SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # CORS — empty list = no CORS (secure default). Set per-environment.
    # Example: CORS_ORIGINS=["https://app.example.com"]
    CORS_ORIGINS: list[str] = []

    # SES — sender address for invite / membership emails.  Empty = no emails sent.
    SES_FROM_EMAIL: str = ""

    # Downstream services — identity-service calls these on role change and removal.
    WORKFLOW_SERVICE_URL: str = "http://workflow-service:8000"
    REPO_SERVICE_URL: str = "http://repo-service:8000"

    # SQS cache invalidation queue. Empty = no-op (local dev / CI).
    SQS_CACHE_INVALIDATION_QUEUE_URL: str = ""

    # Frontend base URL used to build invite accept links in emails.
    INVITE_ACCEPT_BASE_URL: str = "http://localhost:3000"

    @field_validator("PASSPORT_SECRET_KEY")
    @classmethod
    def _secret_must_be_strong(cls, v: str) -> str:
        if v in ("", "change-me-in-production"):
            raise ValueError(
                "PASSPORT_SECRET_KEY must be a strong random secret. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if len(v) < 32:
            raise ValueError(
                "PASSPORT_SECRET_KEY must be at least 32 characters long."
            )
        return v

    model_config = SettingsConfigDict(case_sensitive=True)


settings = Settings()
