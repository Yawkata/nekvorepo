"""
Workflow-service integration test fixtures.

Bootstrap strategy
------------------
Env vars must be set BEFORE any app or shared module is imported.
pydantic-settings reads env vars at class definition time; PASSPORT_SECRET_KEY
has a ≥32-char validator and S3_REPO_BUCKET is required with no default.

Run from services/workflow-service/:
    uv run --group test pytest
"""

# ── Bootstrap — MUST be first in conftest ────────────────────────────────────
import os

_TEST_PASSPORT_SECRET = "a" * 32

os.environ.setdefault("DATABASE_URL",          "postgresql+psycopg://placeholder:x@localhost:5432/placeholder")
os.environ.setdefault("PASSPORT_SECRET_KEY",   _TEST_PASSPORT_SECRET)
os.environ.setdefault("IDENTITY_SERVICE_URL",  "http://identity-service:8000")
os.environ.setdefault("REPO_SERVICE_URL",      "http://repo-service:8000")
os.environ.setdefault("S3_REPO_BUCKET",        "test-bucket")
os.environ.setdefault("SES_FROM_EMAIL",        "")   # disables SES notifications

# ── Standard imports ──────────────────────────────────────────────────────────
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine
from testcontainers.postgres import PostgresContainer

from shared.constants import CommitStatus, DraftStatus, RepoRole
from shared.models.workflow import RepoCommit, RepoHead, RepoTreeRoot, RepoTreeEntry
from shared.models.repo import Draft

_TEST_USER_ID    = "test-user-sub"
_TEST_EMAIL      = "test@example.com"
_TEST_REVIEWER_ID = "reviewer-user-sub"
_TEST_BLOB_HASH  = "a" * 64   # fake SHA-256 hex (64 chars)


# ── Session-scoped database container ────────────────────────────────────────

@pytest.fixture(scope="session")
def pg_container():
    """One PostgreSQL container for the entire test session."""
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def db_engine(pg_container):
    """
    Engine + full schema.  All model modules must be imported so that
    cross-table FKs resolve before create_all() runs.
    """
    url = pg_container.get_connection_url().replace("psycopg2", "psycopg")
    engine = create_engine(url, echo=False)

    import shared.models.identity  # noqa: F401  UserRepoLink
    import shared.models.workflow  # noqa: F401  RepoHead, RepoTreeRoot, …
    import shared.models.repo      # noqa: F401  Blob, Draft
    import shared.models.invite    # noqa: F401  InviteToken

    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


# ── Per-test isolation ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def truncate_tables(db_engine):
    """Wipe all mutable rows after each test. CASCADE handles FK dependencies."""
    yield
    with db_engine.connect() as conn:
        conn.execute(text(
            "TRUNCATE repo_heads, repo_tree_roots RESTART IDENTITY CASCADE"
        ))
        conn.commit()


# ── Core fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def db_session(db_engine):
    """Direct session for seeding and verifying DB state."""
    with Session(db_engine) as session:
        yield session


@pytest.fixture
def mock_identity_client():
    """
    Patch identity_client.get_role so no HTTP call is made.
    Default return value is "admin"; individual tests can override:
        mock_identity_client.return_value = "reviewer"
        mock_identity_client.return_value = None  # non-member
    """
    with patch("app.services.identity_client.get_role", return_value="admin") as mock:
        yield mock


@pytest.fixture
def mock_repo_client():
    """
    Patch repo_client.sync_blobs and wipe_draft so no HTTP calls are made.
    Default sync_blobs returns a single-file blob map.
    Yields (mock_sync_blobs, mock_wipe_draft) — tests can adjust return_value/side_effect.
    """
    default_blobs = {"readme.txt": _TEST_BLOB_HASH}
    with patch("app.services.repo_client.sync_blobs", return_value=default_blobs) as mock_sync, \
         patch("app.services.repo_client.wipe_draft", return_value=None) as mock_wipe:
        yield mock_sync, mock_wipe


@pytest.fixture
def client(db_engine):
    """
    TestClient with:
      - get_db overridden to use the testcontainers session
      - app.main.engine swapped to the testcontainers engine so /health works
    identity_client and repo_client are patched separately per-test via
    mock_identity_client / mock_repo_client fixtures.
    """
    from app.main import app
    from app.api import deps
    import app.main as app_main

    def _db():
        with Session(db_engine) as s:
            yield s

    app.dependency_overrides[deps.get_db] = _db

    # /health uses app.main.engine directly — point it at the test container
    original_engine = app_main.engine
    app_main.engine = db_engine

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app_main.engine = original_engine
    app.dependency_overrides.clear()


# ── Passport JWT factory ──────────────────────────────────────────────────────

@pytest.fixture
def make_passport():
    """
    Mint Passport JWTs matching what identity-service issues:
      iss = "identity-service"
      aud = "internal-microservices"
    """
    def _make(
        user_id: str = _TEST_USER_ID,
        email: str = _TEST_EMAIL,
        expired: bool = False,
        wrong_secret: bool = False,
        wrong_issuer: bool = False,
        wrong_audience: bool = False,
    ) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub":   user_id,
            "email": email,
            "iat":   now,
            "exp":   now + (timedelta(seconds=-1) if expired else timedelta(hours=1)),
            "iss":   "bad-issuer"           if wrong_issuer   else "identity-service",
            "aud":   "wrong-microservices"  if wrong_audience else "internal-microservices",
        }
        secret = "z" * 32 if wrong_secret else _TEST_PASSPORT_SECRET
        return jwt.encode(payload, secret, algorithm="HS256")

    return _make


@pytest.fixture
def auth_headers(make_passport):
    """Return Authorization Bearer headers for the given user."""
    def _headers(user_id: str = _TEST_USER_ID, email: str = _TEST_EMAIL, **kw) -> dict:
        return {"Authorization": f"Bearer {make_passport(user_id=user_id, email=email, **kw)}"}
    return _headers


# ── DB seeder helpers ─────────────────────────────────────────────────────────

@pytest.fixture
def make_repo(db_session):
    """Seed a RepoHead row. Returns the persisted instance."""
    def _make(
        owner_id: str = _TEST_USER_ID,
        repo_name: str = "test-repo",
        latest_commit_hash: Optional[str] = None,
        version: int = 0,
    ) -> RepoHead:
        repo = RepoHead(
            repo_name=repo_name,
            owner_id=owner_id,
            version=version,
            latest_commit_hash=latest_commit_hash,
        )
        db_session.add(repo)
        db_session.commit()
        db_session.refresh(repo)
        return repo

    return _make


@pytest.fixture
def make_draft(db_session):
    """Seed a Draft row. Returns the persisted instance."""
    def _make(
        repo_id: uuid.UUID,
        user_id: str = _TEST_USER_ID,
        status: DraftStatus = DraftStatus.editing,
        base_commit_hash: Optional[str] = None,
    ) -> Draft:
        draft = Draft(
            repo_id=repo_id,
            user_id=user_id,
            status=status,
            base_commit_hash=base_commit_hash,
        )
        db_session.add(draft)
        db_session.commit()
        db_session.refresh(draft)
        return draft

    return _make


@pytest.fixture
def make_commit(db_session):
    """
    Seed a RepoTreeRoot + RepoCommit row.
    Returns the persisted RepoCommit instance.
    Used to represent prior commit history without going through the endpoint.
    """
    def _make(
        repo_id: uuid.UUID,
        owner_id: str = _TEST_USER_ID,
        status: CommitStatus = CommitStatus.pending,
        parent_commit_hash: Optional[str] = None,
        draft_id: Optional[uuid.UUID] = None,
        commit_summary: str = "Seeded test commit",
        author_email: Optional[str] = _TEST_EMAIL,
    ) -> RepoCommit:
        tree_hash = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
        tree = RepoTreeRoot(tree_hash=tree_hash)
        db_session.add(tree)
        db_session.flush()

        commit_hash = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
        commit = RepoCommit(
            commit_hash=commit_hash,
            repo_id=repo_id,
            owner_id=owner_id,
            parent_commit_hash=parent_commit_hash,
            tree_id=tree.id,
            draft_id=draft_id,
            status=status,
            commit_summary=commit_summary,
            author_email=author_email,
        )
        db_session.add(commit)
        db_session.commit()
        db_session.refresh(commit)
        return commit

    return _make
