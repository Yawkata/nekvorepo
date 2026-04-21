"""
Tests for DELETE /v1/internal/repos/{repo_id}/members/{user_id}/commits
"""
import uuid

import pytest
from sqlmodel import select

from shared.constants import CommitStatus
from shared.models.workflow import RepoCommit


def _get_commit(db_session, commit_hash: str) -> RepoCommit | None:
    return db_session.exec(
        select(RepoCommit).where(RepoCommit.commit_hash == commit_hash)
    ).first()


_OWNER = "target-user-sub"
_OTHER = "other-user-sub"


def _url(repo_id, user_id=_OWNER):
    return f"/v1/internal/repos/{repo_id}/members/{user_id}/commits"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_cancels_pending_commits_204(client, make_repo, make_commit, db_session):
    repo = make_repo()
    c = make_commit(repo.id, owner_id=_OWNER, status=CommitStatus.pending)

    resp = client.delete(_url(repo.id))
    assert resp.status_code == 204

    db_session.expire_all()
    refreshed = _get_commit(db_session, c.commit_hash)
    assert refreshed.status == CommitStatus.cancelled


def test_only_pending_status_cancelled(client, make_repo, make_commit, db_session):
    repo = make_repo()
    pending = make_commit(repo.id, owner_id=_OWNER, status=CommitStatus.pending)
    approved = make_commit(repo.id, owner_id=_OWNER, status=CommitStatus.approved)
    rejected = make_commit(repo.id, owner_id=_OWNER, status=CommitStatus.rejected)
    sibling_rej = make_commit(repo.id, owner_id=_OWNER, status=CommitStatus.sibling_rejected)

    client.delete(_url(repo.id))

    db_session.expire_all()
    assert _get_commit(db_session, pending.commit_hash).status == CommitStatus.cancelled
    assert _get_commit(db_session, approved.commit_hash).status == CommitStatus.approved
    assert _get_commit(db_session, rejected.commit_hash).status == CommitStatus.rejected
    assert _get_commit(db_session, sibling_rej.commit_hash).status == CommitStatus.sibling_rejected


def test_no_commits_is_204_no_op(client, make_repo):
    repo = make_repo()
    resp = client.delete(_url(repo.id))
    assert resp.status_code == 204


def test_different_repo_unaffected(client, make_repo, make_commit, db_session):
    repo1 = make_repo(repo_name="repo-1")
    repo2 = make_repo(repo_name="repo-2")
    c2 = make_commit(repo2.id, owner_id=_OWNER, status=CommitStatus.pending)

    client.delete(_url(repo1.id))

    db_session.expire_all()
    assert _get_commit(db_session, c2.commit_hash).status == CommitStatus.pending


def test_different_user_unaffected(client, make_repo, make_commit, db_session):
    repo = make_repo()
    other_commit = make_commit(repo.id, owner_id=_OTHER, status=CommitStatus.pending)

    client.delete(_url(repo.id, user_id=_OWNER))

    db_session.expire_all()
    assert _get_commit(db_session, other_commit.commit_hash).status == CommitStatus.pending


# ---------------------------------------------------------------------------
# Auth / validation
# ---------------------------------------------------------------------------

def test_no_auth_required(client, make_repo):
    """Internal endpoint — should return 204 with no Authorization header."""
    repo = make_repo()
    resp = client.delete(_url(repo.id))
    assert resp.status_code == 204


def test_invalid_repo_uuid_422(client):
    resp = client.delete("/v1/internal/repos/not-a-uuid/members/some-user/commits")
    assert resp.status_code == 422
