import uuid
from typing import Optional
from sqlmodel import SQLModel, Field, Column, DateTime
from sqlalchemy import UniqueConstraint, func, Enum as SAEnum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from datetime import datetime
from ..constants import RepoRole


class UserRepoLink(SQLModel, table=True):
    __tablename__ = "user_repo_links"
    __table_args__ = (UniqueConstraint("repo_id", "user_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    repo_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("repo_heads.id"),
            nullable=False,
            index=True,
        ),
    )
    user_id: str = Field(index=True)  # Cognito sub
    role: RepoRole = Field(
        sa_column=Column(SAEnum(RepoRole), nullable=False, server_default=RepoRole.reader)
    )
    created_at: Optional[datetime] = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
