"""
Tests for POST /v1/repos/{repo_id}/drafts/{draft_id}/mkdir — create an empty folder.

Coverage:
  Happy path  — 201, path + keep_file in response, .keep file created in EFS
  Idempotent  — second mkdir on same path returns 201 (re-writes .keep)
  Error cases — 403 wrong owner, 400 .deleted path, status gates
  Validation  — path too long → 422
  Auth        — no token → 401
"""

from pathlib import Path

from shared.constants import DraftStatus

_URL = "/v1/repos/{repo_id}/drafts/{draft_id}/mkdir"
_USER_ID = "test-user"
_OTHER_ID = "other-user"


def _url(repo_id, draft_id):
    return _URL.format(repo_id=repo_id, draft_id=draft_id)


class TestMkdirSuccess:
    def test_returns_201(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(_url(repo.id, draft.id), json={"path": "src"}, headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 201

    def test_response_has_path_and_keep_file(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        data = client.post(_url(repo.id, draft.id), json={"path": "docs"}, headers=auth_headers(user_id=_USER_ID)).json()
        assert data["path"] == "docs"
        assert data["keep_file"] == "docs/.keep"

    def test_keep_file_created_in_efs(self, client, mock_identity_client, auth_headers, make_repo, make_draft, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        client.post(_url(repo.id, draft.id), json={"path": "src/components"}, headers=auth_headers(user_id=_USER_ID))
        keep = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "src/components/.keep"
        assert keep.exists()

    def test_idempotent_second_mkdir(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r1 = client.post(_url(repo.id, draft.id), json={"path": "lib"}, headers=auth_headers(user_id=_USER_ID))
        r2 = client.post(_url(repo.id, draft.id), json={"path": "lib"}, headers=auth_headers(user_id=_USER_ID))
        assert r1.status_code == 201
        assert r2.status_code == 201

    def test_nested_path(self, client, mock_identity_client, auth_headers, make_repo, make_draft, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        client.post(_url(repo.id, draft.id), json={"path": "a/b/c"}, headers=auth_headers(user_id=_USER_ID))
        keep = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "a/b/c/.keep"
        assert keep.exists()

    def test_needs_rebase_draft_is_writable(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.needs_rebase)
        r = client.post(_url(repo.id, draft.id), json={"path": "dir"}, headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 201


class TestMkdirErrors:
    def test_deleted_path_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(_url(repo.id, draft.id), json={"path": "folder.deleted"}, headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 400

    def test_committing_returns_409(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.committing)
        r = client.post(_url(repo.id, draft.id), json={"path": "dir"}, headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 409

    def test_wrong_owner_returns_403(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_OTHER_ID)
        r = client.post(_url(repo.id, draft.id), json={"path": "dir"}, headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 403


class TestMkdirValidation:
    def test_path_too_long_returns_422(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        long_path = "a/" * 2049  # > 4096 chars
        r = client.post(_url(repo.id, draft.id), json={"path": long_path}, headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 422

    def test_empty_path_returns_422(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(_url(repo.id, draft.id), json={"path": ""}, headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 422


class TestMkdirAuth:
    def test_no_token_returns_401(self, client, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        assert client.post(_url(repo.id, draft.id), json={"path": "dir"}).status_code == 401
