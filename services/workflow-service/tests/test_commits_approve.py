"""
Tests for POST /v1/repos/{repo_id}/commits/{commit_hash}/approve

8-step transaction:
  1. Load commit, verify pending + not self-approval
  2. Validate parent_commit_hash == repo_head.latest_commit_hash
  3. Mark commit approved
  4. Mark sibling pending commits sibling_rejected
  5. Advance repo_heads (optimistic lock on version)
  6. Mark sibling drafts sibling_rejected
  7. Mark stale editing drafts needs_rebase
  8. Mark approved draft approved

Coverage:
  Happy path    — 200, response shape, all DB state transitions verified
  Role checks   — admin/reviewer allowed; author/reader/non-member → 403; self-approval → 403
  State guards  — commit not found, already approved, already rejected, stale commit
  Auth          — no token, expired
"""

import uuid

import pytest
from sqlmodel import select

from shared.constants import CommitStatus, DraftStatus
from shared.models.workflow import RepoCommit
from shared.models.repo import Draft

_URL = "/v1/repos/{repo_id}/commits/{commit_hash}/approve"
_AUTHOR_ID   = "author-user-sub"
_REVIEWER_ID = "reviewer-user-sub"


def _url(repo_id, commit_hash):
    return _URL.format(repo_id=repo_id, commit_hash=commit_hash)


class TestApproveCommitSuccess:
    def test_returns_200(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        r = client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        assert r.status_code == 200

    def test_response_shape(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        r = client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        data = r.json()
        assert "commit_hash" in data
        assert "status" in data
        assert "latest_commit_hash" in data

    def test_response_status_approved(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        r = client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        assert r.json()["status"] == "approved"

    def test_commit_status_updated_in_db(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit, db_session):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()
        updated = db_session.exec(select(RepoCommit).where(RepoCommit.commit_hash == commit.commit_hash)).first()
        assert updated.status == CommitStatus.approved

    def test_repo_head_latest_commit_hash_updated(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit, db_session):
        from shared.models.workflow import RepoHead
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()
        updated_repo = db_session.get(RepoHead, repo.id)
        assert updated_repo.latest_commit_hash == commit.commit_hash

    def test_repo_head_version_incremented(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit, db_session):
        from shared.models.workflow import RepoHead
        mock_identity_client.return_value = "reviewer"
        repo = make_repo(version=0)
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()
        assert db_session.get(RepoHead, repo.id).version == 1

    def test_sibling_commits_marked_sibling_rejected(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit, db_session):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit_a = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, commit_summary="Commit A")
        commit_b = make_commit(repo_id=repo.id, owner_id="other-author", commit_summary="Commit B")
        client.post(_url(repo.id, commit_a.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()
        sibling = db_session.exec(select(RepoCommit).where(RepoCommit.commit_hash == commit_b.commit_hash)).first()
        assert sibling.status == CommitStatus.sibling_rejected

    def test_sibling_drafts_marked_sibling_rejected(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, make_commit, db_session):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        # Sibling: draft in pending status with a sibling commit
        sibling_draft = make_draft(repo_id=repo.id, user_id="other-author", status=DraftStatus.pending)
        sibling_commit = make_commit(repo_id=repo.id, owner_id="other-author", draft_id=sibling_draft.id)
        # Commit to approve (different author, no draft)
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()
        assert db_session.get(Draft, sibling_draft.id).status == DraftStatus.sibling_rejected

    def test_stale_editing_drafts_marked_needs_rebase(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, make_commit, db_session):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        # Editing draft with base_commit_hash=None — will become stale after approval
        editing_draft = make_draft(repo_id=repo.id, user_id="editor", status=DraftStatus.editing, base_commit_hash=None)
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()
        assert db_session.get(Draft, editing_draft.id).status == DraftStatus.needs_rebase

    def test_stale_drafts_base_commit_hash_unchanged_after_needs_rebase(
        self, client, mock_identity_client, mock_repo_client, auth_headers,
        make_repo, make_draft, make_commit, db_session,
    ):
        """
        Approving a commit must NOT overwrite base_commit_hash on drafts that are
        marked needs_rebase.  The original base is the three-way diff anchor for the
        rebase flow; clobbering it with the new HEAD hash makes every file appear
        unchanged and silently skips all conflict detection.

        Regression test for: UPDATE drafts SET status='needs_rebase', base_commit_hash=:h
        Correct behaviour:   UPDATE drafts SET status='needs_rebase'   -- hash untouched
        """
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()

        # prior_commit is what the editing draft was based on — this must survive
        prior_commit = make_commit(
            repo_id=repo.id, owner_id=_AUTHOR_ID, status=CommitStatus.approved,
            commit_summary="Prior approved commit",
        )
        editing_draft = make_draft(
            repo_id=repo.id,
            user_id="editor",
            status=DraftStatus.editing,
            base_commit_hash=prior_commit.commit_hash,  # real, non-null base
        )

        # Advance repo HEAD to prior_commit so the stale check passes for new_commit
        from shared.models.workflow import RepoHead
        repo_head = db_session.get(RepoHead, repo.id)
        repo_head.latest_commit_hash = prior_commit.commit_hash
        repo_head.version += 1
        db_session.add(repo_head)
        db_session.commit()

        # A new commit arrives and gets approved — draft must become needs_rebase
        new_commit = make_commit(
            repo_id=repo.id, owner_id=_AUTHOR_ID,
            parent_commit_hash=prior_commit.commit_hash,
        )
        client.post(_url(repo.id, new_commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))

        db_session.expire_all()
        updated = db_session.get(Draft, editing_draft.id)

        assert updated.status == DraftStatus.needs_rebase
        assert updated.base_commit_hash == prior_commit.commit_hash, (
            "base_commit_hash must remain the original base after needs_rebase — "
            f"got {updated.base_commit_hash!r}, expected {prior_commit.commit_hash!r}"
        )

    def test_stale_drafts_null_base_commit_hash_stays_null_after_needs_rebase(
        self, client, mock_identity_client, mock_repo_client, auth_headers,
        make_repo, make_draft, make_commit, db_session,
    ):
        """
        Edge case: draft with base_commit_hash=NULL must remain NULL (not be set
        to the new HEAD hash) after being marked needs_rebase.
        """
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        editing_draft = make_draft(
            repo_id=repo.id, user_id="editor",
            status=DraftStatus.editing, base_commit_hash=None,
        )
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))

        db_session.expire_all()
        updated = db_session.get(Draft, editing_draft.id)

        assert updated.status == DraftStatus.needs_rebase
        assert updated.base_commit_hash is None, (
            "base_commit_hash=NULL must stay NULL after needs_rebase — "
            f"got {updated.base_commit_hash!r}"
        )

    def test_approved_draft_marked_approved(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, make_commit, db_session):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_AUTHOR_ID, status=DraftStatus.pending)
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, draft_id=draft.id)
        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()
        assert db_session.get(Draft, draft.id).status == DraftStatus.approved

    def test_latest_commit_hash_in_response(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        r = client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        assert r.json()["latest_commit_hash"] == commit.commit_hash


class TestApproveCommitRoles:
    def test_admin_can_approve(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        assert client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID)).status_code == 200

    def test_reviewer_can_approve(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        assert client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID)).status_code == 200

    def test_author_cannot_approve(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id="someone-else")
        assert client.post(_url(repo.id, commit.commit_hash), headers=auth_headers()).status_code == 403

    def test_reader_cannot_approve(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id="someone-else")
        assert client.post(_url(repo.id, commit.commit_hash), headers=auth_headers()).status_code == 403

    def test_non_member_cannot_approve(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = None
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id="someone-else")
        assert client.post(_url(repo.id, commit.commit_hash), headers=auth_headers()).status_code == 403

    def test_self_approval_returns_403(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        """Reviewer cannot approve their own commit."""
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        # commit owner_id matches the passport user_id used to approve
        commit = make_commit(repo_id=repo.id, owner_id=_REVIEWER_ID)
        r = client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        assert r.status_code == 403


class TestApproveCommitStateGuards:
    def test_commit_not_found_returns_404(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        fake_hash = "d" * 64
        assert client.post(_url(repo.id, fake_hash), headers=auth_headers(user_id=_REVIEWER_ID)).status_code == 404

    def test_already_approved_returns_409(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, status=CommitStatus.approved)
        r = client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        assert r.status_code == 409

    def test_already_rejected_returns_409(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, status=CommitStatus.rejected)
        r = client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        assert r.status_code == 409

    def test_sibling_rejected_returns_409(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        """A commit that was already sibling-rejected cannot be approved."""
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, status=CommitStatus.sibling_rejected)
        r = client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        assert r.status_code == 409

    def test_stale_commit_returns_409(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        """Commit parent does not match repo's current latest_commit_hash."""
        mock_identity_client.return_value = "reviewer"
        # Repo already has a different latest commit
        repo = make_repo(latest_commit_hash="e" * 64)
        # Commit has parent=None → mismatch → stale
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, parent_commit_hash=None)
        r = client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        assert r.status_code == 409
        assert "stale" in r.json()["detail"].lower()


class TestApproveCommitAuth:
    def test_no_token_returns_401(self, client, make_repo, make_commit):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        assert client.post(_url(repo.id, commit.commit_hash)).status_code == 401

    def test_expired_token_returns_401(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_commit):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        r = client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(expired=True))
        assert r.status_code == 401
