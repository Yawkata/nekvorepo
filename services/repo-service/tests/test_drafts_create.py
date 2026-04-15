"""
Tests for POST /v1/repos/{repo_id}/drafts — create a new draft.

Two modes:
  Mode B — empty draft (no source_draft_id)
  Mode A — copy from existing draft (source_draft_id provided)

Coverage:
  Happy path   — 201, response shape, EFS dir created, DB row persisted
  Status logic — editing when no commits, reconstructing when repo has commits
  Mode A       — copy EFS, inherit base_commit_hash, status logic
  Validation   — label too long → 422, repo not found → 404
  Auth         — no token → 401, expired → 401

Note: role-based access for this endpoint is tested exhaustively in
test_drafts_role_access.py::TestCreateDraftRoleAccess.
"""

import os
from pathlib import Path

from shared.constants import DraftStatus

_URL = "/v1/repos/{repo_id}/drafts"
_USER_ID = "test-user"


def _url(repo_id):
    return _URL.format(repo_id=repo_id)


class TestCreateDraftSuccess:
    def test_returns_201(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        r = client.post(_url(repo.id), json={}, headers=auth_headers())
        assert r.status_code == 201

    def test_response_shape(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        data = client.post(_url(repo.id), json={}, headers=auth_headers()).json()
        for field in ("draft_id", "repo_id", "user_id", "label", "status", "base_commit_hash",
                      "commit_hash", "created_at", "updated_at"):
            assert field in data

    def test_response_repo_id_matches(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        data = client.post(_url(repo.id), json={}, headers=auth_headers()).json()
        assert data["repo_id"] == str(repo.id)

    def test_custom_label_stored(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        data = client.post(_url(repo.id), json={"label": "My Draft"}, headers=auth_headers()).json()
        assert data["label"] == "My Draft"

    def test_auto_label_when_none(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        data = client.post(_url(repo.id), json={}, headers=auth_headers()).json()
        assert data["label"] is not None
        assert "Draft" in data["label"]

    def test_status_editing_when_no_commits(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo(latest_commit_hash=None)
        data = client.post(_url(repo.id), json={}, headers=auth_headers()).json()
        assert data["status"] == "editing"

    def test_status_reconstructing_when_repo_has_commits(
        self, client, mock_identity_client, auth_headers, make_repo, db_session
    ):
        """When the repo already has a latest_commit_hash the new draft starts in
        'reconstructing' so the background daemon can reconstruct it from S3."""
        repo = make_repo(latest_commit_hash="a" * 64)
        data = client.post(_url(repo.id), json={}, headers=auth_headers()).json()
        assert data["status"] == "reconstructing"

    def test_efs_directory_created(self, client, mock_identity_client, auth_headers, make_repo, tmp_efs):
        repo = make_repo()
        data = client.post(_url(repo.id), json={}, headers=auth_headers(user_id=_USER_ID)).json()
        draft_id = data["draft_id"]
        expected_dir = Path(tmp_efs) / _USER_ID / str(repo.id) / draft_id
        assert expected_dir.exists()
        assert expected_dir.is_dir()

    def test_draft_persisted_in_db(self, client, mock_identity_client, auth_headers, make_repo, db_session):
        from shared.models.repo import Draft
        from sqlmodel import select
        repo = make_repo()
        data = client.post(_url(repo.id), json={}, headers=auth_headers()).json()
        db_session.expire_all()
        draft = db_session.exec(
            select(Draft).where(Draft.repo_id == repo.id)
        ).first()
        assert draft is not None
        assert str(draft.id) == data["draft_id"]


class TestCreateDraftModeA:
    def test_from_source_returns_201(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, tmp_efs, seed_file
    ):
        repo = make_repo()
        src = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(src.id), "readme.txt", b"hello")
        r = client.post(
            _url(repo.id),
            json={"source_draft_id": str(src.id)},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 201

    def test_efs_copied_from_source(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, tmp_efs, seed_file
    ):
        repo = make_repo()
        src = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(src.id), "notes.txt", b"content")
        data = client.post(
            _url(repo.id),
            json={"source_draft_id": str(src.id)},
            headers=auth_headers(user_id=_USER_ID),
        ).json()
        new_dir = Path(tmp_efs) / _USER_ID / str(repo.id) / data["draft_id"]
        assert (new_dir / "notes.txt").exists()

    def test_source_not_found_returns_404(
        self, client, mock_identity_client, auth_headers, make_repo
    ):
        import uuid
        repo = make_repo()
        r = client.post(
            _url(repo.id),
            json={"source_draft_id": str(uuid.uuid4())},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 404

    def test_inherits_base_commit_hash_from_source(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, db_session
    ):
        from shared.models.repo import Draft
        from sqlmodel import select
        repo = make_repo()
        src = make_draft(repo_id=repo.id, user_id=_USER_ID, base_commit_hash="a" * 64)
        data = client.post(
            _url(repo.id),
            json={"source_draft_id": str(src.id)},
            headers=auth_headers(user_id=_USER_ID),
        ).json()
        db_session.expire_all()
        new_draft = db_session.exec(
            select(Draft).where(Draft.id == data["draft_id"])
        ).first()
        assert new_draft.base_commit_hash == "a" * 64


class TestCreateDraftValidation:
    def test_label_too_long_returns_422(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        r = client.post(_url(repo.id), json={"label": "x" * 101}, headers=auth_headers())
        assert r.status_code == 422

    def test_repo_not_found_returns_404(self, client, mock_identity_client, auth_headers):
        import uuid
        fake_id = uuid.uuid4()
        r = client.post(_url(fake_id), json={}, headers=auth_headers())
        assert r.status_code == 404


class TestCreateDraftAuth:
    def test_no_token_returns_401(self, client, make_repo):
        repo = make_repo()
        assert client.post(_url(repo.id), json={}).status_code == 401

    def test_expired_token_returns_401(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        assert client.post(_url(repo.id), json={}, headers=auth_headers(expired=True)).status_code == 401
