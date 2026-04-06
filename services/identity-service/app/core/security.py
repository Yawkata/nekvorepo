import jwt
from datetime import datetime, timedelta, timezone
from app.core.config import settings


def create_passport_token(user_id: str, email: str) -> str:
    """
    Generates the signed internal Passport JWT.

    The JWT carries only stable user identity claims (sub, email). It does NOT
    carry repo counts, roles, or any other volatile aggregate — those go stale
    within the JWT's 1-hour lifetime and should be fetched from authoritative
    endpoints instead.

    Downstream services resolve authorization by calling
    GET /v1/internal/repos/{id}/role?user_id={uid} with a 60-second TTL cache.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": user_id,
        "email": email,
        "iat": datetime.now(timezone.utc),
        "exp": expire,
        "iss": "identity-service",
        "aud": "internal-microservices",
    }
    return jwt.encode(payload, settings.PASSPORT_SECRET_KEY, algorithm=settings.ALGORITHM)
