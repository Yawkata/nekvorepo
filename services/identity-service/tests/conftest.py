"""
Identity-service integration test fixtures.

Bootstrap strategy
------------------
Env vars must be set BEFORE any app or shared module is imported.
pydantic-settings (Settings()) reads env vars at class definition time; the
PASSPORT_SECRET_KEY validator rejects weak values, so we provide a 32-char
value that passes the length check.  The DATABASE_URL placeholder is harmless
because we override deps.get_db with a real testcontainers session, so the
module-level engine in app.database never actually connects.

Run from services/identity-service/:
    uv run --group test pytest
"""

# ── Bootstrap — MUST be first in conftest ─────────────────────────────────────
import os

_TEST_PASSPORT_SECRET = "a" * 32   # 32 chars — passes the ≥32 validator
_TEST_USER_ID  = "test-user-sub"
_TEST_EMAIL    = "test@example.com"

os.environ.setdefault("DATABASE_URL",         "postgresql+psycopg://placeholder:x@localhost:5432/placeholder")
os.environ.setdefault("COGNITO_USER_POOL_ID", "us-east-1_TESTPOOL")
os.environ.setdefault("COGNITO_CLIENT_ID",    "test_client_id")
os.environ.setdefault("COGNITO_CLIENT_SECRET","test_client_secret_placeholder_xxx")
os.environ.setdefault("PASSPORT_SECRET_KEY",  _TEST_PASSPORT_SECRET)
os.environ.setdefault("AWS_REGION",           "us-east-1")

# ── Standard imports ──────────────────────────────────────────────────────────
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine
from testcontainers.postgres import PostgresContainer

from shared.constants import RepoRole
from shared.models.identity import UserRepoLink
from shared.models.workflow import RepoHead


# ── Session-scoped database container ────────────────────────────────────────

@pytest.fixture(scope="session")
def pg_container():
    """One PostgreSQL container per test session."""
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def db_engine(pg_container):
    """
    Engine + schema creation.  Every model must be imported so that
    cross-table FKs (e.g. repo_commits.draft_id → drafts) resolve
    before create_all() runs.
    """
    url = pg_container.get_connection_url().replace("psycopg2", "psycopg")
    engine = create_engine(url, echo=False)

    import shared.models.identity  # noqa: F401  UserRepoLink
    import shared.models.workflow  # noqa: F401  RepoHead, RepoTreeRoot, …
    import shared.models.repo      # noqa: F401  Blob, Draft  → 'drafts' table
    import shared.models.invite    # noqa: F401  InviteToken

    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


# ── Per-test isolation ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def truncate_tables(db_engine):
    """Wipe mutable rows after each test to prevent state bleed."""
    yield
    with db_engine.connect() as conn:
        # CASCADE handles invite_tokens.repo_id → repo_heads and
        # drafts.repo_id → repo_heads, etc.
        conn.execute(text(
            "TRUNCATE repo_heads, user_repo_links RESTART IDENTITY CASCADE"
        ))
        conn.commit()


# ── Core fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_cognito():
    """Fresh MagicMock for CognitoService — injected per test."""
    return MagicMock()


@pytest.fixture
def db_session(db_engine):
    """Direct session for seeding test data (committed, visible to handlers)."""
    with Session(db_engine) as session:
        yield session


@pytest.fixture
def client(db_engine, mock_cognito):
    """
    TestClient with:
      - get_db  → testcontainers session
      - get_cognito → MagicMock (no AWS calls)
    """
    from app.main import app
    from app.api import deps

    def _db():
        with Session(db_engine) as s:
            yield s

    app.dependency_overrides[deps.get_db] = _db
    app.dependency_overrides[deps.get_cognito] = lambda: mock_cognito

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


# ── Passport JWT factory ──────────────────────────────────────────────────────

@pytest.fixture
def make_passport():
    """
    Mint Passport JWTs that match what create_passport_token() produces:
      aud = "internal-microservices"
      iss = "identity-service"
      sub / email claims — NO permissions (those are resolved per-request)
    """
    def _make(
        user_id: str = _TEST_USER_ID,
        email: str = _TEST_EMAIL,
        expired: bool = False,
        wrong_issuer: bool = False,
        wrong_secret: bool = False,
        wrong_audience: bool = False,
    ) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub":   user_id,
            "email": email,
            "iat":   now,
            "exp":   now + (timedelta(seconds=-1) if expired else timedelta(hours=1)),
            "iss":   "bad-issuer"           if wrong_issuer  else "identity-service",
            "aud":   "wrong-microservices"  if wrong_audience else "internal-microservices",
        }
        secret = "z" * 32 if wrong_secret else _TEST_PASSPORT_SECRET
        return jwt.encode(payload, secret, algorithm="HS256")

    return _make


@pytest.fixture
def auth_headers(make_passport):
    """Return Authorization headers for a given user."""
    def _headers(user_id: str = _TEST_USER_ID, email: str = _TEST_EMAIL, **kw) -> dict:
        return {"Authorization": f"Bearer {make_passport(user_id=user_id, email=email, **kw)}"}
    return _headers


# ── DB seeder helpers ─────────────────────────────────────────────────────────

@pytest.fixture
def make_repo(db_session):
    """
    Seed a RepoHead + admin UserRepoLink in one call.
    Returns the persisted RepoHead instance.
    """
    def _make(
        owner_id: str = _TEST_USER_ID,
        repo_name: str = "test-repo",
        description: Optional[str] = None,
    ) -> RepoHead:
        repo = RepoHead(
            repo_name=repo_name,
            owner_id=owner_id,
            description=description,
            version=0,
        )
        db_session.add(repo)
        db_session.flush()
        db_session.add(UserRepoLink(repo_id=repo.id, user_id=owner_id, role=RepoRole.admin))
        db_session.commit()
        db_session.refresh(repo)
        return repo

    return _make


@pytest.fixture
def make_membership(db_session):
    """Seed a UserRepoLink with an explicit role."""
    def _make(repo_id: uuid.UUID, user_id: str, role: RepoRole = RepoRole.reader) -> UserRepoLink:
        link = UserRepoLink(repo_id=repo_id, user_id=user_id, role=role)
        db_session.add(link)
        db_session.commit()
        db_session.refresh(link)
        return link

    return _make
