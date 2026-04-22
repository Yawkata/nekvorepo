"""
Tests for DELETE /v1/repos/{repo_id} — phase-10 admin-only cascade.

Covers:
  - Admin authorization (non-admin rejected with 403)
  - 404 for unknown repo
  - Cascade removes repo_heads + user_repo_links + invite_tokens
  - Downstream service failures (repo-service / workflow-service) do NOT
    abort the primary delete — the cascade is best-effort on those legs.
  - Cache-invalidation SNS publish called once per member snapshot.
"""
import uuid
from unittest.mock import patch

from sqlmodel import select

from shared.constants import RepoRole
from shared.models.identity import UserRepoLink
from shared.models.workflow import RepoHead
from shared.models.invite import InviteToken


_URL = "/v1/repos/{repo_id}"


def _patch_downstream(*, drafts_side=None, commits_side=None):
    """Patch the two best-effort downstream HTTP calls in one context."""
    return (
        patch("app.services.repo_client.delete_repo_drafts", side_effect=drafts_side)
        if drafts_side is not None
        else patch("app.services.repo_client.delete_repo_drafts", return_value=None)
    ), (
        patch("app.services.workflow_client.delete_repo_commits", side_effect=commits_side)
        if commits_side is not None
        else patch("app.services.workflow_client.delete_repo_commits", return_value=None)
    )


class TestDeleteRepoSuccess:
    def test_returns_204(self, client, db_session, make_repo, make_invite_token, auth_headers):
        repo = make_repo()
        make_invite_token(repo_id=repo.id)
        p_repo, p_wf = _patch_downstream()
        with p_repo, p_wf, patch("app.services.events.publish_cache_invalidation") as _p:
            r = client.delete(_URL.format(repo_id=repo.id), headers=auth_headers())
        assert r.status_code == 204

    def test_repo_head_row_gone(self, client, db_session, make_repo, auth_headers):
        repo = make_repo()
        rid = repo.id
        p_repo, p_wf = _patch_downstream()
        with p_repo, p_wf, patch("app.services.events.publish_cache_invalidation"):
            client.delete(_URL.format(repo_id=rid), headers=auth_headers())
        db_session.expire_all()
        still_there = db_session.exec(
            select(RepoHead).where(RepoHead.id == rid)
        ).first()
        assert still_there is None

    def test_memberships_gone(self, client, db_session, make_repo, make_membership, auth_headers):
        repo = make_repo()
        rid = repo.id
        make_membership(repo_id=rid, user_id="other-user", role=RepoRole.reviewer)
        p_repo, p_wf = _patch_downstream()
        with p_repo, p_wf, patch("app.services.events.publish_cache_invalidation"):
            client.delete(_URL.format(repo_id=rid), headers=auth_headers())
        db_session.expire_all()
        remaining = db_session.exec(
            select(UserRepoLink).where(UserRepoLink.repo_id == rid)
        ).all()
        assert remaining == []

    def test_invite_tokens_gone(self, client, db_session, make_repo, make_invite_token, auth_headers):
        repo = make_repo()
        rid = repo.id
        make_invite_token(repo_id=rid)
        p_repo, p_wf = _patch_downstream()
        with p_repo, p_wf, patch("app.services.events.publish_cache_invalidation"):
            client.delete(_URL.format(repo_id=rid), headers=auth_headers())
        db_session.expire_all()
        remaining = db_session.exec(
            select(InviteToken).where(InviteToken.repo_id == rid)
        ).all()
        assert remaining == []

    def test_downstream_called(self, client, make_repo, auth_headers):
        repo = make_repo()
        with patch("app.services.repo_client.delete_repo_drafts") as m_repo, \
             patch("app.services.workflow_client.delete_repo_commits") as m_wf, \
             patch("app.services.events.publish_cache_invalidation"):
            client.delete(_URL.format(repo_id=repo.id), headers=auth_headers())
        m_repo.assert_called_once_with(repo.id)
        m_wf.assert_called_once_with(repo.id)

    def test_cache_invalidation_per_member(
        self, client, db_session, make_repo, make_membership, auth_headers,
    ):
        repo = make_repo()
        make_membership(repo_id=repo.id, user_id="member-2", role=RepoRole.reader)
        make_membership(repo_id=repo.id, user_id="member-3", role=RepoRole.reader)
        p_repo, p_wf = _patch_downstream()
        with p_repo, p_wf, patch("app.services.events.publish_cache_invalidation") as pub:
            client.delete(_URL.format(repo_id=repo.id), headers=auth_headers())
        # Owner + 2 extra members = 3 publishes.
        assert pub.call_count == 3


class TestDeleteRepoAuthorization:
    def test_non_admin_forbidden(self, client, db_session, make_repo, make_membership, auth_headers):
        # Repo owned by someone else; caller is only a reviewer.
        repo = make_repo(owner_id="owner-99")
        make_membership(repo_id=repo.id, user_id="test-user-sub", role=RepoRole.reviewer)
        r = client.delete(_URL.format(repo_id=repo.id), headers=auth_headers())
        assert r.status_code == 403

    def test_non_member_forbidden(self, client, make_repo, auth_headers):
        # Caller has no membership row at all.
        repo = make_repo(owner_id="someone-else")
        r = client.delete(_URL.format(repo_id=repo.id), headers=auth_headers())
        assert r.status_code == 403

    def test_nonexistent_repo_404(self, client, auth_headers):
        r = client.delete(_URL.format(repo_id=uuid.uuid4()), headers=auth_headers())
        assert r.status_code == 404


class TestDeleteRepoDownstreamFailure:
    """Downstream (repo-service / workflow-service) failures must NOT block
    the primary repo_heads delete — the cascade is designed to be retry-friendly."""

    def test_repo_drafts_failure_still_deletes(
        self, client, db_session, make_repo, auth_headers,
    ):
        repo = make_repo()
        rid = repo.id

        def _boom(*_a, **_kw):
            raise RuntimeError("simulated 502 from repo-service")

        with patch("app.services.repo_client.delete_repo_drafts", side_effect=_boom), \
             patch("app.services.workflow_client.delete_repo_commits", return_value=None), \
             patch("app.services.events.publish_cache_invalidation"):
            r = client.delete(_URL.format(repo_id=rid), headers=auth_headers())

        assert r.status_code == 204
        db_session.expire_all()
        still_there = db_session.exec(
            select(RepoHead).where(RepoHead.id == rid)
        ).first()
        assert still_there is None

    def test_workflow_commits_failure_still_deletes(
        self, client, db_session, make_repo, auth_headers,
    ):
        repo = make_repo()
        rid = repo.id

        def _boom(*_a, **_kw):
            raise RuntimeError("simulated 502 from workflow-service")

        with patch("app.services.repo_client.delete_repo_drafts", return_value=None), \
             patch("app.services.workflow_client.delete_repo_commits", side_effect=_boom), \
             patch("app.services.events.publish_cache_invalidation"):
            r = client.delete(_URL.format(repo_id=rid), headers=auth_headers())

        assert r.status_code == 204
        db_session.expire_all()
        still_there = db_session.exec(
            select(RepoHead).where(RepoHead.id == rid)
        ).first()
        assert still_there is None
