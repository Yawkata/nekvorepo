"""
Tests for DELETE /v1/repos/{repo_id}/drafts/{draft_id}/files/{path} — mark file/folder deleted.

Coverage:
  Happy path  — 204, .deleted marker created, auto-reopen rejected draft
  Folder      — marks entire subtree via single .deleted marker
  Status gates — committing → 409, pending/approved/sibling_rejected → 400
  Error cases — 403 wrong owner, 404 draft not found, 400 .deleted path
  Auth        — no token → 401
"""

from pathlib import Path

from shared.constants import DraftStatus

_URL = "/v1/repos/{repo_id}/drafts/{draft_id}/files/{path}"
_USER_ID = "test-user"
_OTHER_ID = "other-user"


def _url(repo_id, draft_id, path):
    return _URL.format(repo_id=repo_id, draft_id=draft_id, path=path)


class TestDeleteFileSuccess:
    def test_returns_204(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "file.txt", b"content")
        r = client.delete(_url(repo.id, draft.id, "file.txt"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 204

    def test_deleted_marker_created(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "notes.txt", b"hello")
        client.delete(_url(repo.id, draft.id, "notes.txt"), headers=auth_headers(user_id=_USER_ID))
        marker = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "notes.txt.deleted"
        assert marker.exists()

    def test_folder_delete_creates_marker(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "src/main.py", b"code")
        # Mark the entire src/ folder
        r = client.delete(_url(repo.id, draft.id, "src"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 204
        marker = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "src.deleted"
        assert marker.exists()

    def test_auto_reopen_rejected_draft(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file, db_session):
        from sqlmodel import select
        from shared.models.repo import Draft
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.rejected)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "old.txt", b"old")
        client.delete(_url(repo.id, draft.id, "old.txt"), headers=auth_headers(user_id=_USER_ID))
        db_session.expire_all()
        updated = db_session.exec(select(Draft).where(Draft.id == draft.id)).first()
        assert updated.status == DraftStatus.editing


class TestDeleteFileStatusGates:
    def test_committing_returns_409(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.committing)
        r = client.delete(_url(repo.id, draft.id, "file.txt"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 409

    def test_pending_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.pending)
        r = client.delete(_url(repo.id, draft.id, "file.txt"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 400

    def test_approved_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.approved)
        r = client.delete(_url(repo.id, draft.id, "file.txt"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 400

    def test_sibling_rejected_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.sibling_rejected)
        r = client.delete(_url(repo.id, draft.id, "file.txt"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 400


class TestDeleteFileErrors:
    def test_deleted_path_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.delete(_url(repo.id, draft.id, "file.txt.deleted"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 400

    def test_wrong_owner_returns_403(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_OTHER_ID)
        r = client.delete(_url(repo.id, draft.id, "file.txt"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 403

    def test_draft_not_found_returns_404(self, client, mock_identity_client, auth_headers, make_repo):
        import uuid
        repo = make_repo()
        assert client.delete(_url(repo.id, uuid.uuid4(), "file.txt"), headers=auth_headers()).status_code == 404


class TestDeleteFileAuth:
    def test_no_token_returns_401(self, client, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        assert client.delete(_url(repo.id, draft.id, "file.txt")).status_code == 401
