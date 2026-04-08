from pydantic import BaseModel
from typing import Optional


class TokenData(BaseModel):
    """
    Typed representation of a decoded Passport JWT payload.

    Contains only stable user identity claims — NOT repo permissions or volatile
    aggregates. Downstream services resolve roles by calling
    GET /v1/internal/repos/{id}/role?user_id={uid} with a 60-second local cache.
    This ensures role changes propagate within 60 seconds across all pods,
    and member removal takes effect immediately via SQS cache invalidation.
    """
    user_id: str
    email: Optional[str] = None
