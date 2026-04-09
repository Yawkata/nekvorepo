"""
Tests for POST /v1/internal/memberships — create a repo membership

Called by repo-service / invite flow after a user accepts an invite.
No passport authentication required (cluster-internal endpoint).

Coverage:
  Happy path   — 201, response shape, DB row created, all four roles accepted
  Duplicate    — same user+repo → 409
  Validation   — missing fields → 422, invalid role → 422

Note: the live DB schema has user_repo_links.repo_id → repo_heads.id FK (from
migrations, not reflected in the SQLModel class). All tests that hit the DB
must create a real repo_heads row first via the make_repo fixture.
"""

import uuid

from shared.models.identity import UserRepoLink
from sqlmodel import Session, select

_URL = "/v1/internal/memberships"


class TestCreateMembershipSuccess:
    def test_returns_201(self, client, make_repo):
        repo = make_repo()
        r = client.post(_URL, json={"repo_id": str(repo.id), "user_id": "new-user-sub", "role": "reader"})
        assert r.status_code == 201

    def test_response_has_id(self, client, make_repo):
        repo = make_repo()
        r = client.post(_URL, json={"repo_id": str(repo.id), "user_id": "new-user-sub", "role": "reader"})
        assert "id" in r.json()

    def test_response_repo_id_matches(self, client, make_repo):
        repo = make_repo()
        r = client.post(_URL, json={"repo_id": str(repo.id), "user_id": "new-user-sub", "role": "reader"})
        assert r.json()["repo_id"] == str(repo.id)

    def test_response_user_id_matches(self, client, make_repo):
        repo = make_repo()
        r = client.post(_URL, json={"repo_id": str(repo.id), "user_id": "specific-user", "role": "reader"})
        assert r.json()["user_id"] == "specific-user"

    def test_response_role_matches(self, client, make_repo):
        repo = make_repo()
        r = client.post(_URL, json={"repo_id": str(repo.id), "user_id": "new-user-sub", "role": "author"})
        assert r.json()["role"] == "author"

    def test_row_persisted_to_db(self, client, make_repo, db_engine):
        repo = make_repo()
        client.post(_URL, json={"repo_id": str(repo.id), "user_id": "persisted-user", "role": "reviewer"})
        with Session(db_engine) as s:
            link = s.exec(
                select(UserRepoLink).where(
                    UserRepoLink.repo_id == repo.id,
                    UserRepoLink.user_id == "persisted-user",
                )
            ).first()
        assert link is not None
        assert link.role.value == "reviewer"

    def test_all_four_roles_accepted(self, client, make_repo):
        repo = make_repo()
        for i, role in enumerate(("admin", "author", "reviewer", "reader")):
            r = client.post(_URL, json={"repo_id": str(repo.id), "user_id": f"user-{i}", "role": role})
            assert r.status_code == 201, f"Role {role} was rejected"

    def test_no_auth_required(self, client, make_repo):
        """Internal endpoint — no passport check."""
        repo = make_repo()
        r = client.post(_URL, json={"repo_id": str(repo.id), "user_id": "any-user", "role": "reader"})
        assert r.status_code not in (401, 403)


class TestCreateMembershipDuplicate:
    def test_duplicate_returns_409(self, client, make_repo):
        repo = make_repo()
        body = {"repo_id": str(repo.id), "user_id": "new-user-sub", "role": "reader"}
        client.post(_URL, json=body)
        r = client.post(_URL, json=body)
        assert r.status_code == 409

    def test_duplicate_detail_message(self, client, make_repo):
        repo = make_repo()
        body = {"repo_id": str(repo.id), "user_id": "new-user-sub", "role": "reader"}
        client.post(_URL, json=body)
        r = client.post(_URL, json=body)
        assert "already a member" in r.json()["detail"].lower()

    def test_different_user_same_repo_is_ok(self, client, make_repo):
        repo = make_repo()
        r1 = client.post(_URL, json={"repo_id": str(repo.id), "user_id": "user-one", "role": "reader"})
        r2 = client.post(_URL, json={"repo_id": str(repo.id), "user_id": "user-two", "role": "reader"})
        assert r1.status_code == 201
        assert r2.status_code == 201


class TestCreateMembershipValidation:
    def test_missing_repo_id_422(self, client):
        r = client.post(_URL, json={"user_id": "u", "role": "reader"})
        assert r.status_code == 422

    def test_missing_user_id_422(self, client):
        r = client.post(_URL, json={"repo_id": str(uuid.uuid4()), "role": "reader"})
        assert r.status_code == 422

    def test_missing_role_422(self, client):
        r = client.post(_URL, json={"repo_id": str(uuid.uuid4()), "user_id": "u"})
        assert r.status_code == 422

    def test_invalid_role_422(self, client):
        r = client.post(_URL, json={"repo_id": str(uuid.uuid4()), "user_id": "u", "role": "superuser"})
        assert r.status_code == 422

    def test_invalid_uuid_repo_id_422(self, client):
        r = client.post(_URL, json={"repo_id": "not-a-uuid", "user_id": "u", "role": "reader"})
        assert r.status_code == 422
