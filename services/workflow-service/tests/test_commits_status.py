"""
Phase 8 — Commit status polling: GET /v1/repos/{repo_id}/commits/{commit_hash}/status

After opening a pending commit's diff view, a reviewer may stay on screen while
another reviewer resolves the commit.  The frontend polls this lightweight endpoint
every 30 seconds to detect a resolution and show the "already resolved" banner.

Coverage
--------
Status responses    — pending, approved, rejected (with comment), sibling_rejected, cancelled
Reviewer comment    — populated on rejection, null otherwise
Timestamp           — always present; ISO 8601 parseable
Role access         — all four roles (admin, reviewer, author, reader) may call
Non-member          — 403
Cross-repo isolation — commit exists but belongs to different repo → 404
Unknown commit      — 404
Auth                — no token → 401; expired → 401
"""

import uuid
from datetime import datetime

import pytest

from shared.constants import CommitStatus

_URL = "/v1/repos/{repo_id}/commits/{commit_hash}/status"


def _url(repo_id, commit_hash: str) -> str:
    return _URL.format(repo_id=repo_id, commit_hash=commit_hash)


# ---------------------------------------------------------------------------
# Status responses
# ---------------------------------------------------------------------------

class TestCommitStatusResponses:
    def test_pending_commit_returns_pending_status(
        self, client, auth_headers, mock_identity_client, mock_repo_client,
        make_repo, make_commit,
    ):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, status=CommitStatus.pending)

        resp = client.get(_url(repo.id, commit.commit_hash), headers=auth_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert data["commit_hash"] == commit.commit_hash
        assert data["status"] == "pending"
        assert data["reviewer_comment"] is None

    def test_approved_commit_returns_approved_status(
        self, client, auth_headers, mock_identity_client, mock_repo_client,
        make_repo, make_commit,
    ):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, status=CommitStatus.approved)

        resp = client.get(_url(repo.id, commit.commit_hash), headers=auth_headers())

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        assert resp.json()["reviewer_comment"] is None

    def test_rejected_commit_returns_status_and_comment(
        self, client, auth_headers, mock_identity_client, mock_repo_client,
        make_repo, make_commit, db_session,
    ):
        from shared.models.workflow import RepoCommit
        from sqlmodel import select

        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, status=CommitStatus.rejected)

        db_commit = db_session.exec(
            select(RepoCommit).where(RepoCommit.commit_hash == commit.commit_hash)
        ).one()
        db_commit.reviewer_comment = "Needs more context before merging."
        db_session.add(db_commit)
        db_session.commit()

        resp = client.get(_url(repo.id, commit.commit_hash), headers=auth_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert data["reviewer_comment"] == "Needs more context before merging."

    def test_sibling_rejected_commit_returns_correct_status(
        self, client, auth_headers, mock_identity_client, mock_repo_client,
        make_repo, make_commit,
    ):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, status=CommitStatus.sibling_rejected)

        resp = client.get(_url(repo.id, commit.commit_hash), headers=auth_headers())

        assert resp.status_code == 200
        assert resp.json()["status"] == "sibling_rejected"

    def test_cancelled_commit_returns_correct_status(
        self, client, auth_headers, mock_identity_client, mock_repo_client,
        make_repo, make_commit,
    ):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, status=CommitStatus.cancelled)

        resp = client.get(_url(repo.id, commit.commit_hash), headers=auth_headers())

        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_response_timestamp_is_present_and_parseable(
        self, client, auth_headers, mock_identity_client, mock_repo_client,
        make_repo, make_commit,
    ):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, status=CommitStatus.pending)

        resp = client.get(_url(repo.id, commit.commit_hash), headers=auth_headers())

        assert resp.status_code == 200
        ts = resp.json()["timestamp"]
        assert ts is not None
        # Must be ISO 8601 (FastAPI serialises timezone-aware datetimes with offset)
        datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Role access — any repo member may poll
# ---------------------------------------------------------------------------

class TestCommitStatusRoleAccess:
    @pytest.mark.parametrize("role", ["admin", "reviewer", "author", "reader"])
    def test_all_member_roles_can_poll_status(
        self, role,
        client, auth_headers, mock_identity_client, mock_repo_client,
        make_repo, make_commit,
    ):
        mock_identity_client.return_value = role
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, status=CommitStatus.pending)

        resp = client.get(_url(repo.id, commit.commit_hash), headers=auth_headers())

        assert resp.status_code == 200

    def test_non_member_returns_403(
        self, client, auth_headers, mock_identity_client, mock_repo_client,
        make_repo, make_commit,
    ):
        mock_identity_client.return_value = None  # not a member
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, status=CommitStatus.pending)

        resp = client.get(_url(repo.id, commit.commit_hash), headers=auth_headers())

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Resource guards
# ---------------------------------------------------------------------------

class TestCommitStatusResourceGuards:
    def test_unknown_commit_hash_returns_404(
        self, client, auth_headers, mock_identity_client, mock_repo_client,
        make_repo,
    ):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()

        resp = client.get(_url(repo.id, "a" * 64), headers=auth_headers())

        assert resp.status_code == 404

    def test_commit_from_different_repo_returns_404(
        self, client, auth_headers, mock_identity_client, mock_repo_client,
        make_repo, make_commit,
    ):
        """
        A commit that exists but belongs to repo_a must not be reachable through
        repo_b's URL — this enforces repository isolation at the API boundary.
        """
        mock_identity_client.return_value = "reviewer"
        repo_a = make_repo(repo_name="repo-a")
        repo_b = make_repo(repo_name="repo-b")
        commit_in_a = make_commit(repo_id=repo_a.id, status=CommitStatus.pending)

        resp = client.get(
            _url(repo_b.id, commit_in_a.commit_hash),
            headers=auth_headers(),
        )

        assert resp.status_code == 404

    def test_unknown_repo_returns_403(
        self, client, auth_headers, mock_identity_client, mock_repo_client,
        make_repo, make_commit,
    ):
        mock_identity_client.return_value = None  # non-member of unknown repo
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, status=CommitStatus.pending)

        resp = client.get(
            _url(uuid.uuid4(), commit.commit_hash),
            headers=auth_headers(),
        )

        # require_member fails first (403) before the commit lookup (404)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestCommitStatusAuth:
    def test_no_token_returns_401(
        self, client, make_repo, make_commit,
    ):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, status=CommitStatus.pending)

        resp = client.get(_url(repo.id, commit.commit_hash))

        assert resp.status_code == 401

    def test_expired_token_returns_401(
        self, client, auth_headers, make_repo, make_commit,
    ):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, status=CommitStatus.pending)

        resp = client.get(
            _url(repo.id, commit.commit_hash),
            headers=auth_headers(expired=True),
        )

        assert resp.status_code == 401

    def test_wrong_secret_returns_401(
        self, client, auth_headers, make_repo, make_commit,
    ):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, status=CommitStatus.pending)

        resp = client.get(
            _url(repo.id, commit.commit_hash),
            headers=auth_headers(wrong_secret=True),
        )

        assert resp.status_code == 401
