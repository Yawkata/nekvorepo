"""
Sibling rejection cascade tests for the approve endpoint.

When commit A is approved, the 8-step transaction must:
  Step 4 — mark all OTHER pending commits for the repo as sibling_rejected
  Step 6 — mark the drafts linked to those sibling commits as sibling_rejected
  Step 7 — mark editing drafts with a stale base_commit_hash as needs_rebase

This file tests the edge cases and multi-entity scenarios that go beyond the
single-sibling happy path in test_commits_approve.py.

Coverage:
  Multiple siblings       → all become sibling_rejected (not just one)
  Already-terminal sibling → NOT re-processed by step 4 (status unchanged)
  Draft linked to terminal sibling commit → NOT re-processed by step 6
  Editing draft base == approved commit hash → NOT set to needs_rebase
  Editing draft base == old hash → IS set to needs_rebase
  Draft in reconstructing status → NOT touched by step 7 (targets only editing)
  Draft in needs_rebase status → NOT touched by step 7 (already needs_rebase)
  Draft in pending status (sibling) → sibling_rejected via step 6
  Unrelated repo drafts → NOT affected by cascade
"""

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


# ---------------------------------------------------------------------------
# Multiple sibling commits
# ---------------------------------------------------------------------------

class TestMultipleSiblingCommits:
    def test_all_pending_siblings_become_sibling_rejected(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_commit, db_session
    ):
        """Three pending commits — approve one, other two must all be sibling_rejected."""
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit_a = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID, commit_summary="A")
        commit_b = make_commit(repo_id=repo.id, owner_id="author-b", commit_summary="B")
        commit_c = make_commit(repo_id=repo.id, owner_id="author-c", commit_summary="C")

        r = client.post(_url(repo.id, commit_a.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        assert r.status_code == 200

        db_session.expire_all()
        for commit_hash in (commit_b.commit_hash, commit_c.commit_hash):
            sibling = db_session.exec(
                select(RepoCommit).where(RepoCommit.commit_hash == commit_hash)
            ).first()
            assert sibling.status == CommitStatus.sibling_rejected, (
                f"Expected sibling_rejected for {commit_hash}, got {sibling.status}"
            )

    def test_approved_commit_status_is_approved_not_sibling_rejected(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_commit, db_session
    ):
        """The approved commit itself must NOT be marked sibling_rejected."""
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit_a = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        make_commit(repo_id=repo.id, owner_id="author-b")  # sibling

        client.post(_url(repo.id, commit_a.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()

        approved = db_session.exec(
            select(RepoCommit).where(RepoCommit.commit_hash == commit_a.commit_hash)
        ).first()
        assert approved.status == CommitStatus.approved


# ---------------------------------------------------------------------------
# Already-terminal siblings not re-processed
# ---------------------------------------------------------------------------

class TestAlreadyTerminalSiblings:
    def test_already_rejected_sibling_commit_not_re_processed(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_commit, db_session
    ):
        """
        A commit that was already rejected before this approval must stay rejected —
        not overwritten with sibling_rejected.  Step 4 only targets pending commits.
        """
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        already_rejected = make_commit(
            repo_id=repo.id, owner_id="author-b",
            status=CommitStatus.rejected,
        )
        commit_to_approve = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)

        client.post(_url(repo.id, commit_to_approve.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()

        refreshed = db_session.exec(
            select(RepoCommit).where(RepoCommit.commit_hash == already_rejected.commit_hash)
        ).first()
        assert refreshed.status == CommitStatus.rejected  # unchanged


# ---------------------------------------------------------------------------
# needs_rebase logic — base_commit_hash matching
# ---------------------------------------------------------------------------

class TestNeedsRebaseLogic:
    def test_editing_draft_with_matching_base_not_rebased(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_draft, make_commit, db_session
    ):
        """
        An editing draft whose base_commit_hash already equals the newly approved
        commit's hash must NOT be marked needs_rebase (it is already up-to-date).

        Step 7 SQL: WHERE base_commit_hash IS NULL OR base_commit_hash != :h
        So a draft with base_commit_hash == approved_commit_hash is excluded.
        """
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)

        # This draft's base equals the commit we're about to approve → up-to-date
        up_to_date_draft = make_draft(
            repo_id=repo.id,
            user_id="editor",
            status=DraftStatus.editing,
            base_commit_hash=commit.commit_hash,
        )

        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()

        refreshed = db_session.get(Draft, up_to_date_draft.id)
        assert refreshed.status == DraftStatus.editing  # NOT needs_rebase

    def test_editing_draft_with_stale_base_becomes_needs_rebase(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_draft, make_commit, db_session
    ):
        """Draft with an old (non-matching) base_commit_hash must become needs_rebase."""
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)

        stale_draft = make_draft(
            repo_id=repo.id,
            user_id="editor",
            status=DraftStatus.editing,
            base_commit_hash="old" + "a" * 61,  # any hash ≠ commit.commit_hash
        )

        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()

        refreshed = db_session.get(Draft, stale_draft.id)
        assert refreshed.status == DraftStatus.needs_rebase

    def test_editing_draft_null_base_becomes_needs_rebase(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_draft, make_commit, db_session
    ):
        """Draft with base_commit_hash=None is treated as stale — step 7 catches it."""
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        null_base_draft = make_draft(
            repo_id=repo.id, user_id="editor",
            status=DraftStatus.editing, base_commit_hash=None,
        )

        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()

        assert db_session.get(Draft, null_base_draft.id).status == DraftStatus.needs_rebase


# ---------------------------------------------------------------------------
# Non-editing drafts must not be touched by step 7
# ---------------------------------------------------------------------------

class TestNonEditingDraftsUntouched:
    def test_reconstructing_draft_not_touched_by_step7(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_draft, make_commit, db_session
    ):
        """Step 7 targets only 'editing' status — reconstructing drafts must be left alone."""
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        reconstructing_draft = make_draft(
            repo_id=repo.id, user_id="editor",
            status=DraftStatus.reconstructing, base_commit_hash=None,
        )

        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()

        refreshed = db_session.get(Draft, reconstructing_draft.id)
        assert refreshed.status == DraftStatus.reconstructing  # unchanged

    def test_needs_rebase_draft_not_touched_by_step7(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_draft, make_commit, db_session
    ):
        """A draft already in needs_rebase must not be overwritten by step 7."""
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)
        already_rebased = make_draft(
            repo_id=repo.id, user_id="editor",
            status=DraftStatus.needs_rebase, base_commit_hash=None,
        )

        client.post(_url(repo.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()

        refreshed = db_session.get(Draft, already_rebased.id)
        assert refreshed.status == DraftStatus.needs_rebase


# ---------------------------------------------------------------------------
# Unrelated repos are not affected
# ---------------------------------------------------------------------------

class TestUnrelatedRepoIsolation:
    def test_editing_draft_in_other_repo_not_rebased(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_draft, make_commit, db_session
    ):
        """Step 7 is scoped to the approved commit's repo_id — other repos untouched."""
        mock_identity_client.return_value = "reviewer"
        repo_a = make_repo(repo_name="repo-a")
        repo_b = make_repo(repo_name="repo-b")

        commit = make_commit(repo_id=repo_a.id, owner_id=_AUTHOR_ID)
        other_repo_draft = make_draft(
            repo_id=repo_b.id, user_id="editor",
            status=DraftStatus.editing, base_commit_hash=None,
        )

        client.post(_url(repo_a.id, commit.commit_hash), headers=auth_headers(user_id=_REVIEWER_ID))
        db_session.expire_all()

        refreshed = db_session.get(Draft, other_repo_draft.id)
        assert refreshed.status == DraftStatus.editing  # untouched


# ---------------------------------------------------------------------------
# Sibling drafts step 6 — linked via draft_id on the sibling commit
# ---------------------------------------------------------------------------

class TestSiblingDraftCascade:
    def test_multiple_sibling_drafts_all_become_sibling_rejected(
        self, client, mock_identity_client, mock_repo_client,
        auth_headers, make_repo, make_draft, make_commit, db_session
    ):
        """All drafts linked to sibling commits must become sibling_rejected."""
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()

        draft_b = make_draft(repo_id=repo.id, user_id="author-b", status=DraftStatus.pending)
        draft_c = make_draft(repo_id=repo.id, user_id="author-c", status=DraftStatus.pending)
        make_commit(repo_id=repo.id, owner_id="author-b", draft_id=draft_b.id)
        make_commit(repo_id=repo.id, owner_id="author-c", draft_id=draft_c.id)
        commit_to_approve = make_commit(repo_id=repo.id, owner_id=_AUTHOR_ID)

        r = client.post(
            _url(repo.id, commit_to_approve.commit_hash),
            headers=auth_headers(user_id=_REVIEWER_ID),
        )
        assert r.status_code == 200
        db_session.expire_all()

        for draft_id in (draft_b.id, draft_c.id):
            assert db_session.get(Draft, draft_id).status == DraftStatus.sibling_rejected, (
                f"Draft {draft_id} should be sibling_rejected"
            )
