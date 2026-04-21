"""
Invite lifecycle endpoints.

  POST   /v1/repos/{repo_id}/invites                      — Send invite (admin)
  GET    /v1/repos/{repo_id}/invites                      — List pending invites (admin)
  POST   /v1/repos/{repo_id}/invites/{token_id}/resend    — Resend invite (admin)
  POST   /v1/repos/{repo_id}/invites/{token_id}/revoke    — Revoke invite (admin)
  POST   /v1/repos/{repo_id}/invites/{token_id}/accept    — Accept invite (any JWT)

All endpoints require a Passport JWT.  Send/list/resend/revoke require admin role.
Accept requires any valid JWT — the caller's email must match the invited_email.
"""
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Security, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlmodel import Session, select

from shared.constants import RepoRole
from shared.models.identity import UserRepoLink
from shared.models.invite import InviteToken
from shared.models.workflow import RepoHead
from shared.schemas.auth import TokenData
from shared.security import verify_passport
from app.api import deps
from app.core.config import settings
from app.services.notifications import send_invite_notification

log = structlog.get_logger()
router = APIRouter()

_72H = timedelta(hours=72)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_admin(repo_id: uuid.UUID, passport: TokenData, db: Session) -> RepoHead:
    """Verify caller is admin of repo_id. Returns the RepoHead. Raises 403/404."""
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


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class InviteRequest(BaseModel):
    email: EmailStr
    role: RepoRole


class InviteResponse(BaseModel):
    token_id: uuid.UUID
    invited_email: str
    role: str
    created_at: datetime
    expires_at: datetime


class ResendResponse(BaseModel):
    token_id: uuid.UUID
    expires_at: datetime


class AcceptResponse(BaseModel):
    repo_id: str
    role: str


# ---------------------------------------------------------------------------
# POST /v1/repos/{repo_id}/invites  — Send invite
# ---------------------------------------------------------------------------

@router.post(
    "/{repo_id}/invites",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send a repository invite (admin only)",
)
def send_invite(
    repo_id: uuid.UUID,
    payload: InviteRequest,
    passport: TokenData = Security(verify_passport),
    db: Session = Depends(deps.get_db),
) -> InviteResponse:
    repo = _require_admin(repo_id, passport, db)

    # Check if invited email already belongs to a member
    existing_member = db.exec(
        text(
            "SELECT url.id FROM user_repo_links url "
            "JOIN users u ON u.id = url.user_id "
            "WHERE url.repo_id = :repo_id AND LOWER(u.email) = LOWER(:email)"
        ).bindparams(repo_id=repo_id, email=payload.email)
    ).first()
    if existing_member is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This user is already a member of this repository.",
        )

    # Check for an existing active (non-expired, non-consumed) invite
    now = _now()
    existing_invite = db.exec(
        select(InviteToken).where(
            InviteToken.repo_id == repo_id,
            InviteToken.invited_email == payload.email.lower(),
            InviteToken.expires_at > now,
            InviteToken.consumed_at == None,  # noqa: E711
        )
    ).first()
    if existing_invite is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pending invite already exists for this email. Use Resend to generate a new one.",
        )

    token = InviteToken(
        repo_id=repo_id,
        invited_email=payload.email.lower(),
        role=payload.role,
        expires_at=now + _72H,
    )
    db.add(token)
    db.commit()
    db.refresh(token)

    accept_url = f"{settings.INVITE_ACCEPT_BASE_URL}/invites/{token.id}/accept"
    try:
        send_invite_notification(
            recipient_email=payload.email,
            repo_name=repo.repo_name,
            role=payload.role.value,
            accept_url=accept_url,
            from_email=settings.SES_FROM_EMAIL,
        )
    except Exception as exc:
        log.error("invite_ses_failed", error=str(exc))

    log.info("invite_sent", repo_id=str(repo_id), invited_email=payload.email, role=payload.role.value)
    return InviteResponse(
        token_id=token.id,
        invited_email=token.invited_email,
        role=token.role.value,
        created_at=token.created_at,
        expires_at=token.expires_at,
    )


# ---------------------------------------------------------------------------
# GET /v1/repos/{repo_id}/invites  — List pending invites
# ---------------------------------------------------------------------------

@router.get(
    "/{repo_id}/invites",
    response_model=list[InviteResponse],
    status_code=status.HTTP_200_OK,
    summary="List pending invites for a repository (admin only)",
)
def list_invites(
    repo_id: uuid.UUID,
    passport: TokenData = Security(verify_passport),
    db: Session = Depends(deps.get_db),
) -> list[InviteResponse]:
    _require_admin(repo_id, passport, db)

    now = _now()
    tokens = db.exec(
        select(InviteToken).where(
            InviteToken.repo_id == repo_id,
            InviteToken.consumed_at == None,  # noqa: E711
            InviteToken.expires_at > now,
        ).order_by(InviteToken.created_at.desc())
    ).all()

    return [
        InviteResponse(
            token_id=t.id,
            invited_email=t.invited_email,
            role=t.role.value,
            created_at=t.created_at,
            expires_at=t.expires_at,
        )
        for t in tokens
    ]


# ---------------------------------------------------------------------------
# POST /v1/repos/{repo_id}/invites/{token_id}/resend  — Resend invite
# ---------------------------------------------------------------------------

@router.post(
    "/{repo_id}/invites/{token_id}/resend",
    response_model=ResendResponse,
    status_code=status.HTTP_200_OK,
    summary="Resend an invite by expiring the old token and issuing a fresh one (admin only)",
)
def resend_invite(
    repo_id: uuid.UUID,
    token_id: uuid.UUID,
    passport: TokenData = Security(verify_passport),
    db: Session = Depends(deps.get_db),
) -> ResendResponse:
    repo = _require_admin(repo_id, passport, db)

    token = db.exec(
        select(InviteToken).where(
            InviteToken.id == token_id,
            InviteToken.repo_id == repo_id,
        )
    ).first()
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found.")
    if token.consumed_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has already been accepted.")
    if token.expires_at <= _now():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has already expired.")

    # Expire the old token so it disappears from the pending list
    token.expires_at = _now()
    db.add(token)

    # Create fresh token with same email + role
    new_token = InviteToken(
        repo_id=repo_id,
        invited_email=token.invited_email,
        role=token.role,
        expires_at=_now() + _72H,
    )
    db.add(new_token)
    db.commit()
    db.refresh(new_token)

    accept_url = f"{settings.INVITE_ACCEPT_BASE_URL}/invites/{new_token.id}/accept"
    try:
        send_invite_notification(
            recipient_email=token.invited_email,
            repo_name=repo.repo_name,
            role=token.role.value,
            accept_url=accept_url,
            from_email=settings.SES_FROM_EMAIL,
        )
    except Exception as exc:
        log.error("resend_ses_failed", error=str(exc))

    log.info("invite_resent", repo_id=str(repo_id), new_token_id=str(new_token.id))
    return ResendResponse(token_id=new_token.id, expires_at=new_token.expires_at)


# ---------------------------------------------------------------------------
# POST /v1/repos/{repo_id}/invites/{token_id}/revoke  — Revoke invite
# ---------------------------------------------------------------------------

@router.post(
    "/{repo_id}/invites/{token_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a pending invite (admin only)",
)
def revoke_invite(
    repo_id: uuid.UUID,
    token_id: uuid.UUID,
    passport: TokenData = Security(verify_passport),
    db: Session = Depends(deps.get_db),
) -> None:
    _require_admin(repo_id, passport, db)

    token = db.exec(
        select(InviteToken).where(
            InviteToken.id == token_id,
            InviteToken.repo_id == repo_id,
        )
    ).first()
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found.")
    if token.consumed_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Cannot revoke a consumed invite.")

    token.expires_at = _now()
    db.add(token)
    db.commit()
    log.info("invite_revoked", repo_id=str(repo_id), token_id=str(token_id))


# ---------------------------------------------------------------------------
# POST /v1/repos/{repo_id}/invites/{token_id}/accept  — Accept invite
# ---------------------------------------------------------------------------

@router.post(
    "/{repo_id}/invites/{token_id}/accept",
    response_model=AcceptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Accept a repository invite (requires Passport JWT; email must match invite)",
)
def accept_invite(
    repo_id: uuid.UUID,
    token_id: uuid.UUID,
    passport: TokenData = Security(verify_passport),
    db: Session = Depends(deps.get_db),
) -> AcceptResponse:
    token = db.exec(
        select(InviteToken).where(
            InviteToken.id == token_id,
            InviteToken.repo_id == repo_id,
        )
    ).first()
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found.")

    # Expiry check BEFORE consumed check (per spec ordering)
    if token.expires_at.replace(tzinfo=timezone.utc) <= _now():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This invite has expired. Please ask the repository admin to resend it.",
        )
    if token.consumed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This invite has already been accepted.",
        )

    # Verify the JWT email matches the invited email (case-insensitive)
    if passport.email.lower() != token.invited_email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This invite was not sent to your email address.",
        )

    # Atomically set consumed_at — race-safe via raw SQL rowcount check
    result = db.exec(  # type: ignore[call-overload]
        text(
            "UPDATE invite_tokens SET consumed_at = NOW() "
            "WHERE id = :id AND repo_id = :repo_id AND consumed_at IS NULL"
        ).bindparams(id=token_id, repo_id=repo_id)
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This invite has already been accepted.",
        )

    # Upsert caller into users table so member list can show their email
    try:
        db.exec(  # type: ignore[call-overload]
            text(
                "INSERT INTO users (id, email) VALUES (:id, :email) "
                "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email"
            ).bindparams(id=passport.user_id, email=passport.email)
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        log.error("accept_user_upsert_failed", user_id=passport.user_id, error=str(exc))

    # Guard against duplicate membership (consumed_at is already set — don't un-consume)
    existing = db.exec(
        select(UserRepoLink).where(
            UserRepoLink.repo_id == repo_id,
            UserRepoLink.user_id == passport.user_id,
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already a member of this repository.",
        )

    link = UserRepoLink(repo_id=repo_id, user_id=passport.user_id, role=token.role)
    db.add(link)
    db.commit()

    log.info(
        "invite_accepted",
        repo_id=str(repo_id),
        user_id=passport.user_id,
        role=token.role.value,
    )
    return AcceptResponse(repo_id=str(repo_id), role=token.role.value)
