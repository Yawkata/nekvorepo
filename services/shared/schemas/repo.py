import uuid
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from ..constants import RepoRole

class RepoCreate(BaseModel):
    name: str
    description: Optional[str] = None

class RepoRead(BaseModel):
    repo_id: uuid.UUID
    name: str
    latest_hash: str
    role_in_repo: RepoRole
    created_at: datetime