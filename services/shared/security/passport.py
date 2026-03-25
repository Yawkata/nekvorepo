"""
Passport (internal HS256 JWT) verification.

Import this module in ANY service that needs to authenticate inbound requests.
It has ZERO dependency on Cognito, JWKS, or email-validator.

Role authorisation: Downstream services should NOT rely on JWT claims for
permission checks. Instead call GET /v1/internal/repos/{id}/role?user_id={uid}
and cache the result for 60 seconds. This guarantees role changes propagate
within one cache TTL and member removal takes effect immediately via SQS
cache invalidation.
"""
import os
from typing import Optional
import jwt
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from shared.schemas.auth import TokenData

_ALGORITHM = "HS256"
_ISSUER = "identity-service"
_AUDIENCE = "internal-microservices"

security = HTTPBearer()


def _get_secret() -> str:
    """
    Reads PASSPORT_SECRET_KEY from the environment at call time.
    Raises RuntimeError on startup if the value is absent or known-weak,
    so the service fails fast with a clear message.
    """
    secret = os.getenv("PASSPORT_SECRET_KEY")
    if not secret:
        raise RuntimeError(
            "PASSPORT_SECRET_KEY is not set. "
            "Generate a secure value with: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    if secret == "change-me-in-production":
        raise RuntimeError(
            "PASSPORT_SECRET_KEY is still set to the default insecure placeholder. "
            "Replace it with a strong random secret before starting the service."
        )
    return secret


def verify_passport(
    auth: HTTPAuthorizationCredentials = Security(security),
) -> TokenData:
    """
    FastAPI Security dependency. Validates the inbound Passport JWT and
    returns a typed TokenData model. Raises 401 on any failure.
    """
    token = auth.credentials
    try:
        payload = jwt.decode(
            token,
            _get_secret(),
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
            audience=_AUDIENCE,
        )
        return TokenData(
            user_id=payload["sub"],
            email=payload.get("email"),
            repo_count=payload.get("repo_count", 0),
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Passport has expired.")
    except RuntimeError:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or malformed passport.")
