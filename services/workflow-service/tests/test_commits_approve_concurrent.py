"""
Concurrency and optimistic-lock tests for the approve endpoint.

The approval transaction has two guards that protect against race conditions:

Guard A — stale_at_approval (Step 2)
  commit.parent_commit_hash != repo_head.latest_commit_hash
  Raised when commit B was already approved and advanced the head before this
  request's commit A is approved.  The author must rebase.

Guard B — concurrent_reviewer (Step 5)
  The optimistic-lock UPDATE returns 0 rows because another request already
  bumped repo_heads.version between this request's read and write.
  Two reviewers clicked "Approve" at the exact same millisecond; one wins,
  the other gets 409 concurrent_reviewer.

Coverage:
  stale_at_approval detail string  — 409 response detail contains the token
  stale_at_approval DB rollback    — stuck commit remains pending after 409
  stale_at_approval after sibling  — approve B after A was approved → 409
  concurrent_reviewer simulation   — two threads race to approve the same commit;
                                     exactly one 200 and one 409
  Self-approval                    — admin cannot approve own commit (reviewer
                                     self-approval is tested in test_commits_approve.py);
                                     DB state unchanged after blocked attempt

Note: basic role enforcement (reviewer/author/reader/non-member) and the
single-reviewer self-approval case are covered by test_commits_approve.py.
This file focuses on the concurrency-specific and optimistic-lock paths.
"""

import threading
import uuid

import pytest
from sqlmodel import select

from shared.constants import CommitStatus, DraftStatus
from shared.models.workflow import RepoCommit

_URL = "/v1/repos/{repo_id}/commits/{commit_hash}/approve"
_AUTHOR_ID   = "author-user-sub"
_REVIEWER_ID = "reviewer-user-sub"
_TEST_USER_ID = "test-user-sub"  # matches conftest._TEST_USER_ID


def _url(repo_id, commit_hash):
    return _URL.format(repo_id=repo_id, commit_hash=commit_hash)


# ---------------------------------------------------------------------------
# Guard A — stale_at_approval
# ---------------------------------------------------------------------------

class TestStaleAtApproval:
    def test_stale_detail_contains_stale_at_approval_token(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_commit
    ):
        """
        The 409 response detail must include the string 'stale_at_approval' so
        that clients can distinguish this error type from other 409s.
        """
        mock_identity_client.return_value = "reviewer"
        repo = make_repo(latest_commit_hash="f" * 64)  # head is already advanced
        # Commit whose parent is None — does not match "f"*64
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, parent_commit_hash=None)

        r = client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))

        assert r.status_code == 409
        assert "stale_at_approval" in r.json()["detail"]

    def test_stale_commit_not_approved_in_db(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_commit, db_session
    ):
        """A stale commit must remain pending in the DB — the transaction must be rolled back."""
        mock_identity_client.return_value = "reviewer"
        repo = make_repo(latest_commit_hash="e" * 64)
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, parent_commit_hash=None)

        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()

        refreshed = db_session.exec(
            select(RepoCommit).where(RepoCommit.commit_hash == commit.commit_hash)
        ).first()
        assert refreshed.status == CommitStatus.pending  # unchanged

    def test_approve_after_sibling_approved_returns_stale(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_commit
    ):
        """
        Scenario: Two reviewers work in sequence.
          1. Reviewer 1 approves commit A  (succeeds — repo head advances)
          2. Reviewer 2 tries to approve commit B (same parent)
             → commit B is now sibling_rejected, so the "not pending" guard fires first.

        This test verifies the request is rejected (via any 409).
        In practice the commit.status == sibling_rejected guard fires before
        the parent-hash check, but both are correct 409s.
        """
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit_a = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, commit_summary="A")
        commit_b = make_commit(repo_id=repo.id, owner_id="author-b", commit_summary="B")

        # Step 1: approve A
        r1 = client.post(_url(repo.id, commit_a.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        assert r1.status_code == 200

        # Step 2: try to approve B — it is now sibling_rejected → 409
        r2 = client.post(_url(repo.id, commit_b.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Guard B — concurrent_reviewer (optimistic lock)
# ---------------------------------------------------------------------------

class TestConcurrentReviewer:
    def test_concurrent_approval_one_wins_one_gets_409(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_commit
    ):
        """
        Two reviewer threads attempt to approve the same commit simultaneously.
        The optimistic-lock guard (step 5) ensures exactly one succeeds (200)
        and the other gets a 409.

        The 409 may carry either:
          - 'concurrent_reviewer' — if both requests passed the staleness check
            before either committed (true race on the optimistic lock)
          - 'not pending' — if the second request ran after the first committed
            and the commit.status was already 'approved'

        Both outcomes are correct.  We assert that exactly one request succeeds
        and exactly one fails with a client error.
        """
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)

        results: list[int] = []
        lock = threading.Lock()

        def _approve():
            r = client.post(
                _url(repo.id, commit.commit_hash),
                headers=auth_headers(user_id=_REVIEWER_ID),
            )
            with lock:
                results.append(r.status_code)

        t1 = threading.Thread(target=_approve)
        t2 = threading.Thread(target=_approve)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results.count(200) == 1, f"Expected exactly 1 success, got: {results}"
        assert results.count(409) == 1, f"Expected exactly 1 conflict, got: {results}"


# ---------------------------------------------------------------------------
# Self-approval guard (403)
# ---------------------------------------------------------------------------

class TestSelfApprovalGuard:
    def test_admin_cannot_approve_own_commit(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_commit
    ):
        """Self-approval is blocked regardless of role — even admin."""
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        # Use _TEST_USER_ID as both the commit owner and the approver (default auth_headers user)
        commit = make_commit(repo_id=repo.id, owner_id=_TEST_USER_ID)

        r = client.post(_url(repo.id, commit.commit_hash), headers=auth_headers())
        assert r.status_code == 403

    def test_self_approval_blocked_commit_remains_pending(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_commit, db_session
    ):
        """A blocked self-approval must not change the commit's status in the DB."""
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_REVIEWER_ID)

        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()

        refreshed = db_session.exec(
            select(RepoCommit).where(RepoCommit.commit_hash == commit.commit_hash)
        ).first()
        assert refreshed.status == CommitStatus.pending
