"""
Tests for POST /v1/repos — create a repository

The endpoint is a fully atomic saga inside identity-service:
  1. Validate repo_name (strip, lowercase, length, charset, reserved suffix)
  2. Check uniqueness for this owner
  3. INSERT repo_heads  (flush)
  4. INSERT user_repo_links  (admin role)
  5. COMMIT — both rows land or neither does

Coverage:
  Happy path   — 201, response shape, DB state, admin membership created
  Name rules   — valid variants, too short, too long, bad chars, consecutive
                 spaces, reserved .deleted suffix, strip/lowercase applied
  Description  — optional, max 200 chars, whitespace stripped, empty → None
  Auth         — no token 403, expired 401, wrong secret 401
  Uniqueness   — same name same owner → 409, same name different owner → OK
"""

import uuid

from shared.models.identity import UserRepoLink
from shared.models.workflow import RepoHead
from sqlmodel import Session, select

_URL = "/v1/repos"


class TestCreateRepoSuccess:
    def test_returns_201(self, client, auth_headers):
        r = client.post(_URL, json={"repo_name": "my-repo"}, headers=auth_headers())
        assert r.status_code == 201

    def test_response_has_repo_id(self, client, auth_headers):
        r = client.post(_URL, json={"repo_name": "my-repo"}, headers=auth_headers())
        assert "repo_id" in r.json()

    def test_response_repo_name_lowercased(self, client, auth_headers):
        r = client.post(_URL, json={"repo_name": "My-Repo"}, headers=auth_headers())
        assert r.json()["repo_name"] == "my-repo"

    def test_response_owner_id_matches_passport(self, client, auth_headers, make_passport):
        token = make_passport(user_id="owner-sub-999")
        r = client.post(_URL, json={"repo_name": "my-repo"},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.json()["owner_id"] == "owner-sub-999"

    def test_response_version_zero(self, client, auth_headers):
        r = client.post(_URL, json={"repo_name": "my-repo"}, headers=auth_headers())
        assert r.json()["version"] == 0

    def test_response_latest_commit_hash_null(self, client, auth_headers):
        r = client.post(_URL, json={"repo_name": "my-repo"}, headers=auth_headers())
        assert r.json()["latest_commit_hash"] is None

    def test_response_created_at_present(self, client, auth_headers):
        r = client.post(_URL, json={"repo_name": "my-repo"}, headers=auth_headers())
        assert r.json()["created_at"] is not None

    def test_repo_head_persisted_to_db(self, client, auth_headers, db_engine):
        r = client.post(_URL, json={"repo_name": "my-repo"}, headers=auth_headers())
        repo_id = uuid.UUID(r.json()["repo_id"])
        with Session(db_engine) as s:
            repo = s.get(RepoHead, repo_id)
        assert repo is not None
        assert repo.repo_name == "my-repo"

    def test_admin_membership_created(self, client, auth_headers, db_engine, make_passport):
        from shared.constants import RepoRole
        token = make_passport(user_id="creator-sub")
        r = client.post(_URL, json={"repo_name": "my-repo"},
                        headers={"Authorization": f"Bearer {token}"})
        repo_id = uuid.UUID(r.json()["repo_id"])
        with Session(db_engine) as s:
            link = s.exec(
                select(UserRepoLink).where(
                    UserRepoLink.repo_id == repo_id,
                    UserRepoLink.user_id == "creator-sub",
                )
            ).first()
        assert link is not None
        assert link.role == RepoRole.admin

    def test_with_description(self, client, auth_headers):
        r = client.post(_URL, json={"repo_name": "my-repo", "description": "A great repo"},
                        headers=auth_headers())
        assert r.json()["description"] == "A great repo"

    def test_description_whitespace_stripped(self, client, auth_headers):
        r = client.post(_URL, json={"repo_name": "my-repo", "description": "  stripped  "},
                        headers=auth_headers())
        assert r.json()["description"] == "stripped"

    def test_description_empty_string_becomes_null(self, client, auth_headers):
        r = client.post(_URL, json={"repo_name": "my-repo", "description": ""},
                        headers=auth_headers())
        assert r.json()["description"] is None

    def test_description_whitespace_only_becomes_null(self, client, auth_headers):
        r = client.post(_URL, json={"repo_name": "my-repo", "description": "   "},
                        headers=auth_headers())
        assert r.json()["description"] is None

    def test_no_description_is_null(self, client, auth_headers):
        r = client.post(_URL, json={"repo_name": "my-repo"}, headers=auth_headers())
        assert r.json()["description"] is None


class TestCreateRepoNameValidation:
    def _post(self, client, auth_headers, name, desc=None):
        body = {"repo_name": name}
        if desc is not None:
            body["description"] = desc
        return client.post(_URL, json=body, headers=auth_headers())

    # Valid names
    def test_minimum_3_chars(self, client, auth_headers):
        assert self._post(client, auth_headers, "abc").status_code == 201

    def test_exactly_50_chars(self, client, auth_headers):
        assert self._post(client, auth_headers, "a" * 50).status_code == 201

    def test_name_with_hyphen(self, client, auth_headers):
        assert self._post(client, auth_headers, "my-repo").status_code == 201

    def test_name_with_space(self, client, auth_headers):
        r = self._post(client, auth_headers, "my repo")
        assert r.status_code == 201
        assert r.json()["repo_name"] == "my repo"

    def test_name_starts_with_digit(self, client, auth_headers):
        assert self._post(client, auth_headers, "123-repo").status_code == 201

    def test_name_leading_trailing_spaces_stripped(self, client, auth_headers):
        r = self._post(client, auth_headers, "  my-repo  ")
        assert r.status_code == 201
        assert r.json()["repo_name"] == "my-repo"

    def test_uppercase_lowercased(self, client, auth_headers):
        r = self._post(client, auth_headers, "MyREPO")
        assert r.status_code == 201
        assert r.json()["repo_name"] == "myrepo"

    # Invalid names
    def test_too_short_2_chars_422(self, client, auth_headers):
        assert self._post(client, auth_headers, "ab").status_code == 422

    def test_too_long_51_chars_422(self, client, auth_headers):
        assert self._post(client, auth_headers, "a" * 51).status_code == 422

    def test_consecutive_spaces_422(self, client, auth_headers):
        assert self._post(client, auth_headers, "my  repo").status_code == 422

    def test_underscore_not_allowed_422(self, client, auth_headers):
        assert self._post(client, auth_headers, "my_repo").status_code == 422

    def test_at_sign_not_allowed_422(self, client, auth_headers):
        assert self._post(client, auth_headers, "my@repo").status_code == 422

    def test_starts_with_hyphen_422(self, client, auth_headers):
        assert self._post(client, auth_headers, "-my-repo").status_code == 422

    def test_reserved_suffix_deleted_422(self, client, auth_headers):
        assert self._post(client, auth_headers, "repo.deleted").status_code == 422

    def test_description_too_long_422(self, client, auth_headers):
        assert self._post(client, auth_headers, "my-repo", desc="x" * 201).status_code == 422

    def test_missing_repo_name_422(self, client, auth_headers):
        r = client.post(_URL, json={}, headers=auth_headers())
        assert r.status_code == 422


class TestCreateRepoAuth:
    def test_no_token_returns_401(self, client):
        r = client.post(_URL, json={"repo_name": "my-repo"})
        assert r.status_code == 401

    def test_expired_token_returns_401(self, client, make_passport):
        token = make_passport(expired=True)
        r = client.post(_URL, json={"repo_name": "my-repo"},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_wrong_secret_returns_401(self, client, make_passport):
        token = make_passport(wrong_secret=True)
        r = client.post(_URL, json={"repo_name": "my-repo"},
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


class TestCreateRepoUniqueness:
    def test_duplicate_name_same_owner_returns_409(self, client, auth_headers):
        client.post(_URL, json={"repo_name": "my-repo"}, headers=auth_headers())
        r = client.post(_URL, json={"repo_name": "my-repo"}, headers=auth_headers())
        assert r.status_code == 409

    def test_same_name_different_owner_is_allowed(self, client, make_passport):
        token_a = make_passport(user_id="owner-a")
        token_b = make_passport(user_id="owner-b")
        r1 = client.post(_URL, json={"repo_name": "shared-name"},
                         headers={"Authorization": f"Bearer {token_a}"})
        r2 = client.post(_URL, json={"repo_name": "shared-name"},
                         headers={"Authorization": f"Bearer {token_b}"})
        assert r1.status_code == 201
        assert r2.status_code == 201

    def test_duplicate_detail_message(self, client, auth_headers):
        client.post(_URL, json={"repo_name": "my-repo"}, headers=auth_headers())
        r = client.post(_URL, json={"repo_name": "my-repo"}, headers=auth_headers())
        assert "already own" in r.json()["detail"].lower()
