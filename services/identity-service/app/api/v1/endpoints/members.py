"""
Member management endpoints.

  GET    /v1/repos/{repo_id}/members                    — List members (any member)
  PUT    /v1/repos/{repo_id}/members/{uid}/role         — Change role (admin)
  DELETE /v1/repos/{repo_id}/members/{uid}              — Remove member (admin)
"""
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, select

from shared.constants import RepoRole
from shared.models.identity import User, UserRepoLink
from shared.models.workflow import RepoHead
from shared.schemas.auth import TokenData
from shared.security import verify_passport
from app.api import deps
from app.core.config import settings
from app.services import workflow_client, repo_client
from app.services.notifications import send_role_changed_notification, send_removed_notification
from app.services.sqs import publish_cache_invalidation

log = structlog.get_logger()
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class MemberResponse(BaseModel):
    user_id: str
    email: str | None
    role: str
    joined_at: str


class RoleChangeRequest(BaseModel):
    role: RepoRole


class RoleChangeResponse(BaseModel):
    user_id: str
    role: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_admin(repo_id: uuid.UUID, passport: TokenData, db: Session) -> RepoHead:
    """Verify caller is admin of repo_id. Returns RepoHead. Raises 403/404."""
    repo = db.get(RepoHead, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found.")
    link = db.exec(
        select(UserRepoLink).where(
            UserRepoLink.repo_id == repo_id,
            UserRepoLink.user_id == passport.user_id,
        )
    ).first()
    if link is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not a member of this repository.")
    if link.role != RepoRole.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required.")
    return repo


def _require_member(repo_id: uuid.UUID, passport: TokenData, db: Session) -> None:
    """Verify caller is any member of repo_id. Raises 403/404."""
    repo = db.get(RepoHead, repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found.")
    link = db.exec(
        select(UserRepoLink).where(
            UserRepoLink.repo_id == repo_id,
            UserRepoLink.user_id == passport.user_id,
        )
    ).first()
    if link is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not a member of this repository.")


def _get_user_email(user_id: str, db: Session) -> str | None:
    row = db.exec(select(User).where(User.id == user_id)).first()
    return row.email if row else None


def _count_admins(repo_id: uuid.UUID, db: Session) -> int:
    result = db.exec(
        select(UserRepoLink).where(
            UserRepoLink.repo_id == repo_id,
            UserRepoLink.role == RepoRole.admin,
        )
    ).all()
    return len(result)


# ---------------------------------------------------------------------------
# GET /v1/repos/{repo_id}/members  — List members with optional search
# ---------------------------------------------------------------------------

@router.get(
    "/{repo_id}/members",
    response_model=list[MemberResponse],
    status_code=status.HTTP_200_OK,
    summary="List repository members (any member)",
)
def list_members(
    repo_id: uuid.UUID,
    q: str | None = Query(default=None, description="Filter by email (case-insensitive partial match)"),
    passport: TokenData = Security(verify_passport),
    db: Session = Depends(deps.get_db),
) -> list[MemberResponse]:
    _require_member(repo_id, passport, db)

    if q:
        rows = db.exec(
            text(
                "SELECT url.user_id, u.email, url.role, url.created_at "
                "FROM user_repo_links url "
                "LEFT JOIN users u ON u.id = url.user_id "
                "WHERE url.repo_id = :repo_id "
                "  AND u.email ILIKE :q_pattern "
                "ORDER BY url.created_at ASC"
            ).bindparams(repo_id=repo_id, q_pattern=f"%{q}%")
        ).all()
    else:
        rows = db.exec(
            text(
                "SELECT url.user_id, u.email, url.role, url.created_at "
                "FROM user_repo_links url "
                "LEFT JOIN users u ON u.id = url.user_id "
                "WHERE url.repo_id = :repo_id "
                "ORDER BY url.created_at ASC"
            ).bindparams(repo_id=repo_id)
        ).all()

    return [
        MemberResponse(
            user_id=row.user_id,
            email=row.email,
            role=row.role,
            joined_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# PUT /v1/repos/{repo_id}/members/{target_user_id}/role  — Change role
# ---------------------------------------------------------------------------

@router.put(
    "/{repo_id}/members/{target_user_id}/role",
    response_model=RoleChangeResponse,
    status_code=status.HTTP_200_OK,
    summary="Change a member's role (admin only)",
)
def change_role(
    repo_id: uuid.UUID,
    target_user_id: str,
    payload: RoleChangeRequest,
    passport: TokenData = Security(verify_passport),
    db: Session = Depends(deps.get_db),
) -> RoleChangeResponse:
    _require_admin(repo_id, passport, db)

    if target_user_id == passport.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own role. Ask another admin.",
        )

    link = db.exec(
        select(UserRepoLink).where(
            UserRepoLink.repo_id == repo_id,
            UserRepoLink.user_id == target_user_id,
        )
    ).first()
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found.")

    old_role = link.role
    new_role = payload.role

    # Last-admin guard: cannot demote the last admin
    if old_role == RepoRole.admin and new_role != RepoRole.admin:
        if _count_admins(repo_id, db) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot demote the last admin. Promote another member to admin first.",
            )

    link.role = new_role
    db.add(link)
    db.commit()

    # Cascade: cancel commits + delete drafts when downgrading from author
    if old_role == RepoRole.author and new_role != RepoRole.author:
        try:
            workflow_client.cancel_member_commits(repo_id, target_user_id)
        except Exception as exc:
            log.error("role_change_cancel_commits_failed", error=str(exc))
        try:
            repo_client.delete_member_drafts(repo_id, target_user_id)
        except Exception as exc:
            log.error("role_change_delete_drafts_failed", error=str(exc))

    # SES notification (best-effort)
    repo = db.get(RepoHead, repo_id)
    repo_name = repo.repo_name if repo else str(repo_id)
    email = _get_user_email(target_user_id, db)
    try:
        send_role_changed_notification(
            recipient_email=email or "",
            repo_name=repo_name,
            old_role=old_role.value,
            new_role=new_role.value,
            from_email=settings.SES_FROM_EMAIL,
        )
    except Exception as exc:
        log.error("role_change_ses_failed", error=str(exc))

    log.info("member_role_changed", repo_id=str(repo_id), user_id=target_user_id, old=old_role.value, new=new_role.value)
    return RoleChangeResponse(user_id=target_user_id, role=new_role.value)


# ---------------------------------------------------------------------------
# DELETE /v1/repos/{repo_id}/members/{target_user_id}  — Remove member
# ---------------------------------------------------------------------------

@router.delete(
    "/{repo_id}/members/{target_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member from a repository (admin only)",
)
def remove_member(
    repo_id: uuid.UUID,
    target_user_id: str,
    passport: TokenData = Security(verify_passport),
    db: Session = Depends(deps.get_db),
) -> None:
    _require_admin(repo_id, passport, db)

    if target_user_id == passport.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove yourself. Ask another admin.",
        )

    link = db.exec(
        select(UserRepoLink).where(
            UserRepoLink.repo_id == repo_id,
            UserRepoLink.user_id == target_user_id,
        )
    ).first()
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found.")

    # Last-admin guard
    if link.role == RepoRole.admin:
        if _count_admins(repo_id, db) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot remove the last admin.",
            )

    # Capture email + repo_name BEFORE deleting the link
    email = _get_user_email(target_user_id, db)
    repo = db.get(RepoHead, repo_id)
    repo_name = repo.repo_name if repo else str(repo_id)

    db.delete(link)
    db.commit()

    # Best-effort cascade
    try:
        workflow_client.cancel_member_commits(repo_id, target_user_id)
    except Exception as exc:
        log.error("remove_cancel_commits_failed", error=str(exc))
    try:
        repo_client.delete_member_drafts(repo_id, target_user_id)
    except Exception as exc:
        log.error("remove_delete_drafts_failed", error=str(exc))

    # SQS cache invalidation (no-op if URL empty)
    publish_cache_invalidation(str(repo_id), target_user_id, settings.SQS_CACHE_INVALIDATION_QUEUE_URL)

    # SES notification (best-effort)
    try:
        send_removed_notification(
            recipient_email=email or "",
            repo_name=repo_name,
            from_email=settings.SES_FROM_EMAIL,
        )
    except Exception as exc:
        log.error("remove_ses_failed", error=str(exc))

    log.info("member_removed", repo_id=str(repo_id), user_id=target_user_id)
