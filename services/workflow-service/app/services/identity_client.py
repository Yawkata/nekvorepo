"""
HTTP client for calling identity-service internal endpoints.

Responsibilities:
  - Resolve a user's role for a given repo (GET /v1/internal/repos/{id}/role).
  - Cache role results per (repo_id, user_id) with a 60-second TTL, matching the
    spec's bounded-eventual-consistency guarantee.
  - Retry only on transient network errors; propagate application-level 4xx immediately.

Lifecycle: call setup() in the FastAPI lifespan startup and teardown() in shutdown.
"""
import time
import threading
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
# Role cache — (repo_id_str, user_id) → (role_or_None, expiry_monotonic)
# ---------------------------------------------------------------------------

_cache: dict[tuple[str, str], tuple[str | None, float]] = {}
_cache_lock = threading.Lock()


def _cache_get(repo_id: str, user_id: str) -> tuple[bool, str | None]:
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
    with _cache_lock:
        _cache[(repo_id, user_id)] = (role, time.monotonic() + ttl)


def invalidate(repo_id: str, user_id: str) -> None:
    """Evict a single cache entry."""
    with _cache_lock:
        _cache.pop((repo_id, user_id), None)

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

_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=5.0),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)),
    reraise=False,
)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def get_role(repo_id: uuid.UUID, user_id: str, ttl: int = 60) -> str | None:
    """
    Return the caller's RepoRole string for the given repo, or None if not a member.
    Results are cached for `ttl` seconds (default 60, per spec).
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
            return None
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
        log.error("identity_client_error", repo_id=rid, status=exc.response.status_code)
        raise HTTPException(status_code=502, detail="Failed to resolve membership.")

    _cache_put(rid, user_id, role, ttl)
    return role
