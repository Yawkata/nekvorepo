"""
Internal endpoints — reachable only within the cluster via Kubernetes NetworkPolicy.
NOT exposed through the ALB.

  POST   /v1/internal/cache/invalidate                    — evict a role cache entry
  DELETE /v1/internal/repos/{id}/members/{uid}/commits    — cancel pending commits on removal
"""
import uuid

import structlog
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlmodel import Session, text

from app.api import deps
from app.services import identity_client

log = structlog.get_logger()
router = APIRouter()


class CacheInvalidateRequest(BaseModel):
    repo_id: uuid.UUID
    user_id: str


@router.post(
    "/cache/invalidate",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalidate the role cache for a user/repo pair",
)
def invalidate_cache(body: CacheInvalidateRequest) -> None:
    """
    Evicts a single entry from workflow-service's in-process role cache.

    Called by the Phase 9 SQS consumer after a membership change so that the
    affected user's role resolves correctly on the next request rather than
    waiting for the 60-second TTL to expire.

    Because each pod has its own in-memory cache, the SQS fan-out must target
    all running workflow-service pods (Phase 9 wires this, same as repo-service).
    """
    identity_client.invalidate(str(body.repo_id), body.user_id)
    log.info(
        "role_cache_invalidated",
        repo_id=str(body.repo_id),
        user_id=body.user_id,
    )


# ---------------------------------------------------------------------------
# DELETE /v1/internal/repos/{repo_id}/members/{user_id}/commits
# Called by identity-service on member role change (from author) or removal.
# Cancels all pending commits for the given user+repo pair.
# ---------------------------------------------------------------------------

@router.delete(
    "/repos/{repo_id}/members/{user_id}/commits",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel all pending commits for a user in a repo",
)
def cancel_member_commits(
    repo_id: uuid.UUID,
    user_id: str,
    db: Session = Depends(deps.get_db),
) -> None:
    """
    Sets status = 'cancelled' on every pending commit owned by user_id in repo_id.

    Called during:
    - Role change away from author (pending drafts/commits no longer valid)
    - Member removal (user loses all access)

    Idempotent — if there are no pending commits the UPDATE affects 0 rows (204 still).
    """
    result = db.exec(  # type: ignore[call-overload]
        text(
            "UPDATE repo_commits SET status = 'cancelled' "
            "WHERE repo_id = :repo_id AND owner_id = :user_id AND status = 'pending'"
        ).bindparams(repo_id=repo_id, user_id=user_id)
    )
    cancelled = result.rowcount
    db.commit()
    log.info(
        "member_commits_cancelled",
        repo_id=str(repo_id),
        user_id=user_id,
        count=cancelled,
    )


# ---------------------------------------------------------------------------
# DELETE /v1/internal/repos/{repo_id}
# Called by identity-service's phase-10 repo deletion cascade.  Workflow-service
# owns repo_commits and is the only service with DELETE privilege on that
# table (migration 010); identity-service therefore delegates via this endpoint
# rather than reaching into a table it does not own.
# ---------------------------------------------------------------------------

@router.delete(
    "/repos/{repo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hard-delete every commit row for a repo (phase 10 cascade)",
)
def delete_repo_commits(
    repo_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
) -> None:
    """
    Workflow-owned slice of the repo deletion cascade:

      1. Flip any still-open commits (pending, sibling_rejected) to 'cancelled'
         so any concurrent reviewer UI observes a terminal status before the
         row disappears.
      2. Hard-delete every repo_commits row for the repo.

    Tree rows (repo_tree_roots, repo_tree_entries) are intentionally preserved:
    they are content-addressed and may be referenced by commits in other repos.

    Idempotent: a second call on an already-empty repo affects 0 rows (204).
    """
    db.exec(  # type: ignore[call-overload]
        text(
            "UPDATE repo_commits SET status = 'cancelled' "
            "WHERE repo_id = :repo_id "
            "  AND status IN ('pending', 'sibling_rejected')"
        ).bindparams(repo_id=repo_id)
    )
    result = db.exec(  # type: ignore[call-overload]
        text("DELETE FROM repo_commits WHERE repo_id = :repo_id").bindparams(
            repo_id=repo_id
        )
    )
    deleted = result.rowcount
    db.commit()
    log.info("repo_commits_deleted", repo_id=str(repo_id), count=deleted)
