import threading
import uuid
import time
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import text, Session, select

from shared.constants import CommitStatus, DraftStatus
from shared.database import engine
from shared.logging import configure_logging
from shared.models.workflow import Draft, RepoCommit, RepoHead
from app.api.v1.api import api_router
from app.core.config import settings
from app.services import identity_client

configure_logging("repo-service")
log = structlog.get_logger()

_SWEEP_INTERVAL_SECONDS = 300  # 5 minutes

# Mapping from CommitStatus → DraftStatus for fallback recovery
_COMMIT_TO_DRAFT_FALLBACK: dict[CommitStatus, DraftStatus] = {
    CommitStatus.rejected: DraftStatus.rejected,
    CommitStatus.sibling_rejected: DraftStatus.sibling_rejected,
    CommitStatus.approved: DraftStatus.approved,
}


# ---------------------------------------------------------------------------
# Background sweep — recover drafts stuck in 'reconstructing'
# ---------------------------------------------------------------------------

def _reconstructing_sweep() -> None:
    """
    Daemon thread: resets drafts stuck in 'reconstructing' status.

    A draft is stuck if updated_at is older than 5 minutes and its background
    task never completed (e.g. the pod crashed mid-download).

    Fallback status is derived from the linked commit's status so that the UI
    can show the correct "Try Again" state.
    """
    while True:
        time.sleep(_SWEEP_INTERVAL_SECONDS)
        try:
            with Session(engine) as db:
                stuck_drafts = db.exec(  # type: ignore[call-overload]
                    text(
                        "SELECT id, commit_hash FROM drafts "
                        "WHERE status = 'reconstructing' "
                        "  AND updated_at < now() - interval '5 minutes' "
                        "FOR UPDATE SKIP LOCKED"
                    )
                ).all()

                recovered = 0
                for row in stuck_drafts:
                    draft = db.get(Draft, row.id)
                    if draft is None:
                        continue

                    if draft.commit_hash:
                        # Draft was previously submitted — restore to its linked
                        # commit's terminal outcome (rejected/sibling_rejected/approved).
                        fallback = DraftStatus.rejected
                        linked_commit = db.exec(
                            select(RepoCommit).where(
                                RepoCommit.commit_hash == draft.commit_hash
                            )
                        ).first()
                        if linked_commit:
                            fallback = _COMMIT_TO_DRAFT_FALLBACK.get(
                                linked_commit.status, DraftStatus.rejected
                            )
                    else:
                        # Brand-new draft that was never submitted (commit_hash is
                        # None) — the background task crashed before it could restore
                        # the EFS snapshot.  Restore to editing/needs_rebase so the
                        # author can keep working.
                        repo = db.get(RepoHead, draft.repo_id)
                        if repo and repo.latest_commit_hash != draft.base_commit_hash:
                            fallback = DraftStatus.needs_rebase
                        else:
                            fallback = DraftStatus.editing

                    draft.status = fallback
                    db.add(draft)
                    recovered += 1

                db.commit()
                if recovered:
                    log.info("reconstructing_sweep_recovered", count=recovered)
        except Exception as exc:
            log.error("reconstructing_sweep_error", error=str(exc))


# ---------------------------------------------------------------------------
# Lifespan — startup and shutdown hooks
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    identity_client.setup(settings.IDENTITY_SERVICE_URL)

    # Start background sweep for stuck reconstructing drafts
    sweep_thread = threading.Thread(
        target=_reconstructing_sweep, daemon=True, name="reconstructing-sweep"
    )
    sweep_thread.start()

    log.info("repo_service_started", identity_url=settings.IDENTITY_SERVICE_URL)
    yield
    identity_client.teardown()
    log.info("repo_service_stopped")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title=f"{settings.PROJECT_NAME} - Repo Service",
    version="2026.1.0",
    description=(
        "Manages draft file trees on EFS. All endpoints require a Passport JWT from identity-service.\n\n"
        "**Flow:** log in via identity-service → copy `access_token` → click **Authorize** → paste as `Bearer <token>`."
    ),
    openapi_tags=[
        {"name": "Drafts", "description": "Create and manage draft file trees. Requires author or admin role."},
        {"name": "Internal", "description": "Service-to-service endpoints. Cluster-internal only in production."},
        {"name": "ops", "description": "Kubernetes liveness and readiness probes."},
    ],
    lifespan=lifespan,
    swagger_ui_parameters={"persistAuthorization": True},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    expose_headers=["X-Correlation-ID", "X-Large-File-Warning"],
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Attach a per-request correlation ID and emit a structured access log entry."""
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
        service="repo-service",
    )
    start = time.monotonic()
    response = await call_next(request)
    log.info(
        "request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round((time.monotonic() - start) * 1000, 1),
    )
    response.headers["X-Correlation-ID"] = correlation_id
    return response


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(api_router, prefix=settings.API_V1_STR)


# ---------------------------------------------------------------------------
# Health probes
# ---------------------------------------------------------------------------

@app.get("/ping", tags=["ops"])
def liveness():
    """Liveness probe — returns 200 immediately, no dependency checks."""
    return {"status": "ok"}


@app.get("/health", tags=["ops"])
def readiness():
    """
    Readiness probe — verifies RDS connectivity and EFS mount availability.
    Returns 200 when all checks pass, 503 otherwise.
    """
    checks: dict = {}

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["rds"] = "ok"
    except Exception:
        checks["rds"] = "error"

    efs_root = Path(settings.EFS_DRAFTS_ROOT)
    try:
        # Verify the mount point is reachable and writable by creating a probe file.
        probe = efs_root / ".health_probe"
        probe.touch()
        probe.unlink()
        checks["efs"] = "ok"
    except Exception:
        checks["efs"] = "error"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return JSONResponse(
        content={"status": overall, "checks": checks},
        status_code=200 if overall == "ok" else 503,
    )
