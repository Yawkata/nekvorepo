"""
Repo-service domain models.

These tables are owned and written by repo-service.
Other services (workflow-service) may read them but must not write them
outside of the narrow status-transition columns explicitly granted by
per-service PostgreSQL roles.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Column, DateTime, Field, SQLModel

from ..constants import DraftStatus


class Blob(SQLModel, table=True):
    """
    S3-backed binary content registry.

    Each row represents a unique file blob keyed by its SHA-256 hash.
    The blob_hash doubles as the S3 object key — content-addressed storage
    makes deduplication free: two identical files produce one S3 object.

    Written by repo-service (sync-blobs), read by workflow-service (tree building)
    and repo-service (view mode, reconstruct).
    """
    __tablename__ = "blobs"

    id: Optional[uuid.UUID] = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
        ),
    )
    blob_hash: str = Field(unique=True, index=True, max_length=64)  # SHA-256 hex; also S3 object key
    size: int = Field(default=0, nullable=False)
    content_type: str = Field(default="text/plain", nullable=False)
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )


class Draft(SQLModel, table=True):
    """
    Working draft for a pending commit submission.

    A draft represents a user's in-progress file tree stored on EFS.
    repo-service owns the I/O lifecycle (create EFS dir, write files, delete dir).
    workflow-service owns status transitions in the commit flow
    (editing → committing → pending → approved / rejected / sibling_rejected).
    """
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
    user_id: str = Field(max_length=64, index=True)             # Cognito sub
    label: Optional[str] = Field(default=None, max_length=100) # User-defined draft label
    base_commit_hash: Optional[str] = Field(default=None, max_length=64)
    commit_hash: Optional[str] = Field(default=None, max_length=64)  # Set after submit
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
