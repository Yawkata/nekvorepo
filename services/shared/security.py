import os
import time
import threading
from datetime import timedelta
from typing import Dict, Optional
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWKClient
from shared.schemas.auth import TokenData

security = HTTPBearer()

# Internal Passport Config
PASSPORT_SECRET = os.getenv("PASSPORT_SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"

# Cognito Config for Initial Login Verification
REGION = os.getenv("AWS_REGION", "us-east-1")
USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID")
JWKS_URL = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}/.well-known/jwks.json"

_JWKS_TTL_SECONDS = 24 * 60 * 60  # 24 hours


class _JWKSCache:
    """
    JWKS cache with 24-hour TTL.
    On JWT validation failure due to an unknown kid, the cache is immediately
    invalidated and the JWKS re-fetched (handles Cognito key rotation).
    """

    def __init__(self, jwks_url: str) -> None:
        self._jwks_url = jwks_url
        self._client: Optional[PyJWKClient] = None
        self._fetched_at: float = 0.0
        self._lock = threading.Lock()

    def _is_expired(self) -> bool:
        return self._client is None or (time.monotonic() - self._fetched_at) > _JWKS_TTL_SECONDS

    def _refresh(self) -> None:
        """Must be called with _lock held."""
        self._client = PyJWKClient(self._jwks_url)
        self._fetched_at = time.monotonic()

    def get_signing_key_from_jwt(self, token: str):
        with self._lock:
            if self._is_expired():
                self._refresh()

        try:
            return self._client.get_signing_key_from_jwt(token)
        except jwt.exceptions.PyJWKClientConnectionError:
            # Network error — propagate immediately, don't hide it
            raise
        except Exception:
            # Unknown kid or other key-lookup failure — invalidate and retry once
            with self._lock:
                self._refresh()
            return self._client.get_signing_key_from_jwt(token)


_jwks_cache = _JWKSCache(JWKS_URL)


def verify_cognito_token(token: str) -> Dict:
    """Verifies an IdToken directly against AWS Cognito JWKS."""
    try:
        signing_key = _jwks_cache.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=os.getenv("COGNITO_CLIENT_ID"),
            leeway=timedelta(seconds=10),  # tolerate minor clock skew with AWS
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid Cognito token: {str(e)}")

def verify_passport(auth: HTTPAuthorizationCredentials = Security(security)) -> TokenData:
    """
    2026 Best Practice: Return a validated Pydantic model for type-safety.
    Validates expiration automatically if 'exp' claim is present.
    """
    token = auth.credentials
    try:
        payload = jwt.decode(
            token,
            PASSPORT_SECRET,
            algorithms=[ALGORITHM],
            issuer="identity-service",
            audience="internal-microservices" # Added explicit audience checking
        )
        return TokenData(
            user_id=payload["sub"],
            email=payload.get("email"),
            permissions=payload.get("permissions", {})
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Passport has expired")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid Passport: {str(e)}")

def has_repo_access(passport: Dict, repo_id: str, required_role: Optional[str] = None) -> bool:
    """
    2026 Optimized: O(1) lookup for permissions.
    Passport['permissions'] is now a Dict[repo_id, role].
    """
    permission_map: Dict[str, str] = passport.get("permissions", {})
    user_role = permission_map.get(str(repo_id))

    if not user_role:
        return False

    if not required_role:
        return True

    # Role Hierarchy: admin > author > reviewer > reader
    role_weights = {"admin": 4, "author": 3, "reviewer": 2, "reader": 1}
    return role_weights.get(user_role, 0) >= role_weights.get(required_role, 0)