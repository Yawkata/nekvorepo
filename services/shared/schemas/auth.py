from pydantic import BaseModel, Field
from typing import Optional


class Token(BaseModel):
    access_token: str
    token_type: str
    refresh_token: Optional[str] = None  # Cognito opaque refresh token (30-day TTL)


class TokenData(BaseModel):
    """
    Typed representation of a decoded Passport JWT payload.

    Contains only user identity — NOT repo permissions.
    Per spec, downstream services resolve roles by calling
    GET /v1/internal/repos/{id}/role?user_id={uid} with a 60-second local cache.
    This ensures role changes propagate within 60 seconds across all pods,
    and member removal takes effect immediately via SQS cache invalidation.

    repo_count is an informational hint for the frontend (e.g. badge counts).
    It is not used for authorization decisions.
    """
    user_id: str
    email: Optional[str] = None
    repo_count: int = Field(default=0)
