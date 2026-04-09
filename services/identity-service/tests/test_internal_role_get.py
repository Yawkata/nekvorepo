"""
Tests for GET /v1/internal/repos/{repo_id}/role?user_id=...

Returns the role for a specific user/repo pair.
Callers (repo-service, workflow-service) cache the result for 60 seconds.
No passport authentication required (cluster-internal endpoint).

Coverage:
  Happy path — 200, correct role, all four roles returned correctly
  Not found  — unknown repo_id → 404, unknown user_id → 404
  Validation — missing user_id param → 422, invalid UUID → 422
"""

import uuid

_BASE = "/v1/internal/repos/{repo_id}/role"


class TestGetRoleSuccess:
    def test_returns_200(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.get(_BASE.format(repo_id=repo.id), params={"user_id": "alice-sub"})
        assert r.status_code == 200

    def test_response_contains_role(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.get(_BASE.format(repo_id=repo.id), params={"user_id": "alice-sub"})
        assert "role" in r.json()

    def test_admin_role_returned_for_owner(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.get(_BASE.format(repo_id=repo.id), params={"user_id": "alice-sub"})
        assert r.json()["role"] == "admin"

    def test_all_four_roles_returned_correctly(self, client, make_repo, make_membership):
        from shared.constants import RepoRole
        repo = make_repo(owner_id="owner-sub")
        make_membership(repo.id, "author-sub", RepoRole.author)
        make_membership(repo.id, "reviewer-sub", RepoRole.reviewer)
        make_membership(repo.id, "reader-sub", RepoRole.reader)

        for user_id, expected_role in [
            ("owner-sub",   "admin"),
            ("author-sub",  "author"),
            ("reviewer-sub","reviewer"),
            ("reader-sub",  "reader"),
        ]:
            r = client.get(_BASE.format(repo_id=repo.id), params={"user_id": user_id})
            assert r.json()["role"] == expected_role, f"Wrong role for {user_id}"

    def test_response_includes_repo_id_and_user_id(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.get(_BASE.format(repo_id=repo.id), params={"user_id": "alice-sub"})
        body = r.json()
        assert body["repo_id"] == str(repo.id)
        assert body["user_id"] == "alice-sub"

    def test_no_auth_required(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.get(_BASE.format(repo_id=repo.id), params={"user_id": "alice-sub"})
        assert r.status_code not in (401, 403)


class TestGetRoleNotFound:
    def test_unknown_user_returns_404(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.get(_BASE.format(repo_id=repo.id), params={"user_id": "stranger"})
        assert r.status_code == 404

    def test_unknown_repo_returns_404(self, client):
        r = client.get(_BASE.format(repo_id=uuid.uuid4()), params={"user_id": "anyone"})
        assert r.status_code == 404

    def test_404_detail_message(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.get(_BASE.format(repo_id=repo.id), params={"user_id": "nobody"})
        assert "not found" in r.json()["detail"].lower()


class TestGetRoleValidation:
    def test_missing_user_id_param_422(self, client, make_repo):
        repo = make_repo()
        r = client.get(_BASE.format(repo_id=repo.id))
        assert r.status_code == 422

    def test_invalid_repo_id_uuid_422(self, client):
        r = client.get("/v1/internal/repos/not-a-uuid/role", params={"user_id": "u"})
        assert r.status_code == 422
