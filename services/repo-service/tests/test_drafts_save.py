"""
Tests for POST /v1/repos/{repo_id}/drafts/{draft_id}/save — save a text file.

Coverage:
  Happy path   — 200, path/size/large_file_warning, file written to EFS
  Auto-reopen  — rejected draft → editing on first write
  Status gates — committing → 409, pending/approved/sibling_rejected → 400
  Validation   — content > 5 MB → 413, .deleted path → 400
  Error cases  — 403 wrong owner
  Auth         — no token → 401
"""

from pathlib import Path

from sqlmodel import select

from shared.constants import DraftStatus
from shared.models.repo import Draft

_URL = "/v1/repos/{repo_id}/drafts/{draft_id}/save"
_USER_ID = "test-user"
_OTHER_ID = "other-user"


def _url(repo_id, draft_id):
    return _URL.format(repo_id=repo_id, draft_id=draft_id)


class TestSaveFileSuccess:
    def test_returns_200(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _url(repo.id, draft.id),
            json={"path": "readme.txt", "content": "Hello"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 200

    def test_response_has_path_size_warning(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        data = client.post(
            _url(repo.id, draft.id),
            json={"path": "file.txt", "content": "content"},
            headers=auth_headers(user_id=_USER_ID),
        ).json()
        assert "path" in data
        assert "size" in data
        assert "large_file_warning" in data

    def test_file_written_to_efs(self, client, mock_identity_client, auth_headers, make_repo, make_draft, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        client.post(
            _url(repo.id, draft.id),
            json={"path": "notes.txt", "content": "my notes"},
            headers=auth_headers(user_id=_USER_ID),
        )
        file_path = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "notes.txt"
        assert file_path.exists()
        assert file_path.read_bytes() == b"my notes"

    def test_size_in_response(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        content = "hello world"
        data = client.post(
            _url(repo.id, draft.id),
            json={"path": "f.txt", "content": content},
            headers=auth_headers(user_id=_USER_ID),
        ).json()
        assert data["size"] == len(content.encode("utf-8"))

    def test_stale_deleted_marker_removed_on_write(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, tmp_efs, seed_file
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        # Create a stale .deleted marker for the file
        marker = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "file.txt.deleted"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        client.post(
            _url(repo.id, draft.id),
            json={"path": "file.txt", "content": "restored"},
            headers=auth_headers(user_id=_USER_ID),
        )
        # The .deleted marker should be gone
        assert not marker.exists()

    def test_large_file_warning_true(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        large_content = "x" * (1024 * 1024 + 1)  # just over 1 MB
        data = client.post(
            _url(repo.id, draft.id),
            json={"path": "big.txt", "content": large_content},
            headers=auth_headers(user_id=_USER_ID),
        ).json()
        assert data["large_file_warning"] is True

    def test_large_file_warning_false(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        data = client.post(
            _url(repo.id, draft.id),
            json={"path": "small.txt", "content": "tiny"},
            headers=auth_headers(user_id=_USER_ID),
        ).json()
        assert data["large_file_warning"] is False

    def test_needs_rebase_draft_is_writable(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.needs_rebase)
        r = client.post(
            _url(repo.id, draft.id),
            json={"path": "f.txt", "content": "ok"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 200


class TestSaveFileAutoReopen:
    def test_rejected_draft_reopened_to_editing(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, db_session
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.rejected)
        client.post(
            _url(repo.id, draft.id),
            json={"path": "fix.txt", "content": "fixed"},
            headers=auth_headers(user_id=_USER_ID),
        )
        db_session.expire_all()
        updated = db_session.exec(select(Draft).where(Draft.id == draft.id)).first()
        assert updated.status == DraftStatus.editing


class TestSaveFileStatusGates:
    def test_committing_returns_409(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.committing)
        r = client.post(
            _url(repo.id, draft.id),
            json={"path": "f.txt", "content": "x"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 409

    def test_pending_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.pending)
        r = client.post(
            _url(repo.id, draft.id),
            json={"path": "f.txt", "content": "x"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 400

    def test_approved_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.approved)
        r = client.post(
            _url(repo.id, draft.id),
            json={"path": "f.txt", "content": "x"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 400

    def test_sibling_rejected_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.sibling_rejected)
        r = client.post(
            _url(repo.id, draft.id),
            json={"path": "f.txt", "content": "x"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 400


class TestSaveFileValidation:
    def test_content_too_large_returns_413(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        oversized = "x" * (5 * 1024 * 1024 + 1)
        r = client.post(
            _url(repo.id, draft.id),
            json={"path": "big.txt", "content": oversized},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 413

    def test_deleted_path_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _url(repo.id, draft.id),
            json={"path": "file.txt.deleted", "content": "x"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 400

    def test_wrong_owner_returns_403(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_OTHER_ID)
        r = client.post(
            _url(repo.id, draft.id),
            json={"path": "f.txt", "content": "x"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 403


class TestSaveFileAuth:
    def test_no_token_returns_401(self, client, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        assert client.post(_url(repo.id, draft.id), json={"path": "f.txt", "content": "x"}).status_code == 401
