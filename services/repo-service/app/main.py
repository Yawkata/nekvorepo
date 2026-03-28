import uuid
import time
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import text

from shared.database import engine
from shared.logging import configure_logging
from app.api.v1.api import api_router
from app.core.config import settings
from app.services import identity_client

configure_logging("repo-service")
log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Lifespan — startup and shutdown hooks
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    identity_client.setup(settings.IDENTITY_SERVICE_URL)
    log.info("repo_service_started", identity_url=settings.IDENTITY_SERVICE_URL)
    yield
    identity_client.teardown()
    log.info("repo_service_stopped")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.PROJECT_NAME,
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
