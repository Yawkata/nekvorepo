"""
Internal endpoints — reachable only within the cluster via Kubernetes NetworkPolicy.
NOT exposed through the ALB.

These are called by Repo-Service and Workflow-Service to manage memberships and
perform role lookups with a 60-second TTL on the caller's side.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from pydantic import BaseModel
from shared.models.identity import UserRepoLink
from shared.constants import RepoRole
from app.api import deps

router = APIRouter()


class MembershipCreate(BaseModel):
    repo_id: int
    user_id: str
    role: RepoRole


class RoleUpdate(BaseModel):
    role: RepoRole


# ---------------------------------------------------------------------------
# POST /v1/internal/memberships
# Assigns a role on repo creation or invite acceptance.
# ---------------------------------------------------------------------------
@router.post("/memberships", status_code=status.HTTP_201_CREATED)
def create_membership(
    payload: MembershipCreate,
    db: Session = Depends(deps.get_db),
):
    existing = db.exec(
        select(UserRepoLink).where(
            UserRepoLink.repo_id == payload.repo_id,
            UserRepoLink.user_id == payload.user_id,
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="This user is already a member of this repository.",
        )
    link = UserRepoLink(
        repo_id=payload.repo_id,
        user_id=payload.user_id,
        role=payload.role,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return {"id": link.id, "repo_id": link.repo_id, "user_id": link.user_id, "role": link.role}


# ---------------------------------------------------------------------------
# GET /v1/internal/repos/{repo_id}/role?user_id=...
# Returns the role for a specific user/repo pair.
# Callers cache the result for 60 seconds.
# ---------------------------------------------------------------------------
@router.get("/repos/{repo_id}/role")
def get_member_role(
    repo_id: int,
    user_id: str,
    db: Session = Depends(deps.get_db),
):
    link = db.exec(
        select(UserRepoLink).where(
            UserRepoLink.repo_id == repo_id,
            UserRepoLink.user_id == user_id,
        )
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Membership not found.")
    return {"repo_id": repo_id, "user_id": user_id, "role": link.role}


# ---------------------------------------------------------------------------
# PUT /v1/internal/repos/{repo_id}/members/{user_id}/role
# Updates Table 1. 60-second TTL provides bounded eventual consistency.
# ---------------------------------------------------------------------------
@router.put("/repos/{repo_id}/members/{user_id}/role", status_code=status.HTTP_200_OK)
def update_member_role(
    repo_id: int,
    user_id: str,
    payload: RoleUpdate,
    db: Session = Depends(deps.get_db),
):
    link = db.exec(
        select(UserRepoLink).where(
            UserRepoLink.repo_id == repo_id,
            UserRepoLink.user_id == user_id,
        )
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Membership not found.")
    link.role = payload.role
    db.add(link)
    db.commit()
    return {"repo_id": repo_id, "user_id": user_id, "role": link.role}


# ---------------------------------------------------------------------------
# DELETE /v1/internal/repos/{repo_id}/members/{user_id}
# Deletes the Table 1 row.
# ---------------------------------------------------------------------------
@router.delete("/repos/{repo_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_membership(
    repo_id: int,
    user_id: str,
    db: Session = Depends(deps.get_db),
):
    link = db.exec(
        select(UserRepoLink).where(
            UserRepoLink.repo_id == repo_id,
            UserRepoLink.user_id == user_id,
        )
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Membership not found.")
    db.delete(link)
    db.commit()
