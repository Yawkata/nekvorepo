"""
Tests for the _committing_sweep background daemon in workflow-service.

The sweep runs every 5 minutes and resets drafts stuck in 'committing'
status where the S3/EFS sync never completed (pod crashed mid-commit).

A draft is stuck if:
  - status = 'committing'
  - updated_at is older than 5 minutes
  - NO repo_commits row exists with draft_id = this draft's id
    (the commit transaction never wrote the commit row)

If a repo_commits row DOES exist, the commit succeeded — only the final
draft-status update was lost.  The sweep must NOT reset those drafts
(a separate reconciliation path handles them).

The daemon is a daemon thread; we test it by calling the sweep SQL directly
against the testcontainers database.

Coverage:
  Stuck committing (no commit row)         → reset to editing
  Stuck committing (commit row exists)     → NOT touched (commit succeeded)
  Recent committing draft (< 5 min)        → NOT touched (within grace window)
  Non-committing draft (editing)           → NOT touched
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session

from shared.models.repo import Draft
from shared.constants import DraftStatus


# ---------------------------------------------------------------------------
# Inline sweep logic (mirrors workflow-service/app/main.py _committing_sweep)
# ---------------------------------------------------------------------------

def _run_sweep(db_engine) -> int:
    """
    Execute one sweep iteration using the test DB engine.
    Mirrors the core SQL of _committing_sweep() in app.main.
    Returns the number of rows updated.
    """
    with Session(db_engine) as db:
        result = db.exec(  # type: ignore[call-overload]
            text(
                "UPDATE drafts SET status = 'editing' "
                "WHERE id IN ("
                "  SELECT id FROM drafts "
                "  WHERE status = 'committing' "
                "    AND updated_at < now() - interval '5 minutes' "
                "    AND NOT EXISTS ("
                "      SELECT 1 FROM repo_commits c WHERE c.draft_id = drafts.id"
                "    ) "
                "  FOR UPDATE SKIP LOCKED"
                ")"
            )
        )
        recovered = result.rowcount
        db.commit()
    return recovered


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stuck_committing_draft(db_engine, repo_id, user_id="test-user", minutes_ago=6):
    """
    Seed a draft stuck in 'committing' with a stale updated_at timestamp.
    Returns the draft UUID.
    """
    stale_ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    draft_id = uuid.uuid4()
    with db_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO drafts (id, repo_id, user_id, status, created_at, updated_at) "
                "VALUES (:id, :repo_id, :user_id, 'committing', :now, :stale)"
            ),
            {
                "id": draft_id,
                "repo_id": repo_id,
                "user_id": user_id,
                "now": datetime.now(timezone.utc),
                "stale": stale_ts,
            },
        )
        conn.commit()
    return draft_id


# ---------------------------------------------------------------------------
# Core recovery: draft without commit row → editing
# ---------------------------------------------------------------------------

class TestCommittingSweepNoCommitRow:
    def test_stuck_draft_becomes_editing(self, db_engine, make_repo):
        """Stuck committing draft with NO linked commit row → reset to editing."""
        repo = make_repo()
        draft_id = _make_stuck_committing_draft(db_engine, repo.id)

        recovered = _run_sweep(db_engine)

        assert recovered >= 1
        with Session(db_engine) as s:
            draft = s.get(Draft, draft_id)
        assert draft.status == DraftStatus.editing

    def test_multiple_stuck_drafts_all_recovered(self, db_engine, make_repo):
        """All drafts in the stuck window are reset in a single sweep pass."""
        repo = make_repo()
        draft_id_a = _make_stuck_committing_draft(db_engine, repo.id, user_id="user-a")
        draft_id_b = _make_stuck_committing_draft(db_engine, repo.id, user_id="user-b")

        _run_sweep(db_engine)

        with Session(db_engine) as s:
            for draft_id in (draft_id_a, draft_id_b):
                assert s.get(Draft, draft_id).status == DraftStatus.editing


# ---------------------------------------------------------------------------
# Commit row exists → do NOT reset (commit succeeded, status update was lost)
# ---------------------------------------------------------------------------

class TestCommittingSweepWithCommitRow:
    def test_stuck_draft_with_commit_row_not_touched(
        self, db_engine, make_repo, make_commit
    ):
        """
        If a repo_commits row references this draft, the S3 sync succeeded.
        The sweep MUST NOT reset the draft — a reconciliation path handles these.
        """
        repo = make_repo()
        draft_id = _make_stuck_committing_draft(db_engine, repo.id)

        # Link a commit to the draft — simulates a successful commit transaction
        make_commit(repo_id=repo.id, draft_id=draft_id)

        recovered = _run_sweep(db_engine)

        # The draft must remain committing (the sweep skipped it)
        with Session(db_engine) as s:
            draft = s.get(Draft, draft_id)
        assert draft.status == DraftStatus.committing
        assert recovered == 0

    def test_mix_of_linked_and_unlinked_drafts(
        self, db_engine, make_repo, make_commit
    ):
        """
        Sweep resets drafts WITHOUT commit rows; leaves drafts WITH commit rows alone.
        """
        repo = make_repo()
        draft_no_commit = _make_stuck_committing_draft(db_engine, repo.id, user_id="user-a")
        draft_has_commit = _make_stuck_committing_draft(db_engine, repo.id, user_id="user-b")
        make_commit(repo_id=repo.id, draft_id=draft_has_commit)

        recovered = _run_sweep(db_engine)

        assert recovered == 1
        with Session(db_engine) as s:
            assert s.get(Draft, draft_no_commit).status == DraftStatus.editing
            assert s.get(Draft, draft_has_commit).status == DraftStatus.committing


# ---------------------------------------------------------------------------
# Safety guards: draft age and status
# ---------------------------------------------------------------------------

class TestCommittingSweepSafetyGuards:
    def test_recent_committing_draft_not_touched(self, db_engine, make_repo):
        """Draft stuck < 5 minutes ago is within the grace window — must not be reset."""
        repo = make_repo()
        draft_id = _make_stuck_committing_draft(db_engine, repo.id, minutes_ago=2)

        recovered = _run_sweep(db_engine)

        assert recovered == 0
        with Session(db_engine) as s:
            draft = s.get(Draft, draft_id)
        assert draft.status == DraftStatus.committing  # unchanged

    def test_non_committing_draft_not_touched(self, db_engine, make_repo, make_draft):
        """Drafts in 'editing' status must never be modified by the committing sweep."""
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="test-user")  # status=editing

        _run_sweep(db_engine)

        with Session(db_engine) as s:
            refreshed = s.get(Draft, draft.id)
        assert refreshed.status == DraftStatus.editing

    def test_sweep_returns_correct_count(self, db_engine, make_repo):
        """Return value is the exact number of drafts recovered."""
        repo = make_repo()
        _make_stuck_committing_draft(db_engine, repo.id, user_id="u1")
        _make_stuck_committing_draft(db_engine, repo.id, user_id="u2")
        _make_stuck_committing_draft(db_engine, repo.id, user_id="u3")

        recovered = _run_sweep(db_engine)
        assert recovered == 3

        # Running again immediately must recover nothing (already editing)
        assert _run_sweep(db_engine) == 0
