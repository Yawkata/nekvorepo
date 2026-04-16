"""
Tests for GET /v1/repos/{repo_id}/head — repo HEAD polling endpoint.

This endpoint lets the frontend poll for HEAD changes on a 30-second interval
(with ±5-second jitter and Page Visibility API pause) so it can detect when
another author's commit has been approved and the draft needs rebase.

Contract:
  - Returns the current latest_commit_hash and commit timestamp.
  - Accessible to all repo members (any role).
  - Returns null hashes/timestamps when the repo has no commits yet.

Coverage:
  Happy path       — 200, response shape, null hash for fresh repo, populated hash
  Timestamp        — commit_timestamp null when no commits, matches commit record
  Head advances    — after seeding a new commit hash, endpoint returns it
  Role access      — admin/author/reviewer/reader → 200; non-member → 403
  Repository guard — unknown repo_id → 404
  Auth             — no token → 401, expired → 401
"""

import hashlib
import uuid

import pytest
from sqlmodel import select

from shared.models.workflow import RepoCommit, RepoHead

_URL = "/v1/repos/{repo_id}/head"
_USER_ID = "test-user"


def _url(repo_id):
    return _URL.format(repo_id=repo_id)


# ---------------------------------------------------------------------------
# Happy path — response shape and correctness
# ---------------------------------------------------------------------------

class TestHeadPollingSuccess:
    def test_returns_200(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        r = client.get(_url(repo.id), headers=auth_headers())
        assert r.status_code == 200

    def test_response_has_repo_id(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        data = client.get(_url(repo.id), headers=auth_headers()).json()
        assert "repo_id" in data

    def test_response_has_latest_commit_hash(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        data = client.get(_url(repo.id), headers=auth_headers()).json()
        assert "latest_commit_hash" in data

    def test_response_has_commit_timestamp(self, client, mock_identity_client, auth_headers, make_repo):
        repo = make_repo()
        data = client.get(_url(repo.id), headers=auth_headers()).json()
        assert "commit_timestamp" in data

    def test_repo_id_in_response_matches_path(
        self, client, mock_identity_client, auth_headers, make_repo
    ):
        repo = make_repo()
        data = client.get(_url(repo.id), headers=auth_headers()).json()
        assert data["repo_id"] == str(repo.id)

    def test_no_commits_returns_null_hash(
        self, client, mock_identity_client, auth_headers, make_repo
    ):
        """A freshly created repo has no commits — latest_commit_hash must be null."""
        repo = make_repo(latest_commit_hash=None)
        data = client.get(_url(repo.id), headers=auth_headers()).json()
        assert data["latest_commit_hash"] is None

    def test_no_commits_returns_null_timestamp(
        self, client, mock_identity_client, auth_headers, make_repo
    ):
        """No commits means no commit timestamp to return."""
        repo = make_repo(latest_commit_hash=None)
        data = client.get(_url(repo.id), headers=auth_headers()).json()
        assert data["commit_timestamp"] is None

    def test_returns_current_latest_commit_hash(
        self, client, mock_identity_client, auth_headers, make_repo, make_commit, advance_repo_head
    ):
        """After a commit is approved, the endpoint returns its hash."""
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_USER_ID)
        advance_repo_head(repo, commit.commit_hash)

        data = client.get(_url(repo.id), headers=auth_headers()).json()
        assert data["latest_commit_hash"] == commit.commit_hash

    def test_commit_timestamp_is_populated_when_commits_exist(
        self, client, mock_identity_client, auth_headers, make_repo, make_commit, advance_repo_head
    ):
        """When the repo has a latest commit, commit_timestamp must be a non-null ISO string."""
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_USER_ID)
        advance_repo_head(repo, commit.commit_hash)

        data = client.get(_url(repo.id), headers=auth_headers()).json()
        assert data["commit_timestamp"] is not None

    def test_commit_timestamp_is_iso8601_string(
        self, client, mock_identity_client, auth_headers, make_repo, make_commit, advance_repo_head
    ):
        """commit_timestamp must be parseable as an ISO-8601 datetime."""
        from datetime import datetime
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_USER_ID)
        advance_repo_head(repo, commit.commit_hash)

        data = client.get(_url(repo.id), headers=auth_headers()).json()
        # Should not raise
        datetime.fromisoformat(data["commit_timestamp"].replace("Z", "+00:00"))

    def test_head_reflects_latest_after_advance(
        self, client, mock_identity_client, auth_headers, make_repo,
        make_commit, advance_repo_head, db_session
    ):
        """
        When HEAD advances (a second commit is approved), the polling endpoint
        immediately returns the new hash — not the old one.
        """
        repo = make_repo()
        first_commit = make_commit(repo_id=repo.id, owner_id=_USER_ID, commit_summary="First")
        advance_repo_head(repo, first_commit.commit_hash)

        second_commit = make_commit(
            repo_id=repo.id,
            owner_id=_USER_ID,
            parent_commit_hash=first_commit.commit_hash,
            commit_summary="Second",
        )
        advance_repo_head(repo, second_commit.commit_hash)

        data = client.get(_url(repo.id), headers=auth_headers()).json()
        assert data["latest_commit_hash"] == second_commit.commit_hash
        assert data["latest_commit_hash"] != first_commit.commit_hash

    def test_two_repos_return_independent_heads(
        self, client, mock_identity_client, auth_headers, make_repo,
        make_commit, advance_repo_head
    ):
        """HEAD polling is scoped to the requested repo — other repos' states are independent."""
        repo_a = make_repo(owner_id=_USER_ID, repo_name="repo-a")
        repo_b = make_repo(owner_id=_USER_ID, repo_name="repo-b")

        commit_a = make_commit(repo_id=repo_a.id, owner_id=_USER_ID, commit_summary="A only")
        advance_repo_head(repo_a, commit_a.commit_hash)
        # repo_b deliberately has no commits

        data_a = client.get(_url(repo_a.id), headers=auth_headers()).json()
        data_b = client.get(_url(repo_b.id), headers=auth_headers()).json()

        assert data_a["latest_commit_hash"] == commit_a.commit_hash
        assert data_b["latest_commit_hash"] is None


# ---------------------------------------------------------------------------
# Role-based access
# ---------------------------------------------------------------------------

class TestHeadPollingRoleAccess:
    """
    All four member roles must be able to poll the HEAD — this endpoint is
    read-only and drives the stale-draft detection in the author's UI as
    well as the reviewer's queue.
    """

    def test_admin_can_poll_head(
        self, client, mock_identity_client, auth_headers, make_repo
    ):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_author_can_poll_head(
        self, client, mock_identity_client, auth_headers, make_repo
    ):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_reviewer_can_poll_head(
        self, client, mock_identity_client, auth_headers, make_repo
    ):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_reader_can_poll_head(
        self, client, mock_identity_client, auth_headers, make_repo
    ):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 200

    def test_non_member_cannot_poll_head(
        self, client, mock_identity_client, auth_headers, make_repo
    ):
        """A user with no membership in the repo must be denied."""
        mock_identity_client.return_value = None
        repo = make_repo()
        assert client.get(_url(repo.id), headers=auth_headers()).status_code == 403


# ---------------------------------------------------------------------------
# Repository guard
# ---------------------------------------------------------------------------

class TestHeadPollingRepositoryGuard:
    def test_unknown_repo_returns_404(
        self, client, mock_identity_client, auth_headers
    ):
        r = client.get(_url(uuid.uuid4()), headers=auth_headers())
        assert r.status_code == 404

    def test_404_detail_mentions_repository(
        self, client, mock_identity_client, auth_headers
    ):
        r = client.get(_url(uuid.uuid4()), headers=auth_headers())
        assert "repository" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Authentication guard
# ---------------------------------------------------------------------------

class TestHeadPollingAuth:
    def test_no_token_returns_401(self, client, make_repo):
        repo = make_repo()
        assert client.get(_url(repo.id)).status_code == 401

    def test_expired_token_returns_401(
        self, client, mock_identity_client, auth_headers, make_repo
    ):
        repo = make_repo()
        assert client.get(
            _url(repo.id), headers=auth_headers(expired=True)
        ).status_code == 401
