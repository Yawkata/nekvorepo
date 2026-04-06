"""
Internal endpoints — reachable only within the cluster via Kubernetes NetworkPolicy.
NOT exposed through the ALB.

  POST /v1/internal/cache/invalidate  — evict a role cache entry (Phase 9 SQS consumer)

Both workflow-service and repo-service maintain independent in-process role caches
(60-second TTL). This endpoint mirrors the one on repo-service so that forced cache
invalidation on membership changes propagates to both services, not just one.
"""
import uuid

import structlog
from fastapi import APIRouter, status
from pydantic import BaseModel

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
