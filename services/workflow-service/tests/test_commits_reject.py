"""
Tests for POST /v1/repos/{repo_id}/commits/{commit_hash}/reject

Coverage:
  Happy path    — 200, response shape, DB state, comment stored, draft updated
  Role checks   — admin/reviewer allowed; author/reader/non-member → 403
  State guards  — commit not found, already approved, already rejected
  Validation    — comment too long
  Auth          — no token, expired
"""

from sqlmodel import select

from shared.constants import CommitStatus, DraftStatus
from shared.models.workflow import RepoCommit
from shared.models.repo import Draft

_URL = "/v1/repos/{repo_id}/commits/{commit_hash}/reject"
_AUTHOR_ID   = "author-user-sub"
_REVIEWER_ID = "reviewer-user-sub"


def _url(repo_id, commit_hash):
    return _URL.format(repo_id=repo_id, commit_hash=commit_hash)


class TestRejectCommitSuccess:
    def test_returns_200(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        r = client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers(user_id=_REVIEWER_ID))
        assert r.status_code == 200

    def test_response_has_commit_hash_and_status(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        r = client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers(user_id=_REVIEWER_ID))
        data = r.json()
        assert data["commit_hash"] == commit.commit_hash
        assert data["status"] == "rejected"

    def test_commit_status_updated_in_db(self, client, mock_identity_client, auth_headers, make_repo, make_commit, db_session):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()
        updated = db_session.exec(select(RepoCommit).where(RepoCommit.commit_hash == commit.commit_hash)).first()
        assert updated.status == CommitStatus.rejected

    def test_reviewer_comment_stored(self, client, mock_identity_client, auth_headers, make_repo, make_commit, db_session):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        client.post(_url(repo.id, commit.commit_hash), json={"comment": "Needs more tests"}, headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()
        updated = db_session.exec(select(RepoCommit).where(RepoCommit.commit_hash == commit.commit_hash)).first()
        assert updated.reviewer_comment == "Needs more tests"

    def test_works_without_comment(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        assert client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers(user_id=_REVIEWER_ID)).status_code == 200

    def test_null_comment_stored_as_none(self, client, mock_identity_client, auth_headers, make_repo, make_commit, db_session):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()
        updated = db_session.exec(select(RepoCommit).where(RepoCommit.commit_hash == commit.commit_hash)).first()
        assert updated.reviewer_comment is None

    def test_associated_draft_marked_rejected(self, client, mock_identity_client, auth_headers, make_repo, make_draft, make_commit, db_session):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_AUTHOR_ID, status=DraftStatus.pending)
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, draft_id=draft.id)
        client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()
        assert db_session.get(Draft, draft.id).status == DraftStatus.rejected

    def test_works_with_no_draft(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, draft_id=None)
        assert client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers(user_id=_REVIEWER_ID)).status_code == 200


class TestRejectCommitRoles:
    def test_admin_can_reject(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        assert client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers(user_id=_REVIEWER_ID)).status_code == 200

    def test_reviewer_can_reject(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        assert client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers(user_id=_REVIEWER_ID)).status_code == 200

    def test_author_cannot_reject(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id="someone-else")
        assert client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers()).status_code == 403

    def test_reader_cannot_reject(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id="someone-else")
        assert client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers()).status_code == 403

    def test_non_member_cannot_reject(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = None
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id="someone-else")
        assert client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers()).status_code == 403


class TestRejectCommitStateGuards:
    def test_commit_not_found_returns_404(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        fake_hash = "f" * 64
        assert client.post(_url(repo.id, fake_hash), json={}, headers=auth_headers(user_id=_REVIEWER_ID)).status_code == 404

    def test_already_approved_returns_409(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, status=CommitStatus.approved)
        assert client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers(user_id=_REVIEWER_ID)).status_code == 409

    def test_already_rejected_returns_409(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, status=CommitStatus.rejected)
        assert client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers(user_id=_REVIEWER_ID)).status_code == 409


class TestRejectCommitValidation:
    def test_comment_too_long_returns_422(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        r = client.post(_url(repo.id, commit.commit_hash), json={"comment": "x" * 501}, headers=auth_headers(user_id=_REVIEWER_ID))
        assert r.status_code == 422


class TestRejectCommitAuth:
    def test_no_token_returns_401(self, client, make_repo, make_commit):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        assert client.post(_url(repo.id, commit.commit_hash), json={}).status_code == 401

    def test_expired_token_returns_401(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        assert client.post(_url(repo.id, commit.commit_hash), json={}, headers=auth_headers(expired=True)).status_code == 401
