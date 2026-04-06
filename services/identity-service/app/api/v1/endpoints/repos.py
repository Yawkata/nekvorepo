"""
Repository management endpoints.

Moved here from workflow-service because identity-service owns both repo_heads
(repository metadata) and user_repo_links (membership). Co-locating these
endpoints eliminates the cross-service HTTP call that was previously required
during repo creation and makes the saga fully atomic.

  POST /v1/repos            — create a repository (fully internal saga)
  GET  /v1/repos            — list repos the caller is a member of
  GET  /v1/repos/{repo_id}  — fetch a single repo's details
"""
import re
import uuid
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from shared.constants import RepoRole
from shared.models.identity import UserRepoLink
from shared.models.workflow import RepoHead
from shared.security import TokenData, verify_passport
from app.api import deps

log = structlog.get_logger()
router = APIRouter()

# Repository name: alphanumeric, hyphens, and spaces; no leading/trailing/consecutive spaces.
_REPO_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 \-]*$")


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CreateRepoRequest(BaseModel):
    repo_name: str
    description: Optional[str] = None

    @field_validator("repo_name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip().lower()
        if v.endswith(".deleted"):
            raise ValueError("Repository name must not end with '.deleted' (reserved suffix).")
        if len(v) < 3:
            raise ValueError("Repository name must be at least 3 characters.")
        if len(v) > 50:
            raise ValueError("Repository name must be 50 characters or fewer.")
        if "  " in v:
            raise ValueError("Repository name must not contain consecutive spaces.")
        if not _REPO_NAME_RE.match(v):
            raise ValueError(
                "Repository name may only contain letters, numbers, hyphens, and spaces."
            )
        return v

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if len(v) > 200:
            raise ValueError("Description must not exceed 200 characters.")
        return v or None


class RepoResponse(BaseModel):
    repo_id: uuid.UUID
    repo_name: str
    description: Optional[str]
    owner_id: str
    latest_commit_hash: Optional[str]
    version: int
    created_at: datetime


class RepoListItem(BaseModel):
    repo_id: uuid.UUID
    repo_name: str
    description: Optional[str]
    owner_id: str
    latest_commit_hash: Optional[str]
    version: int
    created_at: datetime
    role: str  # calling user's role in this repo


# ---------------------------------------------------------------------------
# POST /v1/repos
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=RepoResponse,
    status_code=201,
    responses={
        401: {"description": "Invalid or expired token"},
        409: {"description": "You already own a repository with that name"},
        500: {"description": "Failed to persist repository"},
    },
)
def create_repo(
    body: CreateRepoRequest,
    db: Session = Depends(deps.get_db),
    passport: TokenData = Security(verify_passport),
):
    """
    Creates a new repository and registers the caller as admin.

    The saga is fully internal — identity-service owns both repo_heads and
    user_repo_links, so both writes happen in a single atomic transaction.
    There is no compensating HTTP call on failure; the DB rollback handles it.

      1. Validate uniqueness (read-only query).
      2. INSERT repo_heads (flush to get the PK without committing).
      3. INSERT user_repo_links (admin role) in the same transaction.
      4. COMMIT — both rows land atomically, or neither does.
    """
    # Step 1 — friendly duplicate guard ahead of the DB constraint
    existing = db.exec(
        select(RepoHead).where(
            RepoHead.owner_id == passport.user_id,
            RepoHead.repo_name == body.repo_name,
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"You already own a repository named '{body.repo_name}'.",
        )

    # Steps 2 & 3 — single atomic transaction
    try:
        repo = RepoHead(
            repo_name=body.repo_name,
            description=body.description,
            owner_id=passport.user_id,
            version=0,
        )
        db.add(repo)
        db.flush()  # materialise repo.id without committing

        link = UserRepoLink(
            repo_id=repo.id,
            user_id=passport.user_id,
            role=RepoRole.admin,
        )
        db.add(link)
        db.commit()
        db.refresh(repo)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"You already own a repository named '{body.repo_name}'.",
        )
    except Exception:
        db.rollback()
        log.exception("create_repo_failed")
        raise HTTPException(status_code=500, detail="Failed to create repository.")

    log.info(
        "repo_created",
        repo_id=str(repo.id),
        repo_name=repo.repo_name,
        owner_id=repo.owner_id,
    )
    return RepoResponse(
        repo_id=repo.id,
        repo_name=repo.repo_name,
        description=repo.description,
        owner_id=repo.owner_id,
        latest_commit_hash=repo.latest_commit_hash,
        version=repo.version,
        created_at=repo.created_at,
    )


# ---------------------------------------------------------------------------
# GET /v1/repos
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=list[RepoListItem],
    summary="List repositories the caller is a member of",
    responses={401: {"description": "Invalid or expired token"}},
)
def list_repos(
    db: Session = Depends(deps.get_db),
    passport: TokenData = Security(verify_passport),
):
    """
    Returns every repository in which the authenticated user holds any role,
    sorted by creation date descending (newest first).

    Single JOIN query — no N+1, no cross-service calls.
    For commit activity data see GET /v1/repos/{id}/commits/history on
    workflow-service.
    """
    rows = db.exec(
        select(UserRepoLink, RepoHead)
        .join(RepoHead, UserRepoLink.repo_id == RepoHead.id)
        .where(UserRepoLink.user_id == passport.user_id)
        .order_by(RepoHead.created_at.desc())  # type: ignore[union-attr]
    ).all()

    return [
        RepoListItem(
            repo_id=repo.id,
            repo_name=repo.repo_name,
            description=repo.description,
            owner_id=repo.owner_id,
            latest_commit_hash=repo.latest_commit_hash,
            version=repo.version,
            created_at=repo.created_at,
            role=link.role,
        )
        for link, repo in rows
    ]


# ---------------------------------------------------------------------------
# GET /v1/repos/{repo_id}
# ---------------------------------------------------------------------------

@router.get(
    "/{repo_id}",
    response_model=RepoListItem,
    summary="Get repository details",
    responses={
        401: {"description": "Invalid or expired token"},
        403: {"description": "Not a member of this repository"},
        404: {"description": "Repository not found"},
    },
)
def get_repo(
    repo_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    passport: TokenData = Security(verify_passport),
):
    """
    Returns metadata for a single repository. Any member role is sufficient.

    Membership is verified inline against user_repo_links — no HTTP call to
    identity-service is needed because this IS identity-service.
    """
    link = db.exec(
        select(UserRepoLink).where(
            UserRepoLink.repo_id == repo_id,
            UserRepoLink.user_id == passport.user_id,
        )
    ).first()
    if not link:
        raise HTTPException(status_code=403, detail="Not a member of this repository.")

    repo = db.get(RepoHead, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found.")

    return RepoListItem(
        repo_id=repo.id,
        repo_name=repo.repo_name,
        description=repo.description,
        owner_id=repo.owner_id,
        latest_commit_hash=repo.latest_commit_hash,
        version=repo.version,
        created_at=repo.created_at,
        role=link.role,
    )
