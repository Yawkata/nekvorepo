import uuid
import time
import urllib.request
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import text
from shared.database import engine
from shared.logging import configure_logging
from shared.security.cognito import JWKS_URL
from app.api.v1.api import api_router
from app.core.config import settings

configure_logging("identity-service")
log = structlog.get_logger()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title=f"{settings.PROJECT_NAME} - Identity Service",
    version="2026.1.0",
    description=(
        "**Identity Service**"
        "Handles user registration, authentication via AWS Cognito, "
        "and issues internal Passport JWTs consumed by downstream services.\n\n"
        "**Flow:** `POST /v1/auth/login` → copy `access_token` → click **Authorize** → paste as `Bearer <token>`."
    ),
    openapi_tags=[
        {
            "name": "Authentication",
            "description": (
                "Register, confirm, and log in users. The `/login` response includes an "
                "`access_token` (Passport JWT) — use it to authorize protected endpoints."
            ),
        },
        {
            "name": "Users",
            "description": "User-facing operations — returns the list of accessible repositories.",
        },
        {
            "name": "Internal",
            "description": (
                "Service-to-service endpoints for membership management and role lookups. "
                "In production these are cluster-internal only (not exposed via ALB)."
            ),
        },
        {"name": "ops", "description": "Kubernetes liveness and readiness probes."},
    ],
    swagger_ui_parameters={"persistAuthorization": True},
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


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Attach a per-request correlation ID and log every request/response."""
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
        service="identity-service",
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
    Readiness probe — checks RDS and Cognito JWKS reachability.
    Returns 200 when all pass, 503 when any fail.
    """
    checks: dict = {}

    # RDS
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["rds"] = "ok"
    except Exception:
        checks["rds"] = "error"

    # Cognito JWKS
    try:
        with urllib.request.urlopen(
            urllib.request.Request(JWKS_URL), timeout=3
        ):
            pass
        checks["cognito_jwks"] = "ok"
    except Exception:
        checks["cognito_jwks"] = "error"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return JSONResponse(
        content={"status": overall, "checks": checks},
        status_code=200 if overall == "ok" else 503,
    )
