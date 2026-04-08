"""
Database engine and session factory for identity-service.

Uses a dedicated PostgreSQL role (identity_svc) with permissions scoped to
the tables this service owns/reads — see terraform/rds.tf for role grants.
"""
from typing import Generator

from sqlmodel import Session

from shared.database import create_service_engine
from app.core.config import settings

engine = create_service_engine(settings.DATABASE_URL)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a SQLModel Session, commits on clean exit."""
    with Session(engine) as session:
        yield session
