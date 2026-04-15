"""
Tests for DELETE /v1/repos/{repo_id}/drafts/{draft_id} — hard-delete a draft.

Coverage:
  Happy path      — 204, DB row removed, EFS directory wiped
  Allowed status  — editing, needs_rebase, approved, rejected → 204
  Blocked status  — pending, committing, reconstructing, sibling_rejected → 409
  Error cases     — 404, 403 wrong owner
  Auth            — no token → 401
"""

from pathlib import Path

from sqlmodel import select

from shared.constants import DraftStatus
from shared.models.repo import Draft

_URL = "/v1/repos/{repo_id}/drafts/{draft_id}"
_USER_ID = "test-user"
_OTHER_ID = "other-user"


def _url(repo_id, draft_id):
    return _URL.format(repo_id=repo_id, draft_id=draft_id)


class TestDeleteDraftSuccess:
    def test_returns_204(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.editing)
        r = client.delete(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 204

    def test_draft_removed_from_db(self, client, mock_identity_client, auth_headers, make_repo, make_draft, db_session):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.editing)
        draft_id = draft.id  # capture before delete; accessing after raises ObjectDeletedError
        client.delete(_url(repo.id, draft_id), headers=auth_headers(user_id=_USER_ID))
        db_session.expire_all()
        gone = db_session.exec(select(Draft).where(Draft.id == draft_id)).first()
        assert gone is None

    def test_efs_directory_wiped(self, client, mock_identity_client, auth_headers, make_repo, make_draft, tmp_efs, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.editing)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "file.txt")
        draft_dir = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id)
        assert draft_dir.exists()
        client.delete(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID))
        assert not draft_dir.exists()

    def test_needs_rebase_can_be_deleted(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.needs_rebase)
        assert client.delete(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).status_code == 204

    def test_approved_can_be_deleted(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.approved)
        assert client.delete(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).status_code == 204

    def test_rejected_can_be_deleted(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.rejected)
        assert client.delete(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).status_code == 204


class TestDeleteDraftBlocked:
    def test_pending_returns_409(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.pending)
        assert client.delete(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).status_code == 409

    def test_committing_returns_409(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.committing)
        assert client.delete(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).status_code == 409

    def test_reconstructing_returns_409(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.reconstructing)
        assert client.delete(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).status_code == 409

    def test_sibling_rejected_returns_409(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.sibling_rejected)
        assert client.delete(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).status_code == 409


class TestDeleteDraftErrors:
    def test_not_found_returns_404(self, client, mock_identity_client, auth_headers, make_repo):
        import uuid
        repo = make_repo()
        assert client.delete(_url(repo.id, uuid.uuid4()), headers=auth_headers()).status_code == 404

    def test_wrong_owner_returns_403(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_OTHER_ID, status=DraftStatus.editing)
        assert client.delete(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).status_code == 403


class TestDeleteDraftAuth:
    def test_no_token_returns_401(self, client, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        assert client.delete(_url(repo.id, draft.id)).status_code == 401
