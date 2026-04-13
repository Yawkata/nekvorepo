"""
Tests for GET /v1/repos/{repo_id}/commits/history — full commit history.

Coverage:
  Happy path   — 200, empty list, resolved statuses only, response shape, ordering
  Role checks  — all member roles allowed; non-member → 403
  Auth         — no token, expired
"""

from shared.constants import CommitStatus

_URL = "/v1/repos/{repo_id}/commits/history"


def _url(repo_id):
    return _URL.format(repo_id=repo_id)


class TestCommitHistorySuccess:
    def test_returns_200(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_empty_list_when_no_resolved_commits(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).json() == []

    def test_excludes_pending_commits(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        repo = make_repo()
        make_commit(repo_id=repo.id, status=CommitStatus.pending)
        assert client.get(_url(repo.id), headers=auth_headers()).json() == []

    def test_includes_approved_commits(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        repo = make_repo()
        make_commit(repo_id=repo.id, status=CommitStatus.approved)
        assert len(client.get(_url(repo.id), headers=auth_headers()).json()) == 1

    def test_includes_rejected_commits(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        repo = make_repo()
        make_commit(repo_id=repo.id, status=CommitStatus.rejected)
        assert len(client.get(_url(repo.id), headers=auth_headers()).json()) == 1

    def test_includes_sibling_rejected_commits(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        repo = make_repo()
        make_commit(repo_id=repo.id, status=CommitStatus.sibling_rejected)
        assert len(client.get(_url(repo.id), headers=auth_headers()).json()) == 1

    def test_response_item_shape(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        repo = make_repo()
        make_commit(repo_id=repo.id, status=CommitStatus.approved)
        item = client.get(_url(repo.id), headers=auth_headers()).json()[0]
        for field in ("commit_hash", "status", "commit_summary", "owner_id",
                      "timestamp", "parent_commit_hash", "reviewer_comment", "draft_id"):
            assert field in item

    def test_sorted_newest_first(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        repo = make_repo()
        c1 = make_commit(repo_id=repo.id, status=CommitStatus.approved, commit_summary="First")
        c2 = make_commit(repo_id=repo.id, status=CommitStatus.approved, commit_summary="Second")
        items = client.get(_url(repo.id), headers=auth_headers()).json()
        # Second commit was inserted later → should appear first
        assert items[0]["commit_hash"] == c2.commit_hash
        assert items[1]["commit_hash"] == c1.commit_hash

    def test_only_returns_commits_for_this_repo(self, client, mock_identity_client, auth_headers, make_repo, make_commit):
        repo_a = make_repo(repo_name="repo-a")
        repo_b = make_repo(repo_name="repo-b", owner_id="other-user")
        make_commit(repo_id=repo_b.id, status=CommitStatus.approved)
        assert client.get(_url(repo_a.id), headers=auth_headers()).json() == []


class TestCommitHistoryRoles:
    def test_admin_can_view(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_author_can_view(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_reviewer_can_view(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_reader_can_view(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_non_member_cannot_view(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = None
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 403


class TestCommitHistoryAuth:
    def test_no_token_returns_401(self, client, make_repo):
        repo = make_repo()
        assert client.get(_url(repo.id)).status_code == 401

    def test_expired_token_returns_401(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers(expired=True)).status_code == 401
