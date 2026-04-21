"""
HTTP client for calling workflow-service internal endpoints from identity-service.

Used during member role change and removal to cancel pending commits for the
affected user so that the commit queue stays consistent.

Lifecycle: call setup() in the FastAPI lifespan startup and teardown() on shutdown.
All callers receive a validated client; attempting to call before setup() raises
RuntimeError.

Operations are best-effort — errors are logged and swallowed.  The DB change
(role update / member deletion) is already committed before these calls; a
transient network failure does not roll it back.
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
        timeout=httpx.Timeout(connect=2.0, read=10.0, write=5.0, pool=1.0),
    )
    log.info("workflow_client_ready", base_url=base_url)


def teardown() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
        log.info("workflow_client_closed")


def _get() -> httpx.Client:
    if _client is None:
        raise RuntimeError(
            "Workflow client is not initialized. "
            "Ensure workflow_client.setup() is called during app startup."
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


def cancel_member_commits(repo_id: uuid.UUID, user_id: str) -> None:
    """
    Ask workflow-service to cancel all pending commits for user_id in repo_id.
    Best-effort — logs but does NOT raise on failure.
    """
    @_retry
    def _call() -> None:
        resp = _get().delete(
            f"/v1/internal/repos/{repo_id}/members/{user_id}/commits",
            headers=_outbound_headers(),
        )
        resp.raise_for_status()

    try:
        _call()
        log.info("member_commits_cancelled_via_client", repo_id=str(repo_id), user_id=user_id)
    except Exception as exc:
        log.warning(
            "member_commits_cancel_failed",
            repo_id=str(repo_id),
            user_id=user_id,
            error=str(exc),
        )
