"""
Internal endpoints — reachable only within the cluster via Kubernetes NetworkPolicy.
NOT exposed through the ALB.

These are called by Repo-Service and Workflow-Service to manage memberships and
perform role lookups with a 60-second TTL on the caller's side.
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
from pydantic import BaseModel
from shared.models.identity import UserRepoLink
from shared.models.workflow import RepoHead
from shared.constants import RepoRole
from app.api import deps

router = APIRouter()

_404 = {404: {"description": "Membership not found"}}
_409 = {409: {"description": "User is already a member of this repository"}}


class MembershipCreate(BaseModel):
    repo_id: uuid.UUID
    user_id: str
    role: RepoRole


class RoleUpdate(BaseModel):
    role: RepoRole


class MembershipResponse(BaseModel):
    id: int
    repo_id: str
    user_id: str
    role: RepoRole


class RoleResponse(BaseModel):
    repo_id: str
    user_id: str
    role: RepoRole


# ---------------------------------------------------------------------------
# POST /v1/internal/memberships
# Assigns a role on repo creation or invite acceptance.
# ---------------------------------------------------------------------------
@router.post("/memberships", status_code=status.HTTP_201_CREATED, response_model=MembershipResponse, responses=_409)
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
    return {
        "id": link.id,
        "repo_id": str(link.repo_id),
        "user_id": link.user_id,
        "role": link.role,
    }


# ---------------------------------------------------------------------------
# GET /v1/internal/repos/{repo_id}/role?user_id=...
# Returns the role for a specific user/repo pair.
# Callers cache the result for 60 seconds.
# ---------------------------------------------------------------------------
@router.get("/repos/{repo_id}/role", response_model=RoleResponse, responses=_404)
def get_member_role(
    repo_id: uuid.UUID,
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
    return {"repo_id": str(repo_id), "user_id": user_id, "role": link.role}


# ---------------------------------------------------------------------------
# PUT /v1/internal/repos/{repo_id}/members/{user_id}/role
# Updates Table 1. 60-second TTL provides bounded eventual consistency.
# ---------------------------------------------------------------------------
@router.put("/repos/{repo_id}/members/{user_id}/role", status_code=status.HTTP_200_OK, response_model=RoleResponse, responses=_404)
def update_member_role(
    repo_id: uuid.UUID,
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
    return {"repo_id": str(repo_id), "user_id": user_id, "role": link.role}


# ---------------------------------------------------------------------------
# DELETE /v1/internal/repos/{repo_id}/members/{user_id}
# Deletes the Table 1 row.
# ---------------------------------------------------------------------------
@router.delete(
    "/repos/{repo_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_404, 403: {"description": "Cannot remove the repository owner"}},
)
def delete_membership(
    repo_id: uuid.UUID,
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

    # Prevent deleting the repository owner's membership to avoid orphaned repos.
    # A repo must always have at least one member (its owner).
    repo = db.exec(select(RepoHead).where(RepoHead.id == repo_id)).first()
    if repo and repo.owner_id == user_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot remove the repository owner. Transfer ownership or delete the repository first.",
        )

    db.delete(link)
    db.commit()
