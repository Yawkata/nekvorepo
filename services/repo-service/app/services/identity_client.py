"""
HTTP client for identity-service internal endpoints.

Responsibilities:
  - Resolve a user's role for a given repo (GET /v1/internal/repos/{id}/role).
  - Cache role results per (repo_id, user_id) with a 60-second TTL, matching the
    spec's bounded-eventual-consistency guarantee.
  - Retry only on transient network errors (connect/timeout/protocol); propagate
    application-level errors (4xx) immediately.

Thread-safety:
  - The cache dict is protected by a threading.Lock so it is safe when uvicorn
    runs with --workers > 1 or when a thread pool is used.
  - Each process has its own in-memory cache; that is acceptable because the spec
    allows up to 60 seconds of stale role data across pods.
"""
import time
import threading
import uuid

import httpx
import structlog
from fastapi import HTTPException
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------

_client: httpx.Client | None = None


def setup(base_url: str) -> None:
    """Initialise the shared HTTP client.  Called once at application startup."""
    global _client
    _client = httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=1.0),
        headers={"Content-Type": "application/json"},
    )
    log.info("identity_client_ready", base_url=base_url)


def teardown() -> None:
    """Close the HTTP client cleanly.  Called at application shutdown."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


def _get() -> httpx.Client:
    if _client is None:
        raise RuntimeError(
            "Identity client has not been initialised. Call setup() at startup."
        )
    return _client


# ---------------------------------------------------------------------------
# Role cache
# ---------------------------------------------------------------------------

# Maps (repo_id_str, user_id) → (role_or_None, expiry_monotonic)
_cache: dict[tuple[str, str], tuple[str | None, float]] = {}
_cache_lock = threading.Lock()


def _cache_get(repo_id: str, user_id: str) -> tuple[bool, str | None]:
    """Return (cache_hit, role).  Role is None when the user is not a member."""
    key = (repo_id, user_id)
    with _cache_lock:
        entry = _cache.get(key)
    if entry is None:
        return False, None
    role, expiry = entry
    if time.monotonic() > expiry:
        return False, None
    return True, role


def _cache_put(repo_id: str, user_id: str, role: str | None, ttl: int) -> None:
    key = (repo_id, user_id)
    with _cache_lock:
        _cache[key] = (role, time.monotonic() + ttl)


def invalidate(repo_id: str, user_id: str) -> None:
    """
    Evict a single cache entry.
    Called by the internal cache-invalidation endpoint (Phase 9 SQS consumer).
    """
    with _cache_lock:
        _cache.pop((repo_id, user_id), None)


# ---------------------------------------------------------------------------
# Retry policy — transient errors only
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
# Role lookup
# ---------------------------------------------------------------------------

def get_role(repo_id: uuid.UUID, user_id: str, ttl: int = 60) -> str | None:
    """
    Return the caller's RepoRole string for the given repo, or None if they
    are not a member.

    Results are cached for `ttl` seconds (default 60, per spec).
    Raises HTTPException(503) when identity-service is unreachable after retries.
    Raises HTTPException(502) on unexpected application-level errors.
    """
    rid = str(repo_id)
    hit, cached = _cache_get(rid, user_id)
    if hit:
        return cached

    @_retry
    def _call() -> str | None:
        resp = _get().get(
            f"/v1/internal/repos/{repo_id}/role",
            params={"user_id": user_id},
        )
        if resp.status_code == 404:
            return None  # not a member
        resp.raise_for_status()
        return resp.json()["role"]

    try:
        role = _call()
    except RetryError:
        log.warning("identity_client_unreachable", repo_id=rid, user_id=user_id)
        raise HTTPException(
            status_code=503,
            detail="Identity service is temporarily unavailable. Please try again.",
        )
    except httpx.HTTPStatusError as exc:
        log.error(
            "identity_client_error",
            repo_id=rid,
            status=exc.response.status_code,
        )
        raise HTTPException(status_code=502, detail="Failed to resolve membership.")

    _cache_put(rid, user_id, role, ttl)
    return role
