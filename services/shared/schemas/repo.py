from pydantic import BaseModel
from typing import List, Optional
import uuid

class RepoExplorerItem(BaseModel):
    name: str
    type: str # 'file' or 'folder'
    content_url: Optional[str] = None # S3 Pre-signed URL
    is_draft: bool = False

class RepoExplorerResponse(BaseModel):
    repo_id: uuid.UUID
    current_path: str
    items: List[RepoExplorerItem]