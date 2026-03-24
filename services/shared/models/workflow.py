from typing import Optional
from sqlmodel import SQLModel, Field, Column, DateTime
from sqlalchemy import func, Enum as SAEnum, ForeignKey
from datetime import datetime
from ..constants import NodeType, CommitStatus

class RepoHead(SQLModel, table=True):
    __tablename__ = "repo_heads"
    
    # Enable SQLAlchemy's built-in optimistic concurrency control
    __mapper_args__ = {
        "version_id_col": "version"
    }

    id: Optional[int] = Field(default=None, primary_key=True)
    repo_name: str = Field(max_length=255)
    owner_id: str = Field(max_length=64, index=True) # Cognito sub
    latest_hash: Optional[str] = Field(default=None, max_length=64, index=True)
    version: int = Field(default=1, nullable=False) # For Optimistic Locking
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )

class RepoTreeRoot(SQLModel, table=True):
    """The 'Tree' table - contains unique tree identifiers only."""
    __tablename__ = "repo_tree_roots"
    id: Optional[int] = Field(default=None, primary_key=True)
    tree_hash: str = Field(unique=True, index=True, max_length=64)

class RepoTreeEntry(SQLModel, table=True):
    """The 'Tree-Object Association' table."""
    __tablename__ = "repo_tree_entries"
    id: Optional[int] = Field(default=None, primary_key=True)
    tree_id: int = Field(foreign_key="repo_tree_roots.id", index=True)
    type: NodeType = Field(sa_column=Column(SAEnum(NodeType), nullable=False))
    name: str = Field(max_length=255)
    content_hash: str = Field(max_length=64) # Points to S3 object or another RepoTreeRoot hash
    content_type: str = Field(default="text/plain")
    size: int = Field(default=0)

class RepoCommit(SQLModel, table=True):
    __tablename__ = "repo_commits"
    id: Optional[int] = Field(default=None, primary_key=True)
    commit_hash: str = Field(unique=True, index=True, max_length=64)
    repo_id: int = Field(foreign_key="repo_heads.id", index=True)
    owner_id: str = Field(max_length=64, index=True)
    parent_commit_hash: Optional[str] = Field(default=None, max_length=64)
    tree_id: int = Field(foreign_key="repo_tree_roots.id")
    status: CommitStatus = Field(
        sa_column=Column(SAEnum(CommitStatus), server_default=CommitStatus.pending)
    )
    timestamp: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )