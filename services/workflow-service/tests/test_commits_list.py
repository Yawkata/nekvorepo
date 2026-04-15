"""
Tests for GET /v1/repos/{repo_id}/commits — list pending commits (reviewer queue).

Coverage:
  Happy path   — 200, empty list, only pending commits returned, response shape
  Role checks  — admin/reviewer allowed; author/reader/non-member → 403
  Auth         — no token, expired
"""

from shared.constants import CommitStatus

_URL = "/v1/repos/{repo_id}/commits"


def _url(repo_id):
    return _URL.format(repo_id=repo_id)


class TestListCommitsSuccess:
    def test_returns_200(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_empty_list_when_no_commits(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).json() == []

    def test_returns_pending_commits(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        make_commit(repo_id=repo.id, status=CommitStatus.pending)
        r = client.get(_url(repo.id), headers=auth_headers())
        assert len(r.json()) == 1

    def test_excludes_approved_commits(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        make_commit(repo_id=repo.id, status=CommitStatus.approved)
        assert client.get(_url(repo.id), headers=auth_headers()).json() == []

    def test_excludes_rejected_commits(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        make_commit(repo_id=repo.id, status=CommitStatus.rejected)
        assert client.get(_url(repo.id), headers=auth_headers()).json() == []

    def test_excludes_sibling_rejected_commits(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        make_commit(repo_id=repo.id, status=CommitStatus.sibling_rejected)
        assert client.get(_url(repo.id), headers=auth_headers()).json() == []

    def test_response_item_shape(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        make_commit(repo_id=repo.id)
        item = client.get(_url(repo.id), headers=auth_headers()).json()[0]
        for field in ("commit_hash", "status", "commit_summary", "owner_id", "timestamp", "draft_id"):
            assert field in item

    def test_multiple_pending_commits_all_returned(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        make_commit(repo_id=repo.id, commit_summary="Commit 1")
        make_commit(repo_id=repo.id, commit_summary="Commit 2")
        assert len(client.get(_url(repo.id), headers=auth_headers()).json()) == 2

    def test_only_returns_commits_for_this_repo(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo_a = make_repo(repo_name="repo-a")
        repo_b = make_repo(repo_name="repo-b", owner_id="other-user")
        make_commit(repo_id=repo_b.id)
        assert client.get(_url(repo_a.id), headers=auth_headers()).json() == []


class TestListCommitsRoles:
    def test_admin_can_list(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_reviewer_can_list(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_author_cannot_list(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 403

    def test_reader_cannot_list(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 403

    def test_non_member_cannot_list(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = None
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 403


class TestListCommitsAuth:
    def test_no_token_returns_401(self, client, make_repo):
        repo = make_repo()
        assert client.get(_url(repo.id)).status_code == 401

    def test_expired_token_returns_401(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers(expired=True)).status_code == 401
