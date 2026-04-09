"""
Tests for DELETE /v1/internal/repos/{repo_id}/members/{user_id}

Removes a UserRepoLink row.
Guard: the repository owner cannot be removed (prevents orphaned repos).
Returns 204 No Content on success.
No passport authentication required (cluster-internal endpoint).

Coverage:
  Happy path       — 204, row removed from DB, non-owner member deleted OK
  Owner guard      — cannot delete owner → 403
  Not found        — unknown membership → 404
  Idempotency      — deleting twice returns 404 on second call
  Validation       — invalid UUID → 422
"""

import uuid

from shared.models.identity import UserRepoLink
from sqlmodel import Session, select

_BASE = "/v1/internal/repos/{repo_id}/members/{user_id}"


class TestDeleteMembershipSuccess:
    def test_returns_204(self, client, make_repo, make_membership):
        from shared.constants import RepoRole
        repo = make_repo(owner_id="owner-sub")
        make_membership(repo.id, "member-sub", RepoRole.reader)
        r = client.delete(_BASE.format(repo_id=repo.id, user_id="member-sub"))
        assert r.status_code == 204

    def test_row_removed_from_db(self, client, make_repo, make_membership, db_engine):
        from shared.constants import RepoRole
        repo = make_repo(owner_id="owner-sub")
        make_membership(repo.id, "ex-member", RepoRole.reviewer)
        client.delete(_BASE.format(repo_id=repo.id, user_id="ex-member"))
        with Session(db_engine) as s:
            link = s.exec(
                select(UserRepoLink).where(
                    UserRepoLink.repo_id == repo.id,
                    UserRepoLink.user_id == "ex-member",
                )
            ).first()
        assert link is None

    def test_no_body_in_204_response(self, client, make_repo, make_membership):
        from shared.constants import RepoRole
        repo = make_repo(owner_id="owner-sub")
        make_membership(repo.id, "member-sub", RepoRole.author)
        r = client.delete(_BASE.format(repo_id=repo.id, user_id="member-sub"))
        assert r.content == b""

    def test_non_owner_can_be_deleted(self, client, make_repo, make_membership):
        """Any non-owner member role must be deletable."""
        from shared.constants import RepoRole
        repo = make_repo(owner_id="owner-sub")
        for role, uid in [
            (RepoRole.author,   "author-sub"),
            (RepoRole.reviewer, "reviewer-sub"),
            (RepoRole.reader,   "reader-sub"),
        ]:
            make_membership(repo.id, uid, role)
            r = client.delete(_BASE.format(repo_id=repo.id, user_id=uid))
            assert r.status_code == 204, f"Failed to delete {role.value} member"

    def test_no_auth_required(self, client, make_repo, make_membership):
        from shared.constants import RepoRole
        repo = make_repo(owner_id="owner-sub")
        make_membership(repo.id, "any-member", RepoRole.reader)
        r = client.delete(_BASE.format(repo_id=repo.id, user_id="any-member"))
        assert r.status_code not in (401, 403)


class TestDeleteOwnerGuard:
    def test_owner_deletion_returns_403(self, client, make_repo):
        repo = make_repo(owner_id="owner-sub")
        r = client.delete(_BASE.format(repo_id=repo.id, user_id="owner-sub"))
        assert r.status_code == 403

    def test_owner_guard_detail_message(self, client, make_repo):
        repo = make_repo(owner_id="owner-sub")
        r = client.delete(_BASE.format(repo_id=repo.id, user_id="owner-sub"))
        assert "owner" in r.json()["detail"].lower()

    def test_owner_row_still_exists_after_403(self, client, make_repo, db_engine):
        repo = make_repo(owner_id="owner-sub")
        client.delete(_BASE.format(repo_id=repo.id, user_id="owner-sub"))
        with Session(db_engine) as s:
            link = s.exec(
                select(UserRepoLink).where(
                    UserRepoLink.repo_id == repo.id,
                    UserRepoLink.user_id == "owner-sub",
                )
            ).first()
        assert link is not None


class TestDeleteMembershipNotFound:
    def test_unknown_user_returns_404(self, client, make_repo):
        repo = make_repo(owner_id="owner-sub")
        r = client.delete(_BASE.format(repo_id=repo.id, user_id="nobody"))
        assert r.status_code == 404

    def test_unknown_repo_returns_404(self, client):
        r = client.delete(_BASE.format(repo_id=uuid.uuid4(), user_id="anyone"))
        assert r.status_code == 404

    def test_second_delete_returns_404(self, client, make_repo, make_membership):
        """Deleting the same membership twice is not idempotent — 404 on second call."""
        from shared.constants import RepoRole
        repo = make_repo(owner_id="owner-sub")
        make_membership(repo.id, "one-time-member", RepoRole.reader)
        client.delete(_BASE.format(repo_id=repo.id, user_id="one-time-member"))
        r = client.delete(_BASE.format(repo_id=repo.id, user_id="one-time-member"))
        assert r.status_code == 404


class TestDeleteMembershipValidation:
    def test_invalid_repo_id_uuid_422(self, client):
        r = client.delete("/v1/internal/repos/not-a-uuid/members/user-sub")
        assert r.status_code == 422
