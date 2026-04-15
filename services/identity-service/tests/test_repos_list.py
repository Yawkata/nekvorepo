"""
Tests for GET /v1/repos — list repositories the caller is a member of

Returns every repo where a UserRepoLink exists for the caller's user_id,
sorted by created_at descending. Includes the caller's role in each item.

Coverage:
  Happy path   — empty list, single repo, multiple repos, role field present
  Isolation    — other users' repos not returned
  Sorting      — newest first
  Auth         — no token 403, expired 401
"""

_URL = "/v1/repos"


class TestListReposSuccess:
    def test_empty_list_when_no_repos(self, client, auth_headers):
        r = client.get(_URL, headers=auth_headers())
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_one_repo(self, client, auth_headers, make_repo):
        make_repo()
        r = client.get(_URL, headers=auth_headers())
        assert len(r.json()) == 1

    def test_repo_item_has_required_fields(self, client, auth_headers, make_repo):
        make_repo()
        item = client.get(_URL, headers=auth_headers()).json()[0]
        for field in ("repo_id", "repo_name", "owner_id", "role", "version", "created_at"):
            assert field in item, f"Missing field: {field}"

    def test_role_is_admin_for_creator(self, client, auth_headers, make_repo):
        make_repo()
        item = client.get(_URL, headers=auth_headers()).json()[0]
        assert item["role"] == "admin"

    def test_role_reflects_actual_membership(self, client, make_passport, make_repo, make_membership):
        """A reviewer sees their reviewer role in the list."""
        from shared.constants import RepoRole
        repo = make_repo(owner_id="owner-sub")
        make_membership(repo.id, "reviewer-sub", RepoRole.reviewer)
        token = make_passport(user_id="reviewer-sub")
        items = client.get(_URL, headers={"Authorization": f"Bearer {token}"}).json()
        assert len(items) == 1
        assert items[0]["role"] == "reviewer"

    def test_multiple_repos_returned(self, client, auth_headers, make_repo):
        make_repo(repo_name="repo-one")
        make_repo(repo_name="repo-two")
        make_repo(repo_name="repo-three")
        r = client.get(_URL, headers=auth_headers())
        assert len(r.json()) == 3

    def test_other_users_repos_not_included(self, client, auth_headers, make_repo):
        make_repo(owner_id="other-user-sub", repo_name="their-repo")
        r = client.get(_URL, headers=auth_headers())
        assert r.json() == []

    def test_only_own_repos_returned_when_mixed(self, client, make_passport, make_repo):
        from shared.constants import RepoRole
        token_a = make_passport(user_id="user-a")
        token_b = make_passport(user_id="user-b")
        make_repo(owner_id="user-a", repo_name="a-repo")
        make_repo(owner_id="user-b", repo_name="b-repo")
        resp_a = client.get(_URL, headers={"Authorization": f"Bearer {token_a}"}).json()
        resp_b = client.get(_URL, headers={"Authorization": f"Bearer {token_b}"}).json()
        assert len(resp_a) == 1 and resp_a[0]["repo_name"] == "a-repo"
        assert len(resp_b) == 1 and resp_b[0]["repo_name"] == "b-repo"

    def test_repos_sorted_newest_first(self, client, auth_headers, make_repo, db_session):
        from datetime import datetime, timedelta, timezone
        from shared.models.workflow import RepoHead
        repo_first = make_repo(repo_name="first-repo")
        make_repo(repo_name="second-repo")
        # Pin a deterministic past timestamp — no sleep, no flakiness
        db_session.expire_all()
        first = db_session.get(RepoHead, repo_first.id)
        first.created_at = datetime.now(timezone.utc) - timedelta(seconds=2)
        db_session.add(first)
        db_session.commit()
        items = client.get(_URL, headers=auth_headers()).json()
        assert items[0]["repo_name"] == "second-repo"
        assert items[1]["repo_name"] == "first-repo"


class TestListReposAuth:
    def test_no_token_returns_401(self, client):
        assert client.get(_URL).status_code == 401

    def test_expired_token_returns_401(self, client, make_passport):
        token = make_passport(expired=True)
        assert client.get(_URL, headers={"Authorization": f"Bearer {token}"}).status_code == 401
