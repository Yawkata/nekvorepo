"""
HTTP client for calling repo-service internal endpoints from identity-service.

Used during member role change and removal to hard-delete draft rows and wipe
EFS directories for the affected user.

Same lifecycle and retry pattern as workflow_client.py — best-effort operations
that log and swallow errors without blocking the primary DB change.
"""
import uuid
import structlog
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

log = structlog.get_logger()

_client: httpx.Client | None = None


def _outbound_headers() -> dict[str, str]:
    hdrs = {"Content-Type": "application/json"}
    ctx = structlog.contextvars.get_contextvars()
    if cid := ctx.get("correlation_id"):
        hdrs["X-Correlation-ID"] = cid
    return hdrs


def setup(base_url: str) -> None:
    global _client
    _client = httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(connect=2.0, read=30.0, write=5.0, pool=1.0),
    )
    log.info("repo_client_ready", base_url=base_url)


def teardown() -> None:
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


_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=5.0),
    retry=retry_if_exception_type(
        (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)
    ),
    reraise=True,
)


def delete_member_drafts(repo_id: uuid.UUID, user_id: str) -> None:
    """
    Ask repo-service to hard-delete all drafts for user_id in repo_id and
    wipe their EFS directories.  Best-effort — logs but does NOT raise on failure.
    """
    @_retry
    def _call() -> None:
        resp = _get().delete(
            f"/v1/internal/repos/{repo_id}/members/{user_id}/drafts",
            headers=_outbound_headers(),
        )
        resp.raise_for_status()

    try:
        _call()
        log.info("member_drafts_deleted_via_client", repo_id=str(repo_id), user_id=user_id)
    except Exception as exc:
        log.warning(
            "member_drafts_delete_failed",
            repo_id=str(repo_id),
            user_id=user_id,
            error=str(exc),
        )
