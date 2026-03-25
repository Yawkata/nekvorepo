import jwt
from datetime import datetime, timedelta, timezone
from app.core.config import settings


def create_passport_token(user_id: str, email: str, repo_count: int = 0) -> str:
    """
    Generates the signed internal Passport JWT.

    Per spec, the JWT carries only user identity — NOT repo permissions.
    Downstream services resolve authorization by calling the identity-service
    role endpoint with a 60-second TTL cache, ensuring role changes propagate
    within one TTL period rather than waiting for JWT expiry (up to 1 hour).

    repo_count is an informational hint for the frontend; not used for authz.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": user_id,
        "email": email,
        "repo_count": repo_count,
        "iat": datetime.now(timezone.utc),
        "exp": expire,
        "iss": "identity-service",
        "aud": "internal-microservices",
    }
    return jwt.encode(payload, settings.PASSPORT_SECRET_KEY, algorithm=settings.ALGORITHM)
