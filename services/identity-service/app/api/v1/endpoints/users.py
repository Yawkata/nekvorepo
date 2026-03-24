"""
User-facing endpoints.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, Security
from sqlmodel import Session, select
from pydantic import BaseModel
from datetime import datetime
from shared.models.identity import UserRepoLink
from shared.models.workflow import RepoHead, RepoCommit
from shared.security import verify_passport, TokenData
from shared.constants import RepoRole
from app.api import deps

router = APIRouter()


class RepoSummary(BaseModel):
    repo_id: int
    repo_name: str
    role: RepoRole
    last_activity: Optional[datetime]
    created_at: datetime


@router.get("/me/repos", response_model=List[RepoSummary])
def list_my_repos(
    db: Session = Depends(deps.get_db),
    passport: TokenData = Security(verify_passport),
):
    """
    Returns all repositories the authenticated user has access to,
    with role and last-activity metadata. Used by the dashboard.
    Last activity = timestamp of the most recent approved commit,
    falling back to created_at if no commits exist.
    """
    links = db.exec(
        select(UserRepoLink).where(UserRepoLink.user_id == passport.user_id)
    ).all()

    results: List[RepoSummary] = []
    for link in links:
        repo = db.get(RepoHead, link.repo_id)
        if not repo:
            continue

        # Latest approved commit timestamp as last_activity
        last_commit = db.exec(
            select(RepoCommit)
            .where(
                RepoCommit.repo_id == link.repo_id,
                RepoCommit.status == "approved",
            )
            .order_by(RepoCommit.timestamp.desc())
        ).first()

        results.append(
            RepoSummary(
                repo_id=link.repo_id,
                repo_name=repo.repo_name,
                role=link.role,
                last_activity=last_commit.timestamp if last_commit else None,
                created_at=repo.created_at,
            )
        )

    # Sort: by last_activity desc (None falls back to created_at)
    results.sort(
        key=lambda r: r.last_activity or r.created_at,
        reverse=True,
    )
    return results
