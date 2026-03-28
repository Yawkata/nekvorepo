"""
User-facing endpoints.
"""
import uuid
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, Security
from sqlmodel import Session, select, func
from pydantic import BaseModel
from shared.models.identity import UserRepoLink
from shared.models.workflow import RepoHead, RepoCommit
from shared.security import verify_passport, TokenData
from shared.constants import RepoRole, CommitStatus
from app.api import deps

router = APIRouter()


class RepoSummary(BaseModel):
    repo_id: uuid.UUID
    repo_name: str
    description: Optional[str]
    role: RepoRole
    last_activity: Optional[datetime]
    created_at: datetime


@router.get("/me/repos", response_model=List[RepoSummary], responses={401: {"description": "Invalid or expired token"}})
def list_my_repos(
    db: Session = Depends(deps.get_db),
    passport: TokenData = Security(verify_passport),
):
    """
    Returns all repositories the authenticated user has access to,
    with role and last-activity metadata. Used by the dashboard.

    Last activity = timestamp of the most recent approved commit,
    falling back to created_at if no commits exist.

    Implemented as 2 queries instead of 2N to eliminate the N+1 pattern:
      1. JOIN UserRepoLink + RepoHead for all memberships.
      2. Single GROUP BY query for max approved-commit timestamp per repo.
    """
    # Query 1: all memberships + repo metadata in one JOIN
    rows = db.exec(
        select(UserRepoLink, RepoHead)
        .join(RepoHead, UserRepoLink.repo_id == RepoHead.id)
        .where(UserRepoLink.user_id == passport.user_id)
    ).all()

    if not rows:
        return []

    repo_ids = [repo.id for _, repo in rows]

    # Query 2: latest approved commit timestamp per repo (single round-trip)
    commit_rows = db.exec(
        select(RepoCommit.repo_id, func.max(RepoCommit.timestamp).label("last_ts"))
        .where(
            RepoCommit.repo_id.in_(repo_ids),
            RepoCommit.status == CommitStatus.approved,
        )
        .group_by(RepoCommit.repo_id)
    ).all()
    last_activity_map: dict = {row.repo_id: row.last_ts for row in commit_rows}

    results: List[RepoSummary] = []
    for link, repo in rows:
        results.append(
            RepoSummary(
                repo_id=repo.id,
                repo_name=repo.repo_name,
                description=repo.description,
                role=link.role,
                last_activity=last_activity_map.get(repo.id),
                created_at=repo.created_at,
            )
        )

    # Sort: last_activity desc, fall back to created_at
    results.sort(
        key=lambda r: r.last_activity or r.created_at,
        reverse=True,
    )
    return results
