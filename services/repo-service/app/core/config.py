from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "repo-service"
    API_V1_STR: str = "/v1"

    # Deployment environment — controls Swagger/OpenAPI exposure.
    # "dev" / "staging" keep /docs open; "prod" disables them to reduce
    # attack surface (AWS Well-Architected SEC03).  Override via ENV=prod.
    ENV: str = "dev"

    # Database
    DATABASE_URL: str

    # Internal Passport JWT — no default; service refuses to start without a strong value.
    PASSPORT_SECRET_KEY: str

    # Internal service URLs (cluster-local names in production, overridable for dev)
    IDENTITY_SERVICE_URL: str = "http://identity-service:8000"

    # EFS draft storage root.
    # Local:      bind-mounted host directory (see docker-compose.yml)
    # Production: EFS CSI driver mounts this path on each EKS node
    EFS_DRAFTS_ROOT: str = "/mnt/efs/drafts"

    # Role resolution cache TTL.  Per spec, role changes propagate within 60 seconds.
    ROLE_CACHE_TTL_SECONDS: int = 60

    # S3 bucket for committed file blobs.
    # Provisioned by terraform/s3.tf as "${project_name}-repo-blobs".
    S3_REPO_BUCKET: str

    # SES sender address for commit lifecycle notifications.
    # Leave empty to disable notifications (default).
    # Must be a SES-verified address or domain to send in production.
    SES_FROM_EMAIL: str = ""

    # SQS queue for cross-pod role-cache invalidation on member removal.
    # Leave empty to disable (default — no-op in local dev / CI).
    SQS_CACHE_INVALIDATION_QUEUE_URL: str = ""
    AWS_REGION: str = "us-east-1"

    # CORS — empty list = no CORS (secure default). Set per-environment.
    CORS_ORIGINS: list[str] = []

    @field_validator("PASSPORT_SECRET_KEY")
    @classmethod
    def _secret_must_be_strong(cls, v: str) -> str:
        if v in ("", "change-me-in-production"):
            raise ValueError(
                "PASSPORT_SECRET_KEY must be a strong random secret. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if len(v) < 32:
            raise ValueError("PASSPORT_SECRET_KEY must be at least 32 characters long.")
        return v

    model_config = SettingsConfigDict(case_sensitive=True)


settings = Settings()
