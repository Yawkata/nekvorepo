"""
User-facing endpoints.
"""
import uuid
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, Security
from sqlmodel import Session, select
from pydantic import BaseModel
from shared.models.identity import UserRepoLink
from shared.models.workflow import RepoHead
from shared.security import verify_passport, TokenData
from shared.constants import RepoRole
from app.api import deps

router = APIRouter()


class RepoSummary(BaseModel):
    repo_id: uuid.UUID
    repo_name: str
    description: Optional[str]
    role: RepoRole
    created_at: datetime


@router.get("/me/repos", response_model=List[RepoSummary], responses={401: {"description": "Invalid or expired token"}})
def list_my_repos(
    db: Session = Depends(deps.get_db),
    passport: TokenData = Security(verify_passport),
):
    """
    Returns all repositories the authenticated user has access to, with role
    metadata. Used by the dashboard.

    Single JOIN query — no N+1, no cross-service DB reads.
    For commit activity (last_activity), call
    GET /v1/repos/{id}/commits/history on workflow-service.
    """
    rows = db.exec(
        select(UserRepoLink, RepoHead)
        .join(RepoHead, UserRepoLink.repo_id == RepoHead.id)
        .where(UserRepoLink.user_id == passport.user_id)
        .order_by(RepoHead.created_at.desc())  # type: ignore[union-attr]
    ).all()

    return [
        RepoSummary(
            repo_id=repo.id,
            repo_name=repo.repo_name,
            description=repo.description,
            role=link.role,
            created_at=repo.created_at,
        )
        for link, repo in rows
    ]
