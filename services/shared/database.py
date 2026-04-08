"""
Database engine factory for per-service engine creation.

Each service creates its own engine by calling create_service_engine() with its
own DATABASE_URL.  This enables per-service PostgreSQL credentials (least-privilege
roles per AWS Well-Architected SEC05) and removes the module-level side effect
that previously fired at import time.

Usage (in each service's app/database.py):

    from shared.database import create_service_engine
    from app.core.config import settings
    from typing import Generator
    from sqlmodel import Session

    engine = create_service_engine(settings.DATABASE_URL)

    def get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session
"""
import os
from sqlalchemy import Engine
from sqlmodel import create_engine


# ---------------------------------------------------------------------------
# PostgreSQL connection options
# ---------------------------------------------------------------------------
_SSL_CERT = "/home/appuser/.postgresql/root.crt"


def create_service_engine(database_url: str) -> Engine:
    """
    Build and return a SQLAlchemy engine configured for production use on AWS RDS.

    Parameters
    ----------
    database_url:
        The full PostgreSQL DSN for this service's dedicated DB user.
        Each service should pass its own DATABASE_URL from settings so
        per-service PostgreSQL roles are honoured.
    """
    connect_args: dict = {
        "options": "-c statement_timeout=5000"  # 5 s hard cap per statement
    }
    if "amazonaws.com" in database_url:
        connect_args["sslmode"] = "verify-full"
        if os.path.exists(_SSL_CERT):
            connect_args["sslrootcert"] = _SSL_CERT

    pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
    max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "3"))

    return create_engine(
        database_url,
        echo=os.getenv("DEBUG", "false").lower() == "true",
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_pre_ping=True,  # discard stale connections silently (handles RDS failover)
        connect_args=connect_args,
    )
