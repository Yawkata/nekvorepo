import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, Column, DateTime
from sqlalchemy import UniqueConstraint, Index, func, Enum as SAEnum
from shared.constants import RepoRole

class UserRepoLink(SQLModel, table=True):
    __tablename__ = "user_repo_links"
    __table_args__ = (
        UniqueConstraint("repo_id", "user_id", name="uq_user_repo"),
        Index("ix_user_repo_user_id", "user_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    repo_id: uuid.UUID = Field(nullable=False)
    user_id: str = Field(nullable=False, max_length=64) # Cognito Sub
    
    role: RepoRole = Field(
        default=RepoRole.reader, #дефолта в полето, не в колоната. за колоната оставяме сървър дефолт
        sa_column=Column(SAEnum(RepoRole, name="repo_role"), nullable=False),
    )
    
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )