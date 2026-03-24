import uuid
import time
import logging
import urllib.request
import urllib.error
import structlog
from fastapi import FastAPI, Request, Security
from fastapi.responses import JSONResponse
from sqlmodel import text
from shared.database import engine
from shared.security import verify_passport, TokenData, JWKS_URL
from app.api.v1.api import api_router
from app.core.config import settings

# ---------------------------------------------------------------------------
# Structlog configuration — emit JSON on every log record
# ---------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title=settings.PROJECT_NAME, version="2026.1.0")


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Attach a correlation ID to every request and log start/finish."""
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
        service="identity-service",
    )
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000, 1)
    log.info(
        "request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    response.headers["X-Correlation-ID"] = correlation_id
    return response


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/v1/auth/verify-me")
def verify_me(passport: TokenData = Security(verify_passport)):
    return {"status": "verified", "data": passport}


# ---------------------------------------------------------------------------
# Health probes (spec §2.1)
# ---------------------------------------------------------------------------

@app.get("/ping", tags=["ops"])
def liveness():
    """Liveness probe — returns 200 immediately without touching dependencies."""
    return {"status": "ok"}


@app.get("/health", tags=["ops"])
def readiness():
    """
    Readiness probe — checks RDS and Cognito JWKS.
    Returns 200 when all pass, 503 when any fail.
    """
    checks: dict = {}

    # RDS check
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["rds"] = "ok"
    except Exception:
        checks["rds"] = "error"

    # Cognito JWKS check
    try:
        req = urllib.request.Request(JWKS_URL)
        with urllib.request.urlopen(req, timeout=3):
            pass
        checks["cognito_jwks"] = "ok"
    except Exception:
        checks["cognito_jwks"] = "error"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    status_code = 200 if overall == "ok" else 503
    return JSONResponse(
        content={"status": overall, "checks": checks},
        status_code=status_code,
    )