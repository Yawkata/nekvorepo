import threading
import uuid
import time
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import text, Session
from app.database import engine
from shared.logging import configure_logging
from shared.sqs_consumer import run_cache_invalidation_consumer
from app.api.v1.api import api_router
from app.core.config import settings
from app.services import identity_client, repo_client

configure_logging("workflow-service")
log = structlog.get_logger()

_SWEEP_INTERVAL_SECONDS = 300  # 5 minutes
_SWEEP_STALE_MINUTES = 5


# ---------------------------------------------------------------------------
# Background sweep — recover drafts stuck in 'committing'
# ---------------------------------------------------------------------------

def _committing_sweep() -> None:
    """
    Daemon thread: resets drafts stuck in 'committing' status.

    A draft is stuck if:
      - status = 'committing'
      - updated_at is older than 5 minutes
      - no repo_commits row was written for this draft (the S3 sync never completed)

    Safe to run concurrently via FOR UPDATE SKIP LOCKED — only one pod
    processes each stuck draft at a time.
    """
    while True:
        time.sleep(_SWEEP_INTERVAL_SECONDS)
        try:
            with Session(engine) as db:
                result = db.exec(  # type: ignore[call-overload]
                    text(
                        "UPDATE drafts SET status = 'editing' "
                        "WHERE id IN ("
                        "  SELECT id FROM drafts "
                        "  WHERE status = 'committing' "
                        "    AND updated_at < now() - interval '5 minutes' "
                        "    AND NOT EXISTS ("
                        "      SELECT 1 FROM repo_commits c WHERE c.draft_id = drafts.id"
                        "    ) "
                        "  FOR UPDATE SKIP LOCKED"
                        ")"
                    )
                )
                recovered = result.rowcount
                db.commit()
                if recovered:
                    log.info("committing_sweep_recovered", count=recovered)
        except Exception as exc:
            log.error("committing_sweep_error", error=str(exc))


def _sqs_cache_invalidation_consumer() -> None:
    """
    Daemon thread: long-polls the SQS cache-invalidation queue and evicts
    stale role-cache entries whenever a member is removed.  Delegates to the
    shared consumer helper so classification of fatal vs transient errors is
    identical across every consumer service.
    """
    run_cache_invalidation_consumer(
        queue_url=settings.SQS_CACHE_INVALIDATION_QUEUE_URL,
        region_name=settings.AWS_REGION,
        on_invalidate=identity_client.invalidate,
        service_name="workflow-service",
    )


# ---------------------------------------------------------------------------
# Lifespan — startup and shutdown hooks
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize internal service HTTP clients
    identity_client.setup(settings.IDENTITY_SERVICE_URL)
    repo_client.setup(settings.REPO_SERVICE_URL)

    # Start background sweep for stuck committing drafts
    sweep_thread = threading.Thread(target=_committing_sweep, daemon=True, name="committing-sweep")
    sweep_thread.start()

    # Start SQS cache-invalidation consumer (no-op if queue URL not configured)
    sqs_thread = threading.Thread(target=_sqs_cache_invalidation_consumer, daemon=True, name="sqs-cache-invalidation")
    sqs_thread.start()

    log.info(
        "workflow_service_started",
        identity_url=settings.IDENTITY_SERVICE_URL,
        repo_url=settings.REPO_SERVICE_URL,
    )
    yield
    # Shutdown: close HTTP clients cleanly
    identity_client.teardown()
    repo_client.teardown()
    log.info("workflow_service_stopped")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title=f"{settings.PROJECT_NAME} - Workflow Service",
    version="2026.1.0",
    description=(
        "Manages repository lifecycle. All endpoints require a Passport JWT from identity-service.\n\n"
        "**Flow:** log in via identity-service → copy `access_token` → click **Authorize** → paste as `Bearer <token>`."
    ),
    openapi_tags=[
        {"name": "repos", "description": "Create and manage repositories."},
        {"name": "ops", "description": "Kubernetes liveness and readiness probes."},
    ],
    lifespan=lifespan,
    swagger_ui_parameters={"persistAuthorization": True},
    # Disable interactive docs + OpenAPI schema in production (SEC03 — reduce attack surface).
    docs_url=None if settings.ENV == "prod" else "/docs",
    redoc_url=None if settings.ENV == "prod" else "/redoc",
    openapi_url=None if settings.ENV == "prod" else "/openapi.json",
)

# CORS — restricted to configured origins; expose correlation ID header to JS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    expose_headers=["X-Correlation-ID"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all for unhandled exceptions — return structured JSON with correlation ID."""
    log.error("unhandled_exception", error=str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred.", "type": "internal_error"},
        headers={"X-Correlation-ID": request.headers.get("X-Correlation-ID", "")},
    )


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Attach a per-request correlation ID and log every request/response."""
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
        service="workflow-service",
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
    Readiness probe — verifies RDS connectivity.
    Returns 200 when all checks pass, 503 otherwise.
    """
    checks: dict = {}

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["rds"] = "ok"
    except Exception:
        checks["rds"] = "error"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return JSONResponse(
        content={"status": overall, "checks": checks},
        status_code=200 if overall == "ok" else 503,
    )
