"""
Repository lifecycle endpoints.
"""
import re
import uuid
from typing import Optional
from datetime import datetime
import structlog
from fastapi import APIRouter, Depends, Security, HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from shared.models.workflow import RepoHead
from shared.models.identity import UserRepoLink
from shared.security import verify_passport, TokenData
from shared.constants import RepoRole
from app.api import deps
from app.services import identity_client

log = structlog.get_logger()
router = APIRouter()

# Per spec: "alphanumeric characters plus hyphens and spaces"
# No leading/trailing/consecutive spaces enforced by field_validator.
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
# Endpoints
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
    Creates a new repository owned by the authenticated user.

    Saga pattern — the FK on user_repo_links.repo_id requires the repo_heads row
    to exist before identity-service can insert the membership:

      1. Validate uniqueness (read-only query).
      2. Persist repo_heads row (DB write).
         → On IntegrityError: 409 (race-condition duplicate).
      3. Call identity-service to register the creator as admin.
         → On failure: compensate by deleting the repo row, then propagate error.
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

    # Step 2 — write repo_heads (must come before identity-service call due to FK)
    try:
        repo = RepoHead(
            repo_name=body.repo_name,
            description=body.description,
            owner_id=passport.user_id,
            version=0,
        )
        db.add(repo)
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
        log.exception("create_repo_db_failed")
        raise HTTPException(status_code=500, detail="Failed to persist repository.")

    repo_id = repo.id

    # Step 3 — register admin membership; compensate on failure
    try:
        identity_client.create_membership(
            repo_id=repo_id,
            user_id=passport.user_id,
            role=RepoRole.admin,
        )
    except HTTPException:
        try:
            db.delete(repo)
            db.commit()
        except Exception:
            log.exception("create_repo_compensation_failed", repo_id=str(repo_id))
        raise

    log.info("repo_created", repo_id=str(repo.id), repo_name=repo.repo_name, owner_id=repo.owner_id)

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
# GET /v1/repos — list all repos the caller is a member of
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=list[RepoListItem],
    summary="List repositories the caller is a member of",
    responses={
        401: {"description": "Invalid or expired token"},
    },
)
def list_repos(
    db: Session = Depends(deps.get_db),
    passport: TokenData = Security(verify_passport),
):
    """
    Returns every repository in which the authenticated user holds any role,
    sorted by creation date descending (newest first).

    Implemented as two queries (no N+1):
      1. SELECT all UserRepoLink rows for this user.
      2. SELECT all matching RepoHead rows via IN clause.
    """
    links = db.exec(
        select(UserRepoLink).where(UserRepoLink.user_id == passport.user_id)
    ).all()

    if not links:
        return []

    repo_ids = [link.repo_id for link in links]
    role_map: dict[uuid.UUID, str] = {link.repo_id: link.role for link in links}

    repos = db.exec(
        select(RepoHead)
        .where(RepoHead.id.in_(repo_ids))  # type: ignore[attr-defined]
        .order_by(RepoHead.created_at.desc())  # type: ignore[union-attr]
    ).all()

    return [
        RepoListItem(
            repo_id=r.id,
            repo_name=r.repo_name,
            description=r.description,
            owner_id=r.owner_id,
            latest_commit_hash=r.latest_commit_hash,
            version=r.version,
            created_at=r.created_at,
            role=role_map.get(r.id, ""),
        )
        for r in repos
    ]


# ---------------------------------------------------------------------------
# GET /v1/repos/{repo_id} — fetch a single repo's details
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
    membership: tuple[TokenData, str] = Depends(deps.require_member),
):
    """
    Returns metadata for a single repository.  Any member role is sufficient.
    """
    _, role = membership
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
        role=role,
    )
