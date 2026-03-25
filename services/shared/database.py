import os
from typing import Generator
from sqlmodel import Session, create_engine

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is required but not set."
    )

# ---------------------------------------------------------------------------
# PostgreSQL connection options
# ---------------------------------------------------------------------------
_SSL_CERT = "/home/appuser/.postgresql/root.crt"

connect_args: dict = {
    "options": "-c statement_timeout=5000"  # 5 s hard cap per statement
}
if "amazonaws.com" in DATABASE_URL:
    connect_args["sslmode"] = "verify-full"
    if os.path.exists(_SSL_CERT):
        connect_args["sslrootcert"] = _SSL_CERT

# ---------------------------------------------------------------------------
# Pool sizing — tuned per-service via env vars.
# Defaults (5 base + 10 overflow = 15 max) are appropriate for a single-worker
# uvicorn process handling sync endpoints. Tune upward for multi-worker deploys.
# Rule of thumb: pool_size ≈ expected concurrent requests per worker.
# ---------------------------------------------------------------------------
_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "3"))  # seconds before "pool full" error

engine = create_engine(
    DATABASE_URL,
    echo=os.getenv("DEBUG", "false").lower() == "true",
    pool_size=_POOL_SIZE,
    max_overflow=_MAX_OVERFLOW,
    pool_timeout=_POOL_TIMEOUT,
    pool_pre_ping=True,   # discard stale connections silently (handles RDS failover)
    connect_args=connect_args,
)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a SQLModel Session, commits on clean exit."""
    with Session(engine) as session:
        yield session
