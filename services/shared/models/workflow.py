import uuid
from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint

class RepoHead(SQLModel, table=True):
    __tablename__ = "repo_heads"
    repo_id: uuid.UUID = Field(primary_key=True) #искаме просто да сочи към repo_id, не да генерираме
    latest_hash: str = Field(nullable=False, max_length=64)