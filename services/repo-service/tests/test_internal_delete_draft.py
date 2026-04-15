"""
Tests for DELETE /v1/internal/drafts/{draft_id} — idempotent EFS wipe.

Coverage:
  Happy path  — 204, EFS directory removed
  Idempotent  — missing directory still returns 204
  Validation  — missing query params → 422
  No auth required
"""

import uuid
from pathlib import Path

_URL = "/v1/internal/drafts/{draft_id}"
_USER_ID = "user-1"


def _url(draft_id, repo_id, user_id):
    return f"{_URL.format(draft_id=draft_id)}?user_id={user_id}&repo_id={repo_id}"


class TestWipeDraftSuccess:
    def test_returns_204(self, client, make_repo, make_draft, seed_file, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "file.txt", b"data")
        r = client.delete(_url(draft.id, repo.id, _USER_ID))
        assert r.status_code == 204

    def test_efs_directory_wiped(self, client, make_repo, make_draft, seed_file, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "main.py", b"code")
        draft_dir = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id)
        assert draft_dir.exists()
        client.delete(_url(draft.id, repo.id, _USER_ID))
        assert not draft_dir.exists()

    def test_idempotent_missing_dir(self, client, make_repo, make_draft):
        """Directory doesn't exist yet — should still return 204."""
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.delete(_url(draft.id, repo.id, _USER_ID))
        assert r.status_code == 204

    def test_no_auth_required(self, client, make_repo, make_draft):
        """Internal endpoint — no Passport JWT needed."""
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.delete(_url(draft.id, repo.id, _USER_ID))
        assert r.status_code != 401

    def test_second_call_still_returns_204(self, client, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "f.txt", b"x")
        client.delete(_url(draft.id, repo.id, _USER_ID))
        r = client.delete(_url(draft.id, repo.id, _USER_ID))
        assert r.status_code == 204


class TestWipeDraftValidation:
    def test_missing_user_id_returns_422(self, client):
        draft_id = uuid.uuid4()
        repo_id = uuid.uuid4()
        r = client.delete(f"/v1/internal/drafts/{draft_id}?repo_id={repo_id}")
        assert r.status_code == 422

    def test_missing_repo_id_returns_422(self, client):
        draft_id = uuid.uuid4()
        r = client.delete(f"/v1/internal/drafts/{draft_id}?user_id=someone")
        assert r.status_code == 422
