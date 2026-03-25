import uuid
from typing import Optional
from sqlmodel import SQLModel, Field, Column, DateTime
from sqlalchemy import func, Enum as SAEnum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from datetime import datetime
from ..constants import RepoRole


class InviteToken(SQLModel, table=True):
    """Table 7 — Single-use invite links for adding collaborators to a repo."""
    __tablename__ = "invite_tokens"

    # The token UUID doubles as the primary key; the URL carries only this UUID.
    id: Optional[uuid.UUID] = Field(
        default_factory=uuid.uuid4,
        sa_column=Column(
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=None,  # Always generated in Python to be safe
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
    invited_email: str = Field(max_length=254, index=True)
    role: RepoRole = Field(
        sa_column=Column(SAEnum(RepoRole), nullable=False, server_default=RepoRole.reader)
    )
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    consumed_at: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True), nullable=True)
    )
