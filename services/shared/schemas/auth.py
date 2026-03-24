from pydantic import BaseModel, EmailStr, Field
from typing import List, Dict, Any, Optional
from shared.constants import RepoRole

class Token(BaseModel):
    access_token: str
    token_type: str
    refresh_token: Optional[str] = None  # Cognito opaque refresh token (30-day TTL)

class TokenData(BaseModel):
    user_id: str
    email: Optional[EmailStr] = None
    # Optimized for 2026: Map of {repo_id: role} for O(1) checks
    permissions: Dict[str, RepoRole] = Field(default_factory=dict)

class UserRegister(BaseModel):
    email: EmailStr
    password: str
    full_name: str