import jwt
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
from app.core.config import settings
from shared.constants import RepoRole # Add this import

def create_passport_token(user_id: str, email: str, permissions: Dict[str, RepoRole]) -> str:
    """
    Generates the signed internal Passport.
    permissions: Dict of {repo_id: role}
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES if hasattr(settings, 'ACCESS_TOKEN_EXPIRE_MINUTES') else 60
    )

    to_encode = {
        "sub": user_id,
        "email": email,
        "permissions": permissions, # This is now the optimized Map
        "iat": datetime.now(timezone.utc),
        "exp": expire,
        "iss": "identity-service"
    }

    return jwt.encode(to_encode, settings.PASSPORT_SECRET_KEY, algorithm=settings.ALGORITHM)