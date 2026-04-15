"""
Tests for GET /v1/repos/{repo_id}/drafts — list caller's own drafts.

Coverage:
  Happy path  — 200, empty list, response shape, only own drafts returned
  Ordering    — active before resolved, deleted excluded
  Role checks — all member roles → 200; non-member → 403
  Auth        — no token → 401
"""

from shared.constants import DraftStatus

_URL = "/v1/repos/{repo_id}/drafts"
_USER_ID = "test-user"
_OTHER_ID = "other-user"


def _url(repo_id):
    return _URL.format(repo_id=repo_id)


class TestListDraftsSuccess:
    def test_returns_200(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_empty_list_when_no_drafts(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).json() == []

    def test_returns_own_drafts(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        make_draft(repo_id=repo.id, user_id=_USER_ID)
        items = client.get(_url(repo.id), headers=auth_headers(user_id=_USER_ID)).json()
        assert len(items) == 1

    def test_excludes_other_users_drafts(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        make_draft(repo_id=repo.id, user_id=_OTHER_ID)
        items = client.get(_url(repo.id), headers=auth_headers(user_id=_USER_ID)).json()
        assert items == []

    def test_response_item_shape(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        make_draft(repo_id=repo.id, user_id=_USER_ID)
        item = client.get(_url(repo.id), headers=auth_headers(user_id=_USER_ID)).json()[0]
        for field in ("draft_id", "repo_id", "user_id", "label", "status",
                      "base_commit_hash", "commit_hash", "created_at", "updated_at"):
            assert field in item

    def test_excludes_deleted_drafts(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.deleted)
        items = client.get(_url(repo.id), headers=auth_headers(user_id=_USER_ID)).json()
        assert items == []

    def test_multiple_drafts_returned(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        make_draft(repo_id=repo.id, user_id=_USER_ID, label="Draft 1")
        make_draft(repo_id=repo.id, user_id=_USER_ID, label="Draft 2")
        items = client.get(_url(repo.id), headers=auth_headers(user_id=_USER_ID)).json()
        assert len(items) == 2

    def test_active_drafts_before_resolved(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        """editing/needs_rebase appear before approved/rejected in sort order."""
        repo = make_repo()
        make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.approved, label="Resolved")
        make_draft(repo_id=repo.id, user_id=_USER_ID, status=DraftStatus.editing, label="Active")
        items = client.get(_url(repo.id), headers=auth_headers(user_id=_USER_ID)).json()
        statuses = [i["status"] for i in items]
        # editing should come before approved
        assert statuses.index("editing") < statuses.index("approved")


class TestListDraftsRoles:
    def test_admin_can_list(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_author_can_list(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_reviewer_can_list(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_reader_can_list(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_non_member_cannot_list(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = None
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 403


class TestListDraftsAuth:
    def test_no_token_returns_401(self, client, make_repo):
        repo = make_repo()
        assert client.get(_url(repo.id)).status_code == 401

    def test_expired_token_returns_401(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers(expired=True)).status_code == 401
