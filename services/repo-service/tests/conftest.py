"""
Shared pytest fixtures for repo-service integration tests.

Infrastructure:
  - Real PostgreSQL (testcontainers) — full FK / constraint coverage
  - Real temp directory for EFS — actual file I/O, no fakes
  - Mocked StorageManager — no boto3 / S3 calls
  - Patched identity_client.get_role — no HTTP to identity-service
"""
import os
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select
from testcontainers.postgres import PostgresContainer

# ── Bootstrap env vars BEFORE any app import ────────────────────────────────
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:x@localhost/placeholder")
os.environ.setdefault("PASSPORT_SECRET_KEY", "a" * 32)
os.environ.setdefault("IDENTITY_SERVICE_URL", "http://identity-service:8000")
os.environ.setdefault("S3_REPO_BUCKET", "test-bucket")
os.environ.setdefault("SES_FROM_EMAIL", "")

# App imports must come AFTER env var bootstrap
from app.api import deps                          # noqa: E402
from app.main import app                          # noqa: E402
from app.services.efs import EFSService           # noqa: E402
import shared.models.repo as _repo_models         # noqa: E402, F401
import shared.models.workflow as _workflow_models  # noqa: E402, F401


# ── Session-scoped Postgres container ───────────────────────────────────────

@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def db_engine(pg_container):
    url = pg_container.get_connection_url().replace("psycopg2", "psycopg")
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


# ── Per-test table cleanup ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def truncate_tables(db_engine):
    yield
    with db_engine.connect() as conn:
        conn.execute(text(
            "TRUNCATE repo_heads, blobs, repo_tree_roots RESTART IDENTITY CASCADE"
        ))
        conn.commit()


# ── Per-test temp EFS directory ─────────────────────────────────────────────

@pytest.fixture
def tmp_efs():
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


# ── Direct DB session (for seeding / assertions) ────────────────────────────

@pytest.fixture
def db_session(db_engine):
    with Session(db_engine) as session:
        yield session


# ── Identity mock ────────────────────────────────────────────────────────────

@pytest.fixture
def mock_identity_client():
    """Patches get_role to return 'admin' by default; override with .return_value."""
    with patch("app.services.identity_client.get_role", return_value="admin") as mock:
        yield mock


# ── Storage mock ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_storage():
    """
    Patches the two module-level _storage singletons (internal.py and view.py).
    Tests can assert mock_storage.upload_blob.called etc.
    """
    mock = MagicMock()
    mock.upload_blob.return_value = None
    mock.blob_exists.return_value = False
    mock.download_blob.return_value = b""
    mock.generate_presigned_url.return_value = "https://s3.example.com/fake-url"
    with (
        patch("app.api.v1.endpoints.internal._storage", mock),
        patch("app.api.v1.endpoints.view._storage", mock),
    ):
        yield mock


@pytest.fixture
def mock_storage_manager():
    """
    Patches the StorageManager *class* so that any inline ``StorageManager()``
    call inside an endpoint or background task (e.g. the rebase / reconstruct
    handlers) receives the mock instance rather than a live boto3 client.

    By default ``download_blob`` returns empty bytes.  Override per-test:

        mock_storage_manager.download_blob.side_effect = lambda h: content_map[h]

    or:

        mock_storage_manager.download_blob.return_value = b"fixed content"
    """
    mock = MagicMock()
    mock.download_blob.return_value = b""
    mock.upload_blob.return_value = None
    mock.blob_exists.return_value = False
    with patch("app.services.storage.StorageManager", return_value=mock):
        yield mock


# ── Test client ──────────────────────────────────────────────────────────────

@pytest.fixture
def client(db_engine, tmp_efs):
    """
    TestClient with:
      - DB session pointed at the test Postgres container
      - EFSService backed by a per-test temp directory
      - app.main.engine swapped for the /health RDS probe
      - app.main.settings.EFS_DRAFTS_ROOT pointed at tmp_efs for the /health EFS probe
    """
    import app.main as app_main

    def _db():
        with Session(db_engine) as session:
            yield session

    def _efs():
        return EFSService(tmp_efs)

    app.dependency_overrides[deps.get_db] = _db
    app.dependency_overrides[deps.get_efs] = _efs

    original_engine = app_main.engine
    app_main.engine = db_engine

    original_efs_root = app_main.settings.EFS_DRAFTS_ROOT
    app_main.settings.EFS_DRAFTS_ROOT = tmp_efs

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()
    app_main.engine = original_engine
    app_main.settings.EFS_DRAFTS_ROOT = original_efs_root


# ── JWT helpers ──────────────────────────────────────────────────────────────

_SECRET = "a" * 32
_ALGORITHM = "HS256"


def _make_passport(user_id: str = "test-user", expired: bool = False) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iss": "identity-service",
        "aud": "internal-microservices",
        "iat": now - 3600 if expired else now,
        "exp": now - 1 if expired else now + 3600,
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)


@pytest.fixture
def auth_headers():
    def _make(user_id: str = "test-user", expired: bool = False) -> dict:
        token = _make_passport(user_id=user_id, expired=expired)
        return {"Authorization": f"Bearer {token}"}
    return _make


# ── DB seeders ───────────────────────────────────────────────────────────────

@pytest.fixture
def make_repo(db_session):
    def _make(
        owner_id: str = "test-user",
        repo_name: str | None = None,
        latest_commit_hash: str | None = None,
        version: int = 0,
    ):
        from shared.models.workflow import RepoHead
        name = repo_name or f"repo-{uuid.uuid4().hex[:8]}"
        repo = RepoHead(
            owner_id=owner_id,
            repo_name=name,
            latest_commit_hash=latest_commit_hash,
            version=version,
        )
        db_session.add(repo)
        db_session.commit()
        db_session.refresh(repo)
        return repo
    return _make


@pytest.fixture
def make_draft(db_session):
    def _make(
        repo_id,
        user_id: str = "test-user",
        status=None,
        base_commit_hash: str | None = None,
        label: str | None = None,
    ):
        from shared.constants import DraftStatus
        from shared.models.repo import Draft
        draft = Draft(
            repo_id=repo_id,
            user_id=user_id,
            status=status or DraftStatus.editing,
            base_commit_hash=base_commit_hash,
            label=label,
        )
        db_session.add(draft)
        db_session.commit()
        db_session.refresh(draft)
        return draft
    return _make


@pytest.fixture
def make_commit(db_session):
    import hashlib

    def _make(
        repo_id,
        owner_id: str = "test-user",
        status=None,
        parent_commit_hash: str | None = None,
        draft_id=None,
        commit_summary: str = "Test commit",
        tree_id: int | None = None,
    ):
        from shared.constants import CommitStatus
        from shared.models.workflow import RepoCommit, RepoTreeRoot

        if tree_id is None:
            tree_hash = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
            tree_root = RepoTreeRoot(tree_hash=tree_hash)
            db_session.add(tree_root)
            db_session.commit()
            db_session.refresh(tree_root)
            tree_id = tree_root.id

        commit_hash = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        commit = RepoCommit(
            commit_hash=commit_hash,
            repo_id=repo_id,
            owner_id=owner_id,
            parent_commit_hash=parent_commit_hash,
            tree_id=tree_id,
            draft_id=draft_id,
            status=status or CommitStatus.approved,
            commit_summary=commit_summary,
        )
        db_session.add(commit)
        db_session.commit()
        db_session.refresh(commit)
        return commit
    return _make


@pytest.fixture
def advance_repo_head(db_session):
    """
    Advance repo_heads.latest_commit_hash to the given commit_hash.
    Increments the optimistic-lock version counter.

    Usage:
        advance_repo_head(repo, commit.commit_hash)
    """
    def _advance(repo, commit_hash: str):
        from shared.models.workflow import RepoHead
        r = db_session.get(RepoHead, repo.id)
        r.latest_commit_hash = commit_hash
        r.version += 1
        db_session.add(r)
        db_session.commit()
        db_session.refresh(r)
        return r
    return _advance


@pytest.fixture
def make_blob(db_session):
    import hashlib

    def _make(
        blob_hash: str | None = None,
        size: int = 100,
        content_type: str = "text/plain",
    ):
        from shared.models.repo import Blob
        if blob_hash is None:
            blob_hash = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        blob = Blob(blob_hash=blob_hash, size=size, content_type=content_type)
        db_session.add(blob)
        db_session.commit()
        db_session.refresh(blob)
        return blob
    return _make


@pytest.fixture
def make_tree(db_session):
    """
    Insert a RepoTreeRoot + RepoTreeEntry rows for a given {path: blob_hash} map.
    Returns the RepoTreeRoot.

    Content-addressed: if two calls within the same test use identical blobs,
    the same tree root is returned (no duplicate-key error).
    """
    import hashlib

    def _make(blobs: dict[str, str]):
        from shared.constants import NodeType
        from shared.models.workflow import RepoTreeEntry, RepoTreeRoot

        combined = ",".join(f"{k}:{v}" for k, v in sorted(blobs.items()))
        tree_hash = hashlib.sha256(combined.encode()).hexdigest()

        # Return existing root if already present (same blobs → same tree)
        existing = db_session.exec(
            select(RepoTreeRoot).where(RepoTreeRoot.tree_hash == tree_hash)
        ).first()
        if existing:
            return existing

        tree_root = RepoTreeRoot(tree_hash=tree_hash)
        db_session.add(tree_root)
        db_session.commit()
        db_session.refresh(tree_root)

        for path, blob_hash in blobs.items():
            entry = RepoTreeEntry(
                tree_id=tree_root.id,
                type=NodeType.blob,
                name=path,
                content_hash=blob_hash,
            )
            db_session.add(entry)
        db_session.commit()
        return tree_root
    return _make


# ── EFS file seeder ──────────────────────────────────────────────────────────

@pytest.fixture
def seed_file(tmp_efs):
    """
    Returns a helper that physically writes a file into the temp EFS directory.

    Usage:
        seed_file(user_id, repo_id, draft_id, "path/file.txt", b"content")
    """
    def _seed(
        user_id: str,
        repo_id: str,
        draft_id: str,
        path: str,
        content: bytes = b"hello",
    ):
        full_path = Path(tmp_efs) / user_id / repo_id / draft_id / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

    return _seed


@pytest.fixture
def seed_deleted_marker(tmp_efs):
    """
    Returns a helper that creates a zero-byte .deleted marker in the temp EFS directory.

    Usage:
        seed_deleted_marker(user_id, repo_id, draft_id, "docs")
        # creates {tmp_efs}/{user_id}/{repo_id}/{draft_id}/docs.deleted
    """
    def _seed(
        user_id: str,
        repo_id: str,
        draft_id: str,
        path: str,
    ):
        marker = Path(tmp_efs) / user_id / repo_id / draft_id / (path + ".deleted")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"")

    return _seed
