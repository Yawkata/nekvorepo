"""
Tests for POST /v1/repos/{repo_id}/drafts/{draft_id}/upload — upload a binary file.

Coverage:
  Happy path   — 200, path/size/is_binary, file written to EFS, auto-reopen rejected
  Status gates — committing → 409, pending/sibling_rejected/reconstructing → 400
  Validation   — file > 100 MB → 413
  Error cases  — 403 wrong owner
  Auth         — no token → 401
"""

from pathlib import Path

from shared.constants import DraftStatus

_URL = "/v1/repos/{repo_id}/drafts/{draft_id}/upload"
_USER_ID = "test-user"
_OTHER_ID = "other-user"


def _url(repo_id, draft_id):
    return _URL.format(repo_id=repo_id, draft_id=draft_id)


def _upload(client, repo_id, draft_id, path: str, content: bytes, headers: dict):
    return client.post(
        _url(repo_id, draft_id),
        data={"path": path},
        files={"file": ("filename", content, "application/octet-stream")},
        headers=headers,
    )


class TestUploadFileSuccess:
    def test_returns_200(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = _upload(client, repo.id, draft.id, "img.bin", b"\x00\x01\x02", auth_headers(user_id=_USER_ID))
        assert r.status_code == 200

    def test_response_has_path_size_is_binary(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        data = _upload(client, repo.id, draft.id, "data.bin", b"\x00\xff", auth_headers(user_id=_USER_ID)).json()
        assert "path" in data
        assert "size" in data
        assert "is_binary" in data

    def test_binary_file_is_binary_true(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        data = _upload(client, repo.id, draft.id, "bin.dat", b"\x00\x01\x02\x03", auth_headers(user_id=_USER_ID)).json()
        assert data["is_binary"] is True

    def test_text_file_is_binary_false(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        data = _upload(client, repo.id, draft.id, "note.txt", b"plain text", auth_headers(user_id=_USER_ID)).json()
        assert data["is_binary"] is False

    def test_file_written_to_efs(self, client, mock_identity_client, auth_headers, make_repo, make_draft, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        _upload(client, repo.id, draft.id, "upload.bin", b"\xde\xad\xbe\xef", auth_headers(user_id=_USER_ID))
        file_path = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "upload.bin"
        assert file_path.exists()
        assert file_path.read_bytes() == b"\xde\xad\xbe\xef"

    def test_needs_rebase_draft_is_writable(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.needs_rebase)
        r = _upload(client, repo.id, draft.id, "f.bin", b"data", auth_headers(user_id=_USER_ID))
        assert r.status_code == 200


class TestUploadFileAutoReopen:
    def test_rejected_draft_reopened_to_editing(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, db_session
    ):
        """Upload to a rejected draft must auto-reopen it to editing (mirrors save behaviour)."""
        from sqlmodel import select
        from shared.models.repo import Draft
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.rejected)
        _upload(client, repo.id, draft.id, "fix.bin", b"\x01\x02", auth_headers(user_id=_USER_ID))
        db_session.expire_all()
        updated = db_session.exec(select(Draft).where(Draft.id == draft.id)).first()
        assert updated.status == DraftStatus.editing


class TestUploadFileStatusGates:
    def test_committing_returns_409(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.committing)
        r = _upload(client, repo.id, draft.id, "f.bin", b"data", auth_headers(user_id=_USER_ID))
        assert r.status_code == 409

    def test_approved_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.approved)
        r = _upload(client, repo.id, draft.id, "f.bin", b"data", auth_headers(user_id=_USER_ID))
        assert r.status_code == 400

    def test_pending_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.pending)
        r = _upload(client, repo.id, draft.id, "f.bin", b"data", auth_headers(user_id=_USER_ID))
        assert r.status_code == 400

    def test_sibling_rejected_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.sibling_rejected)
        r = _upload(client, repo.id, draft.id, "f.bin", b"data", auth_headers(user_id=_USER_ID))
        assert r.status_code == 400

    def test_reconstructing_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.reconstructing)
        r = _upload(client, repo.id, draft.id, "f.bin", b"data", auth_headers(user_id=_USER_ID))
        assert r.status_code == 400


class TestUploadFileValidation:
    def test_oversized_file_returns_413(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        # 100 MB + 1 byte
        big = b"x" * (100 * 1024 * 1024 + 1)
        r = _upload(client, repo.id, draft.id, "huge.bin", big, auth_headers(user_id=_USER_ID))
        assert r.status_code == 413

    def test_deleted_path_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = _upload(client, repo.id, draft.id, "file.bin.deleted", b"data", auth_headers(user_id=_USER_ID))
        assert r.status_code == 400

    def test_wrong_owner_returns_403(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_OTHER_ID)
        r = _upload(client, repo.id, draft.id, "f.bin", b"data", auth_headers(user_id=_USER_ID))
        assert r.status_code == 403


class TestUploadFileAuth:
    def test_no_token_returns_401(self, client, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _url(repo.id, draft.id),
            data={"path": "f.bin"},
            files={"file": ("f", b"data", "application/octet-stream")},
        )
        assert r.status_code == 401
