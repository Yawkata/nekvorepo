"""
HTTP client for calling identity-service internal endpoints.

Lifecycle: call setup() in the FastAPI lifespan startup and teardown() in
shutdown. All callers receive a validated client; attempting to use the client
before setup() raises RuntimeError immediately.

Retry policy: Per spec, all inter-service calls use tenacity with 3 attempts,
exponential backoff, and a 503 response on exhaustion.
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
from shared.constants import RepoRole

log = structlog.get_logger()

_client: httpx.Client | None = None

# ---------------------------------------------------------------------------
# Lifecycle management (called from app lifespan)
# ---------------------------------------------------------------------------

def setup(base_url: str) -> None:
    """Initialize the shared HTTP client. Must be called once at startup."""
    global _client
    _client = httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=1.0),
        headers={"Content-Type": "application/json"},
    )
    log.info("identity_client_ready", base_url=base_url)


def teardown() -> None:
    """Close the shared HTTP client. Call at shutdown."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        log.info("identity_client_closed")


def _get() -> httpx.Client:
    if _client is None:
        raise RuntimeError(
            "Identity client is not initialized. "
            "Ensure identity_client.setup() is called during app startup."
        )
    return _client


# ---------------------------------------------------------------------------
# Internal retry decorator
# Retries on transient network errors only — not on 4xx HTTP errors.
# 3 attempts: immediate, ~0.5s, ~1.5s → total max ~2s of wait.
# ---------------------------------------------------------------------------

def _is_transient(exc: Exception) -> bool:
    """True for connection/timeout errors; False for HTTP 4xx/5xx responses."""
    return isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError))


_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=5.0),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)),
    reraise=False,
)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def create_membership(repo_id: uuid.UUID, user_id: str, role: RepoRole) -> None:
    """
    Registers a user-repo membership in identity-service.
    Raises HTTPException(502) on transient failure after retries,
    or HTTPException(4xx) on application-level errors.
    """
    @_retry
    def _call() -> None:
        resp = _get().post(
            "/v1/internal/memberships",
            json={"repo_id": str(repo_id), "user_id": user_id, "role": role.value},
        )
        resp.raise_for_status()

    try:
        _call()
    except RetryError:
        log.error("identity_create_membership_exhausted_retries", repo_id=str(repo_id), user_id=user_id)
        raise HTTPException(status_code=503, detail="Identity service unavailable. Please try again.")
    except httpx.HTTPStatusError as exc:
        log.error(
            "identity_create_membership_failed",
            repo_id=str(repo_id),
            user_id=user_id,
            status_code=exc.response.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail="Failed to register repo membership in identity-service.",
        )
    except httpx.RequestError as exc:
        log.error("identity_service_unreachable", repo_id=str(repo_id), error=str(exc))
        raise HTTPException(status_code=502, detail="Identity service is unreachable.")


def delete_membership(repo_id: uuid.UUID, user_id: str) -> None:
    """
    Best-effort compensating action — deletes a membership created earlier in
    the same request. Logs but does NOT raise on failure, since the original
    error is already being propagated and double-faulting would swallow it.
    """
    try:
        resp = _get().delete(f"/v1/internal/repos/{repo_id}/members/{user_id}")
        resp.raise_for_status()
    except Exception as exc:
        log.warning(
            "identity_delete_membership_compensation_failed",
            repo_id=str(repo_id),
            user_id=user_id,
            error=str(exc),
        )
