from typing import Optional
from sqlmodel import SQLModel, Field, Column, DateTime
from sqlalchemy import func, Enum as SAEnum
import uuid
from datetime import datetime
from ..constants import NodeType, CommitStatus

class RepoHead(SQLModel, table=True): 
    __tablename__ = "repo_heads"
    repo_id: uuid.UUID = Field(primary_key=True)
    repo_name: str = Field(max_length=255)
    creator_id: str = Field(max_length=64) 
    latest_hash: Optional[str] = Field(default=None, max_length=64, index=True)
    version: int = Field(default=1) # For Optimistic Locking in 2026

class RepoTree(SQLModel, table=True): 
    __tablename__ = "repo_trees"
    id: Optional[int] = Field(default=None, primary_key=True)
    tree_hash: str = Field(index=True, max_length=64)
    repo_id: uuid.UUID = Field(index=True) # Added to scope tree lookups
    type: NodeType = Field(sa_column=Column(SAEnum(NodeType), nullable=False))
    name: str = Field(max_length=255)
    content_hash: str = Field(max_length=64) 
    content_type: str = Field(default="text/plain")
    size: int = Field(default=0)

class RepoCommit(SQLModel, table=True): 
    __tablename__ = "repo_commits"
    commit_hash: str = Field(primary_key=True, max_length=64)
    repo_id: uuid.UUID = Field(index=True)
    author_id: str = Field(max_length=64, index=True) # Added for Reviewer POV
    parent_commit_hash: Optional[str] = Field(default=None, max_length=64)
    tree_hash: str = Field(max_length=64)
    status: CommitStatus = Field(
        sa_column=Column(SAEnum(CommitStatus), server_default=CommitStatus.pending)
    )
    timestamp: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )