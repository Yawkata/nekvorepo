"""
Cognito JWKS verification — identity-service only.

This module creates a module-level JWKS cache at import time and requires
COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID environment variables.

No other service should import from this module.  Passport JWT verification
for all other services is handled by shared.security.passport.
"""
import os
import time
import threading
from datetime import timedelta
from typing import Dict, Optional

import jwt
from jwt import PyJWKClient
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Cognito JWKS Config — populated from identity-service env vars
# ---------------------------------------------------------------------------
_REGION = os.getenv("AWS_REGION", "us-east-1")
_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID")
_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID")

_COGNITO_BASE_URL = f"https://cognito-idp.{_REGION}.amazonaws.com/{_USER_POOL_ID}"
JWKS_URL = f"{_COGNITO_BASE_URL}/.well-known/jwks.json"
_ISSUER = _COGNITO_BASE_URL  # iss claim value in Cognito IdTokens

_JWKS_TTL_SECONDS = 24 * 60 * 60  # 24 hours


class _JWKSCache:
    """
    Thread-safe JWKS cache with a 24-hour TTL.
    On a JWT validation failure caused by an unknown kid, the cache is
    immediately invalidated and JWKS re-fetched — handles Cognito key rotation
    without requiring a service restart.
    """

    def __init__(self, jwks_url: str) -> None:
        self._jwks_url = jwks_url
        self._client: Optional[PyJWKClient] = None
        self._fetched_at: float = 0.0
        self._lock = threading.Lock()

    def _is_expired(self) -> bool:
        return self._client is None or (
            time.monotonic() - self._fetched_at
        ) > _JWKS_TTL_SECONDS

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
    """
    Verifies a Cognito IdToken directly against the Cognito JWKS endpoint.
    Returns the decoded payload dict on success, raises HTTP 401 on failure.
    """
    try:
        signing_key = _jwks_cache.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=_CLIENT_ID,
            issuer=_ISSUER,
            leeway=timedelta(seconds=10),  # tolerate minor clock skew with AWS
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired Cognito token.")
