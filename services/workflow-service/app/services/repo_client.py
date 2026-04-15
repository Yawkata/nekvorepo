"""
HTTP client for calling repo-service internal endpoints.

Lifecycle: call setup() in the FastAPI lifespan startup and teardown() in
shutdown. All callers receive a validated client; attempting to use the client
before setup() raises RuntimeError immediately.

Retry policy: 3 attempts, exponential backoff, 502 on exhaustion.
"""
import uuid
import structlog
import httpx
from fastapi import HTTPException
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError,
)

log = structlog.get_logger()

_client: httpx.Client | None = None


# ---------------------------------------------------------------------------
# Lifecycle management (called from app lifespan)
# ---------------------------------------------------------------------------

def _outbound_headers() -> dict[str, str]:
    """Build headers for outbound requests, propagating the correlation ID."""
    hdrs = {"Content-Type": "application/json"}
    ctx = structlog.contextvars.get_contextvars()
    if cid := ctx.get("correlation_id"):
        hdrs["X-Correlation-ID"] = cid
    return hdrs


def setup(base_url: str) -> None:
    """Initialize the shared HTTP client. Must be called once at startup."""
    global _client
    _client = httpx.Client(
        base_url=base_url,
        # sync-blobs walks EFS + uploads to S3 — allow up to 60 s read timeout
        timeout=httpx.Timeout(connect=2.0, read=60.0, write=10.0, pool=1.0),
        headers={"Content-Type": "application/json"},
    )
    log.info("repo_client_ready", base_url=base_url)


def teardown() -> None:
    """Close the shared HTTP client. Call at shutdown."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        log.info("repo_client_closed")


def _get() -> httpx.Client:
    if _client is None:
        raise RuntimeError(
            "Repo client is not initialized. "
            "Ensure repo_client.setup() is called during app startup."
        )
    return _client


# ---------------------------------------------------------------------------
# Internal retry decorator
# Retries on transient network errors only — not on 4xx/5xx HTTP responses.
# ---------------------------------------------------------------------------

_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=5.0),
    retry=retry_if_exception_type(
        (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)
    ),
    reraise=False,
)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def sync_blobs(draft_id: uuid.UUID, repo_id: uuid.UUID, user_id: str) -> dict[str, str]:
    """
    Ask repo-service to walk the EFS draft directory, upload blobs to S3,
    and return the { relative_path: sha256hex } map.

    Raises HTTPException(502) on transient failure after retries.
    """
    @_retry
    def _call() -> dict[str, str]:
        resp = _get().post(
            "/v1/internal/sync-blobs",
            json={
                "draft_id": str(draft_id),
                "repo_id": str(repo_id),
                "user_id": user_id,
            },
            headers=_outbound_headers(),
        )
        resp.raise_for_status()
        return resp.json()["blobs"]

    try:
        return _call()
    except RetryError:
        log.error("repo_sync_blobs_exhausted_retries", draft_id=str(draft_id), repo_id=str(repo_id))
        raise HTTPException(status_code=502, detail="Repo service unavailable during blob sync. Please try again.")
    except httpx.HTTPStatusError as exc:
        log.error(
            "repo_sync_blobs_failed",
            draft_id=str(draft_id),
            repo_id=str(repo_id),
            status_code=exc.response.status_code,
        )
        raise HTTPException(status_code=502, detail="Blob sync failed in repo-service.")
    except httpx.RequestError as exc:
        log.error("repo_service_unreachable", error=str(exc))
        raise HTTPException(status_code=502, detail="Repo service is unreachable.")


def wipe_draft(draft_id: uuid.UUID, repo_id: uuid.UUID, user_id: str) -> None:
    """
    Ask repo-service to delete the EFS draft directory after approval.
    Best-effort — logs but does NOT raise on failure (wipe is idempotent and
    can be retried manually; the commit is already committed).
    """
    try:
        resp = _get().delete(
            f"/v1/internal/drafts/{draft_id}",
            params={"repo_id": str(repo_id), "user_id": user_id},
            headers=_outbound_headers(),
        )
        resp.raise_for_status()
        log.info("draft_efs_wiped", draft_id=str(draft_id), repo_id=str(repo_id))
    except Exception as exc:
        log.warning(
            "draft_efs_wipe_failed",
            draft_id=str(draft_id),
            repo_id=str(repo_id),
            error=str(exc),
        )
