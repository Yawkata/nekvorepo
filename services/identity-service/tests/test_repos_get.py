"""
Tests for GET /v1/repos/{repo_id} — fetch a single repository

Access control: any repo member may call this endpoint.
Non-members receive 403 (membership checked before repo existence).

Coverage:
  Happy path   — 200, full response, role matches membership
  Access control — non-member 403, non-existent repo 404
  Auth         — no token 403, expired 401
  Input        — invalid UUID 422
"""

import uuid

_URL = "/v1/repos/{repo_id}"


class TestGetRepoSuccess:
    def test_returns_200_for_member(self, client, auth_headers, make_repo):
        repo = make_repo()
        r = client.get(_URL.format(repo_id=repo.id), headers=auth_headers())
        assert r.status_code == 200

    def test_response_has_correct_repo_id(self, client, auth_headers, make_repo):
        repo = make_repo()
        r = client.get(_URL.format(repo_id=repo.id), headers=auth_headers())
        assert r.json()["repo_id"] == str(repo.id)

    def test_response_has_correct_repo_name(self, client, auth_headers, make_repo):
        repo = make_repo(repo_name="my-special-repo")
        r = client.get(_URL.format(repo_id=repo.id), headers=auth_headers())
        assert r.json()["repo_name"] == "my-special-repo"

    def test_response_role_is_admin_for_owner(self, client, auth_headers, make_repo):
        repo = make_repo()
        r = client.get(_URL.format(repo_id=repo.id), headers=auth_headers())
        assert r.json()["role"] == "admin"

    def test_response_role_reflects_actual_role(self, client, make_passport, make_repo, make_membership):
        from shared.constants import RepoRole
        repo = make_repo(owner_id="owner-sub")
        make_membership(repo.id, "reader-sub", RepoRole.reader)
        token = make_passport(user_id="reader-sub")
        r = client.get(_URL.format(repo_id=repo.id),
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["role"] == "reader"

    def test_all_response_fields_present(self, client, auth_headers, make_repo):
        repo = make_repo()
        item = client.get(_URL.format(repo_id=repo.id), headers=auth_headers()).json()
        for field in ("repo_id", "repo_name", "owner_id", "role", "version",
                      "created_at", "latest_commit_hash"):
            assert field in item


class TestGetRepoAccessControl:
    def test_non_member_returns_403(self, client, make_passport, make_repo):
        repo = make_repo(owner_id="owner-sub")
        token = make_passport(user_id="stranger-sub")
        r = client.get(_URL.format(repo_id=repo.id),
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    def test_nonexistent_repo_with_no_membership_returns_403(self, client, auth_headers):
        """get_repo checks membership first; unknown repo_id → no link → 403."""
        r = client.get(_URL.format(repo_id=uuid.uuid4()), headers=auth_headers())
        assert r.status_code == 403

    def test_invalid_uuid_returns_422(self, client, auth_headers):
        r = client.get("/v1/repos/not-a-uuid", headers=auth_headers())
        assert r.status_code == 422


class TestGetRepoAuth:
    def test_no_token_returns_401(self, client, make_repo):
        repo = make_repo()
        assert client.get(_URL.format(repo_id=repo.id)).status_code == 401

    def test_expired_token_returns_401(self, client, make_passport, make_repo):
        repo = make_repo()
        token = make_passport(expired=True)
        r = client.get(_URL.format(repo_id=repo.id),
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
