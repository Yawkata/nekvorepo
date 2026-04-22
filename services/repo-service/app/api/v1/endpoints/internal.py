"""
Internal endpoints — reachable only within the cluster via Kubernetes NetworkPolicy.
NOT exposed through the ALB / API Gateway.

  POST   /v1/internal/sync-blobs                          — walk EFS, upload blobs, return hash map
  DELETE /v1/internal/drafts/{id}                         — idempotent EFS wipe (post-approval)
  POST   /v1/internal/cache/invalidate                    — evict role cache entry
  DELETE /v1/internal/repos/{id}/members/{uid}/drafts     — hard-delete all drafts + wipe EFS
"""
import hashlib
import uuid

import structlog
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, select

from shared.models.repo import Blob, Draft
from app.services.storage import StorageManager
from app.api import deps
from app.services import identity_client
from app.services.efs import EFSService

log = structlog.get_logger()
router = APIRouter()

# Module-level StorageManager — one S3 client, reused across requests
_storage = StorageManager()


# ---------------------------------------------------------------------------
# POST /v1/internal/sync-blobs
# Called by Workflow-Service after setting draft.status = committing.
# Walks EFS, uploads new blobs to S3, registers them in the blobs table.
# Does NOT wipe the EFS directory — caller sends a separate DELETE after the
# commit row is safely written to the database.
# ---------------------------------------------------------------------------

class SyncBlobsRequest(BaseModel):
    draft_id: uuid.UUID
    repo_id: uuid.UUID
    user_id: str


class SyncBlobsResponse(BaseModel):
    blobs: dict[str, str]  # { "relative/path": "sha256hex" }


@router.post(
    "/sync-blobs",
    response_model=SyncBlobsResponse,
    status_code=status.HTTP_200_OK,
    summary="Walk EFS, upload blobs to S3, return content hash map",
)
def sync_blobs(
    body: SyncBlobsRequest,
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
):
    """
    1. Walk the EFS draft directory, skipping .deleted markers.
    2. SHA-256 each real file.
    3. Upload to S3 (idempotent — skips existing keys).
    4. Upsert a Blob row (INSERT ... ON CONFLICT DO NOTHING).
    5. Return the { path: sha256hex } map for Workflow-Service to build the tree.

    The EFS directory is NOT wiped here. Workflow-Service sends
    DELETE /v1/internal/drafts/{draft_id} after the commit row is committed.
    """
    files = efs.list_files(body.user_id, str(body.repo_id), str(body.draft_id))

    blob_map: dict[str, str] = {}

    for file in files:
        raw = efs.read_file(body.user_id, str(body.repo_id), str(body.draft_id), file.path)
        content_hash = hashlib.sha256(raw).hexdigest()

        # Upload to S3 — no-op if key already exists
        content_type = "application/octet-stream" if file.is_binary else "text/plain"
        _storage.upload_blob(raw, content_hash, content_type)

        # Upsert blob row — ON CONFLICT DO NOTHING (hash is unique key)
        stmt = (
            pg_insert(Blob)
            .values(
                blob_hash=content_hash,
                size=file.size,
                content_type=content_type,
            )
            .on_conflict_do_nothing(index_elements=["blob_hash"])
        )
        db.exec(stmt)  # type: ignore[arg-type]

        blob_map[file.path] = content_hash

    db.commit()

    log.info(
        "sync_blobs_complete",
        draft_id=str(body.draft_id),
        repo_id=str(body.repo_id),
        file_count=len(blob_map),
    )
    return SyncBlobsResponse(blobs=blob_map)


# ---------------------------------------------------------------------------
# DELETE /v1/internal/drafts/{draft_id}
# Idempotent EFS wipe called by Workflow-Service after the commit row is
# committed.  Missing directory → 204 (already cleaned up).
# ---------------------------------------------------------------------------

@router.delete(
    "/drafts/{draft_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Idempotent EFS directory wipe (post-approval cleanup)",
)
def wipe_draft(
    draft_id: uuid.UUID,
    user_id: str,
    repo_id: uuid.UUID,
    efs: EFSService = Depends(deps.get_efs),
):
    """
    Remove the EFS directory for a draft after its commit has been approved.
    Idempotent — a missing directory is treated as success.
    """
    efs.delete_dir(user_id, str(repo_id), str(draft_id))
    log.info("draft_efs_wiped", draft_id=str(draft_id), repo_id=str(repo_id), user_id=user_id)


# ---------------------------------------------------------------------------
# Phase 9 hook — role cache invalidation
# ---------------------------------------------------------------------------

class CacheInvalidateRequest(BaseModel):
    repo_id: uuid.UUID
    user_id: str


@router.post(
    "/cache/invalidate",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalidate the role cache for a user/repo pair",
)
def invalidate_cache(body: CacheInvalidateRequest):
    """
    Evicts a single entry from the in-process role cache.
    Called by the Phase 9 SQS consumer after a membership change so that
    the removed user stops being served from cache within the current TTL window.

    Because each pod has its own in-memory cache, the SQS message must be
    fan-out to all running repo-service pods (Phase 9 wires this).
    """
    identity_client.invalidate(str(body.repo_id), body.user_id)
    log.info(
        "role_cache_invalidated",
        repo_id=str(body.repo_id),
        user_id=body.user_id,
    )


# ---------------------------------------------------------------------------
# DELETE /v1/internal/repos/{repo_id}/members/{user_id}/drafts
# Called by identity-service on member role change (from author) or removal.
# Hard-deletes all draft rows and wipes their EFS directories (best-effort).
# ---------------------------------------------------------------------------

@router.delete(
    "/repos/{repo_id}/members/{user_id}/drafts",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hard-delete all drafts for a user in a repo + best-effort EFS wipe",
)
def delete_member_drafts(
    repo_id: uuid.UUID,
    user_id: str,
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
) -> None:
    """
    Hard-deletes all Draft rows for user_id in repo_id and wipes their EFS
    directories (best-effort — a missing or unreadable directory does not abort).

    Called during:
    - Role change away from author (in-progress work becomes inaccessible)
    - Member removal (user loses all access)

    Idempotent — if there are no drafts the operation is a no-op (204 still).
    """
    drafts = db.exec(
        select(Draft).where(
            Draft.repo_id == repo_id,
            Draft.user_id == user_id,
        )
    ).all()

    for draft in drafts:
        try:
            efs.delete_dir(user_id, str(repo_id), str(draft.id))
        except Exception as exc:
            log.warning(
                "draft_efs_wipe_failed",
                draft_id=str(draft.id),
                repo_id=str(repo_id),
                error=str(exc),
            )
        db.delete(draft)

    db.commit()
    log.info(
        "member_drafts_deleted",
        repo_id=str(repo_id),
        user_id=user_id,
        count=len(drafts),
    )


# ---------------------------------------------------------------------------
# DELETE /v1/internal/repos/{repo_id}/drafts
# Phase 10 — called from workflow-service on repo deletion.
# Hard-deletes every draft for the repo and wipes the corresponding EFS
# directories (best-effort, idempotent).
# ---------------------------------------------------------------------------

@router.delete(
    "/repos/{repo_id}/drafts",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hard-delete all drafts + EFS dirs for a repo (phase 10 cascade)",
)
def delete_repo_drafts(
    repo_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
) -> None:
    """
    Walks every Draft row for the given repo, wipes its EFS directory, and
    hard-deletes the row.  Idempotent: a missing row or directory counts as
    success (204).  The EFS wipe is best-effort per-draft so that a single
    corrupted directory does not abort the entire cascade.

    This endpoint is internal-only (Kubernetes NetworkPolicy denies external
    traffic to /v1/internal/*).  It is called exactly once per repo deletion
    by workflow-service's DELETE /v1/repos/{id} coordinator.
    """
    drafts = db.exec(select(Draft).where(Draft.repo_id == repo_id)).all()

    for draft in drafts:
        try:
            efs.delete_dir(draft.user_id, str(repo_id), str(draft.id))
        except Exception as exc:
            log.warning(
                "draft_efs_wipe_failed",
                draft_id=str(draft.id),
                repo_id=str(repo_id),
                error=str(exc),
            )
        db.delete(draft)

    db.commit()
    log.info(
        "repo_drafts_deleted",
        repo_id=str(repo_id),
        count=len(drafts),
    )
