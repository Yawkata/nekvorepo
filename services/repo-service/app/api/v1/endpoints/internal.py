"""
Internal endpoints — reachable only within the cluster via Kubernetes NetworkPolicy.
NOT exposed through the ALB / API Gateway.

Called by Workflow-Service during the commit flow:
  POST /v1/internal/sync-blobs        — walk EFS, upload blobs to S3, return hash map
  DELETE /v1/internal/drafts/{id}     — idempotent EFS directory wipe (post-approval)

Phase 9:
  POST /v1/internal/cache/invalidate  — invalidates the role cache for a user/repo pair
"""
import hashlib
import uuid

import structlog
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session

from shared.models.repo import Blob
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
