import os
from typing import Dict, List, Optional
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWKClient
from shared.schemas.auth import TokenData # Import the existing schema

security = HTTPBearer()

# Internal Passport Config
PASSPORT_SECRET = os.getenv("PASSPORT_SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"

# Cognito Config for Initial Login Verification
REGION = os.getenv("AWS_REGION", "us-east-1")
USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID")
JWKS_URL = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}/.well-known/jwks.json"

jwks_client = PyJWKClient(JWKS_URL, cache_keys=True)

def verify_cognito_token(token: str) -> Dict:
    """Verifies the token directly against AWS Cognito JWKS."""
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=os.getenv("COGNITO_CLIENT_ID")
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid Cognito Token: {str(e)}")

def verify_passport(auth: HTTPAuthorizationCredentials = Security(security)) -> TokenData:
    """
    2026 Best Practice: Return a validated Pydantic model for type-safety.
    """
    token = auth.credentials
    try:
        payload = jwt.decode(
            token,
            PASSPORT_SECRET,
            algorithms=[ALGORITHM],
            issuer="identity-service"
        )
        # Convert raw dict to type-safe model
        return TokenData(
            user_id=payload["sub"],
            email=payload.get("email"),
            permissions=payload.get("permissions", {})
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid Passport: {str(e)}")

def has_repo_access(passport: Dict, repo_id: str, required_role: Optional[str] = None) -> bool:
    """
    2026 Optimized: $O(1)$ lookup for permissions.
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