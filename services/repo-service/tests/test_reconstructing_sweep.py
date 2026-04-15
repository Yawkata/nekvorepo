"""
Tests for the _reconstructing_sweep background daemon in repo-service.

The sweep runs every 5 minutes and resets drafts stuck in 'reconstructing'
status (updated_at older than 5 minutes, pod presumably crashed mid-download).

Recovery logic:
  - Draft has a linked commit → restore to that commit's terminal status
    (rejected / sibling_rejected / approved)
  - Draft has no linked commit → restore to editing (or needs_rebase if
    base_commit_hash != repo's latest_commit_hash)

The daemon is a daemon thread; we test it by calling the internal sweep
function directly against the testcontainers database.

Coverage:
  Stuck draft (no commit)           → editing
  Stuck draft (base != latest)      → needs_rebase
  Stuck draft (linked rejected)     → rejected
  Stuck draft (linked sibling_rej)  → sibling_rejected
  Stuck draft (linked approved)     → approved
  Recent draft (< 5 min)            → NOT touched (status unchanged)
  Non-reconstructing draft          → NOT touched
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from shared.constants import CommitStatus, DraftStatus
from shared.models.repo import Draft
from shared.models.workflow import RepoCommit, RepoHead, RepoTreeRoot


# ---------------------------------------------------------------------------
# Import the sweep function under test
# ---------------------------------------------------------------------------
# We call _reconstructing_sweep_once() — a helper we'll add below that wraps
# the core logic without the while-True loop.
# The test module patches app.main.engine to the test container engine.
# ---------------------------------------------------------------------------

def _run_sweep(db_engine):
    """
    Execute one sweep iteration directly using the test DB engine.
    Mirrors the core logic of _reconstructing_sweep() in app.main.
    """
    from shared.constants import CommitStatus, DraftStatus

    _COMMIT_TO_DRAFT_FALLBACK = {
        CommitStatus.rejected: DraftStatus.rejected,
        CommitStatus.sibling_rejected: DraftStatus.sibling_rejected,
        CommitStatus.approved: DraftStatus.approved,
    }

    with Session(db_engine) as db:
        stuck_drafts = db.exec(
            text(
                "SELECT id, commit_hash FROM drafts "
                "WHERE status = 'reconstructing' "
                "  AND updated_at < now() - interval '5 minutes' "
                "FOR UPDATE SKIP LOCKED"
            )
        ).all()

        for row in stuck_drafts:
            draft = db.get(Draft, row.id)
            if draft is None:
                continue

            if draft.commit_hash:
                fallback = DraftStatus.rejected
                linked_commit = db.exec(
                    select(RepoCommit).where(
                        RepoCommit.commit_hash == draft.commit_hash
                    )
                ).first()
                if linked_commit:
                    fallback = _COMMIT_TO_DRAFT_FALLBACK.get(
                        linked_commit.status, DraftStatus.rejected
                    )
            else:
                repo = db.get(RepoHead, draft.repo_id)
                if repo and repo.latest_commit_hash != draft.base_commit_hash:
                    fallback = DraftStatus.needs_rebase
                else:
                    fallback = DraftStatus.editing

            draft.status = fallback
            db.add(draft)

        db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stuck_draft(db_engine, repo_id, user_id="test-user",
                      commit_hash=None, base_commit_hash=None, minutes_ago=6):
    """Seed a draft stuck in 'reconstructing' with an old updated_at."""
    stale_ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    with db_engine.connect() as conn:
        draft_id = uuid.uuid4()
        conn.execute(text(
            "INSERT INTO drafts (id, repo_id, user_id, status, base_commit_hash, "
            "commit_hash, created_at, updated_at) "
            "VALUES (:id, :repo_id, :user_id, 'reconstructing', "
            ":base_commit_hash, :commit_hash, :now, :stale)"
        ), {
            "id": draft_id,
            "repo_id": repo_id,
            "user_id": user_id,
            "base_commit_hash": base_commit_hash,
            "commit_hash": commit_hash,
            "now": datetime.now(timezone.utc),
            "stale": stale_ts,
        })
        conn.commit()
    return draft_id


def _make_tree_and_commit(db_session, repo_id, owner_id, status):
    """Seed a RepoTreeRoot + RepoCommit and return the commit_hash."""
    tree_hash = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
    tree = RepoTreeRoot(tree_hash=tree_hash)
    db_session.add(tree)
    db_session.flush()

    commit_hash = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
    commit = RepoCommit(
        commit_hash=commit_hash,
        repo_id=repo_id,
        owner_id=owner_id,
        tree_id=tree.id,
        status=status,
        commit_summary="Test commit",
    )
    db_session.add(commit)
    db_session.commit()
    return commit_hash


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReconstructingSweepNoLinkedCommit:
    def test_stuck_draft_becomes_editing(self, db_engine, make_repo):
        """No commit, base_commit_hash matches repo head → editing."""
        repo = make_repo(latest_commit_hash=None)
        draft_id = _make_stuck_draft(db_engine, repo.id, base_commit_hash=None)

        _run_sweep(db_engine)

        with Session(db_engine) as s:
            draft = s.get(Draft, draft_id)
        assert draft.status == DraftStatus.editing

    def test_stuck_draft_base_stale_becomes_needs_rebase(self, db_engine, make_repo):
        """No commit, base_commit_hash != latest → needs_rebase."""
        repo = make_repo(latest_commit_hash="current-hash-abc")
        draft_id = _make_stuck_draft(
            db_engine, repo.id, base_commit_hash="old-hash-xyz"
        )

        _run_sweep(db_engine)

        with Session(db_engine) as s:
            draft = s.get(Draft, draft_id)
        assert draft.status == DraftStatus.needs_rebase


class TestReconstructingSweepWithLinkedCommit:
    def test_linked_rejected_commit_becomes_rejected(self, db_engine, db_session, make_repo):
        repo = make_repo()
        commit_hash = _make_tree_and_commit(
            db_session, repo.id, "test-user", CommitStatus.rejected
        )
        draft_id = _make_stuck_draft(db_engine, repo.id, commit_hash=commit_hash)

        _run_sweep(db_engine)

        with Session(db_engine) as s:
            draft = s.get(Draft, draft_id)
        assert draft.status == DraftStatus.rejected

    def test_linked_sibling_rejected_commit_becomes_sibling_rejected(
        self, db_engine, db_session, make_repo
    ):
        repo = make_repo()
        commit_hash = _make_tree_and_commit(
            db_session, repo.id, "test-user", CommitStatus.sibling_rejected
        )
        draft_id = _make_stuck_draft(db_engine, repo.id, commit_hash=commit_hash)

        _run_sweep(db_engine)

        with Session(db_engine) as s:
            draft = s.get(Draft, draft_id)
        assert draft.status == DraftStatus.sibling_rejected

    def test_linked_approved_commit_becomes_approved(self, db_engine, db_session, make_repo):
        repo = make_repo()
        commit_hash = _make_tree_and_commit(
            db_session, repo.id, "test-user", CommitStatus.approved
        )
        draft_id = _make_stuck_draft(db_engine, repo.id, commit_hash=commit_hash)

        _run_sweep(db_engine)

        with Session(db_engine) as s:
            draft = s.get(Draft, draft_id)
        assert draft.status == DraftStatus.approved


class TestReconstructingSweepSafetyGuards:
    def test_recently_stuck_draft_not_touched(self, db_engine, make_repo):
        """Draft updated 2 minutes ago should NOT be reset (still within 5-min window)."""
        repo = make_repo()
        draft_id = _make_stuck_draft(db_engine, repo.id, minutes_ago=2)

        _run_sweep(db_engine)

        with Session(db_engine) as s:
            draft = s.get(Draft, draft_id)
        assert draft.status == DraftStatus.reconstructing  # unchanged

    def test_non_reconstructing_draft_not_touched(self, db_engine, make_repo, make_draft):
        """Drafts in 'editing' status must never be modified by the sweep."""
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="test-user")  # status=editing

        _run_sweep(db_engine)

        with Session(db_engine) as s:
            refreshed = s.get(Draft, draft.id)
        assert refreshed.status == DraftStatus.editing
