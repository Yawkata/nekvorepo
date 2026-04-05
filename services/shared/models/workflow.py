import uuid
from typing import Optional
from sqlmodel import SQLModel, Field, Column, DateTime
from sqlalchemy import func, Enum as SAEnum, ForeignKey, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from datetime import datetime
from ..constants import NodeType, CommitStatus, DraftStatus


class RepoHead(SQLModel, table=True):
    __tablename__ = "repo_heads"
    __table_args__ = (UniqueConstraint("owner_id", "repo_name"),)

    id: Optional[uuid.UUID] = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
        ),
    )
    repo_name: str = Field(max_length=50)
    owner_id: str = Field(max_length=64, index=True)  # Cognito sub
    description: Optional[str] = Field(default=None, max_length=200)  # spec: varchar 200
    latest_commit_hash: Optional[str] = Field(default=None, max_length=64, index=True)
    version: int = Field(default=0, nullable=False)  # Optimistic locking counter
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )


class RepoTreeRoot(SQLModel, table=True):
    """The 'Tree' table — contains unique tree identifiers only."""
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
    content_hash: str = Field(max_length=64)  # Points to blobs.blob_hash or another tree_hash


class Blob(SQLModel, table=True):
    """Table 6 — S3-backed binary content registry."""
    __tablename__ = "blobs"

    id: Optional[uuid.UUID] = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
        ),
    )
    blob_hash: str = Field(unique=True, index=True, max_length=64)  # SHA-256 hex; also used as the S3 object key
    size: int = Field(default=0, nullable=False)
    content_type: str = Field(default="text/plain", nullable=False)
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )


class RepoCommit(SQLModel, table=True):
    """Table 5 — Commit records."""
    __tablename__ = "repo_commits"

    id: Optional[int] = Field(default=None, primary_key=True)
    commit_hash: str = Field(unique=True, index=True, max_length=64)
    repo_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("repo_heads.id"),
            nullable=False,
            index=True,
        ),
    )
    owner_id: str = Field(max_length=64, index=True)
    parent_commit_hash: Optional[str] = Field(default=None, max_length=64)
    tree_id: int = Field(foreign_key="repo_tree_roots.id")
    draft_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("drafts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    status: CommitStatus = Field(
        sa_column=Column(
            SAEnum(CommitStatus), server_default=CommitStatus.pending, nullable=False
        )
    )
    commit_summary: str = Field(max_length=200)  # Required one-liner — shown in commit lists
    commit_description: Optional[str] = Field(default=None, max_length=5000)  # Optional extended description
    changes_summary: Optional[str] = Field(default=None)  # e.g. "3 files changed, 1 added, 2 modified" — auto-computed
    reviewer_comment: Optional[str] = Field(default=None)  # Populated on reviewer rejection
    author_email: Optional[str] = Field(default=None, max_length=254)  # Author's email at submission time — used for notifications
    timestamp: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )


class Draft(SQLModel, table=True):
    """Table 8 — Working draft for a pending commit submission."""
    __tablename__ = "drafts"

    id: Optional[uuid.UUID] = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
        ),
    )
    repo_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("repo_heads.id"),
            nullable=False,
            index=True,
        ),
    )
    user_id: str = Field(max_length=64, index=True)  # Cognito sub (spec field name)
    label: Optional[str] = Field(default=None, max_length=100)  # User-defined draft label
    base_commit_hash: Optional[str] = Field(default=None, max_length=64)
    commit_hash: Optional[str] = Field(default=None, max_length=64)  # FK to repo_commits after submit
    status: DraftStatus = Field(
        sa_column=Column(
            SAEnum(DraftStatus), server_default=DraftStatus.editing, nullable=False
        )
    )
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
    updated_at: Optional[datetime] = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )
    )
