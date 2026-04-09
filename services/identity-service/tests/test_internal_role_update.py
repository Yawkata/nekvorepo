"""
Tests for PUT /v1/internal/repos/{repo_id}/members/{user_id}/role

Updates the role of an existing membership.
60-second TTL cache on callers provides bounded eventual consistency.
No passport authentication required (cluster-internal endpoint).

Coverage:
  Happy path — 200, role updated in response, DB row updated, all role transitions
  Not found  — unknown membership → 404
  Validation — invalid role → 422, invalid UUID → 422
"""

import uuid

from shared.models.identity import UserRepoLink
from sqlmodel import Session, select

_BASE = "/v1/internal/repos/{repo_id}/members/{user_id}/role"


class TestUpdateRoleSuccess:
    def test_returns_200(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.put(
            _BASE.format(repo_id=repo.id, user_id="alice-sub"),
            json={"role": "reviewer"},
        )
        assert r.status_code == 200

    def test_response_contains_new_role(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.put(
            _BASE.format(repo_id=repo.id, user_id="alice-sub"),
            json={"role": "author"},
        )
        assert r.json()["role"] == "author"

    def test_db_row_updated(self, client, make_repo, db_engine):
        repo = make_repo(owner_id="alice-sub")
        client.put(
            _BASE.format(repo_id=repo.id, user_id="alice-sub"),
            json={"role": "reviewer"},
        )
        with Session(db_engine) as s:
            link = s.exec(
                select(UserRepoLink).where(
                    UserRepoLink.repo_id == repo.id,
                    UserRepoLink.user_id == "alice-sub",
                )
            ).first()
        assert link.role.value == "reviewer"

    def test_all_role_transitions(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        for new_role in ("author", "reviewer", "reader", "admin"):
            r = client.put(
                _BASE.format(repo_id=repo.id, user_id="alice-sub"),
                json={"role": new_role},
            )
            assert r.status_code == 200
            assert r.json()["role"] == new_role

    def test_no_auth_required(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.put(
            _BASE.format(repo_id=repo.id, user_id="alice-sub"),
            json={"role": "reader"},
        )
        assert r.status_code not in (401, 403)


class TestUpdateRoleNotFound:
    def test_unknown_user_returns_404(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.put(
            _BASE.format(repo_id=repo.id, user_id="nobody"),
            json={"role": "reader"},
        )
        assert r.status_code == 404

    def test_unknown_repo_returns_404(self, client):
        r = client.put(
            _BASE.format(repo_id=uuid.uuid4(), user_id="alice-sub"),
            json={"role": "reader"},
        )
        assert r.status_code == 404


class TestUpdateRoleValidation:
    def test_invalid_role_value_422(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.put(
            _BASE.format(repo_id=repo.id, user_id="alice-sub"),
            json={"role": "superuser"},
        )
        assert r.status_code == 422

    def test_missing_role_field_422(self, client, make_repo):
        repo = make_repo(owner_id="alice-sub")
        r = client.put(_BASE.format(repo_id=repo.id, user_id="alice-sub"), json={})
        assert r.status_code == 422

    def test_invalid_repo_id_uuid_422(self, client):
        r = client.put(
            "/v1/internal/repos/not-a-uuid/members/user/role",
            json={"role": "reader"},
        )
        assert r.status_code == 422
