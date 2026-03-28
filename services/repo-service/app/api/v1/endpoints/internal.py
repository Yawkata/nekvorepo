"""
Internal endpoints — reachable only within the cluster via Kubernetes NetworkPolicy.
NOT exposed through the ALB / API Gateway.

These are called by Workflow-Service during the commit flow (Phase 5).

Phase 4 stubs:
  POST /v1/internal/sync-blobs    — Phase 5: walks EFS, uploads blobs to S3, wipes EFS
  DELETE /v1/internal/drafts/cleanup — Phase 5: SQS consumer for EFS directory cleanup

Phase 9:
  POST /v1/internal/cache/invalidate — invalidates the role cache for a user/repo pair
"""
import uuid

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.services import identity_client

log = structlog.get_logger()
router = APIRouter()


# ---------------------------------------------------------------------------
# Phase 5 stubs
# ---------------------------------------------------------------------------

class SyncBlobsRequest(BaseModel):
    draft_id: uuid.UUID
    repo_id: uuid.UUID
    user_id: str


@router.post(
    "/sync-blobs",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="[Phase 5] Walk EFS, upload blobs to S3, wipe EFS",
    include_in_schema=True,
)
def sync_blobs(body: SyncBlobsRequest):
    """
    Phase 5 implementation will:
      1. Verify draft status == committing (409 otherwise).
      2. Walk EFS at /mnt/efs/drafts/{user_id}/{repo_id}/{draft_id}/.
      3. Compute SHA-256 per real file, skipping .deleted markers.
      4. For each hash not already in the blobs table, upload the blob to S3.
      5. Insert missing rows into blobs and repo_tree_entries.
      6. Wipe the EFS directory after a successful transfer.
      7. Return the content_hashes map so Workflow-Service can build the commit tree.
    """
    raise HTTPException(
        status_code=501,
        detail="sync-blobs is implemented in Phase 5.",
    )


class CleanupRequest(BaseModel):
    user_id: str
    repo_id: uuid.UUID
    draft_id: uuid.UUID


@router.delete(
    "/drafts/cleanup",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="[Phase 5] Idempotent EFS directory cleanup (SQS consumer)",
    include_in_schema=True,
)
def cleanup_draft(body: CleanupRequest):
    """
    Phase 5 implementation will:
      - Be driven by a KEDA-scaled SQS consumer running on Spot instances.
      - Delete /mnt/efs/drafts/{user_id}/{repo_id}/{draft_id}/ idempotently
        (missing directory → 204, not an error).
      - Handle the case where the directory is partially written (pod crash).
    """
    raise HTTPException(
        status_code=501,
        detail="EFS cleanup via SQS is implemented in Phase 5.",
    )


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
