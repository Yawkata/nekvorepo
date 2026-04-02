"""
Commit lifecycle endpoints.

All endpoints require membership in the target repository (via require_member).

POST   /v1/repos/{repo_id}/commits                         — submit draft for review
GET    /v1/repos/{repo_id}/commits                         — list pending commits (reviewers)
POST   /v1/repos/{repo_id}/commits/{commit_hash}/approve   — 8-step approval transaction
POST   /v1/repos/{repo_id}/commits/{commit_hash}/reject    — reviewer rejection
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlmodel import Session, select

from shared.constants import CommitStatus, DraftStatus, NodeType, RepoRole
from shared.models.workflow import Draft, RepoCommit, RepoHead, RepoTreeEntry, RepoTreeRoot
from shared.schemas.auth import TokenData

from app.api import deps
from app.core.config import settings
from app.services import repo_client
from shared.notifications import send_notification

log = structlog.get_logger()
router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SubmitCommitRequest(BaseModel):
    draft_id: uuid.UUID


class CommitResponse(BaseModel):
    commit_hash: str
    status: CommitStatus
    changes_summary: Optional[str]
    owner_id: str
    timestamp: datetime
    draft_id: Optional[uuid.UUID]


class CommitListItem(BaseModel):
    commit_hash: str
    status: CommitStatus
    changes_summary: Optional[str]
    owner_id: str
    timestamp: datetime
    draft_id: Optional[uuid.UUID]


class ApproveResponse(BaseModel):
    commit_hash: str
    status: CommitStatus
    latest_commit_hash: str


class RejectRequest(BaseModel):
    comment: Optional[str] = None

    @field_validator("comment")
    @classmethod
    def _validate_comment(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if len(v) > 500:
            raise ValueError("Reviewer comment must not exceed 500 characters.")
        return v or None


class RejectResponse(BaseModel):
    commit_hash: str
    status: CommitStatus


# ---------------------------------------------------------------------------
# Tree-building helpers
# ---------------------------------------------------------------------------

def _build_tree(blob_map: dict[str, str], db: Session) -> tuple[int, str]:
    """
    Given { "relative/path": "sha256hex" }, build the RepoTreeRoot +
    RepoTreeEntry rows and return (tree_id, tree_hash).

    Flat-tree strategy: all blobs hang off a single root tree node.
    Recursive directory trees are deferred to Phase 6+.
    """
    entries = sorted(
        [
            {"type": "blob", "name": path, "content_hash": content_hash}
            for path, content_hash in blob_map.items()
        ],
        key=lambda e: e["name"],
    )

    tree_hash = hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    # Upsert tree root — idempotent
    db.exec(  # type: ignore[arg-type]
        text(
            "INSERT INTO repo_tree_roots (tree_hash) VALUES (:h) ON CONFLICT DO NOTHING"
        ).bindparams(h=tree_hash)
    )
    db.flush()

    root = db.exec(
        select(RepoTreeRoot).where(RepoTreeRoot.tree_hash == tree_hash)
    ).one()

    # Insert tree entries only if this tree root is new
    existing_count = db.exec(  # type: ignore[arg-type]
        text(
            "SELECT COUNT(*) FROM repo_tree_entries WHERE tree_id = :tid"
        ).bindparams(tid=root.id)
    ).scalar()

    if existing_count == 0:
        for entry in entries:
            db.add(
                RepoTreeEntry(
                    tree_id=root.id,
                    type=NodeType.blob,
                    name=entry["name"],
                    content_hash=entry["content_hash"],
                    content_type="text/plain",
                    size=0,
                )
            )
        db.flush()

    return root.id, tree_hash


def _compute_changes_summary(
    new_blobs: dict[str, str],
    parent_commit_hash: Optional[str],
    db: Session,
) -> str:
    """
    Diff new blob map against the parent commit's tree.
    Returns a human-readable string like "3 files changed, 1 added, 2 modified".
    """
    if not parent_commit_hash:
        count = len(new_blobs)
        return f"{count} {'file' if count == 1 else 'files'} added"

    parent_commit = db.exec(
        select(RepoCommit).where(RepoCommit.commit_hash == parent_commit_hash)
    ).first()
    if parent_commit is None:
        count = len(new_blobs)
        return f"{count} {'file' if count == 1 else 'files'} added"

    parent_entries = db.exec(
        select(RepoTreeEntry).where(RepoTreeEntry.tree_id == parent_commit.tree_id)
    ).all()

    old_map = {e.name: e.content_hash for e in parent_entries}

    added = [k for k in new_blobs if k not in old_map]
    deleted = [k for k in old_map if k not in new_blobs]
    modified = [k for k in new_blobs if k in old_map and new_blobs[k] != old_map[k]]

    total = len(added) + len(modified) + len(deleted)
    if total == 0:
        return "No changes"

    parts = [f"{total} {'file' if total == 1 else 'files'} changed"]
    if added:
        parts.append(f"{len(added)} added")
    if modified:
        parts.append(f"{len(modified)} modified")
    if deleted:
        parts.append(f"{len(deleted)} deleted")

    return ", ".join(parts)


# ---------------------------------------------------------------------------
# POST /v1/repos/{repo_id}/commits — Submit draft for review
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=CommitResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {"description": "Not a member or insufficient role"},
        404: {"description": "Draft not found"},
        409: {"description": "Draft is not in a submittable state"},
        422: {"description": "Draft has no files to commit"},
        502: {"description": "Repo service unavailable"},
    },
)
def create_commit(
    repo_id: uuid.UUID,
    body: SubmitCommitRequest,
    db: Session = Depends(deps.get_db),
    membership: tuple[TokenData, str] = Depends(deps.require_member),
):
    """
    Submit a draft for reviewer approval.

    Saga (each failure resets draft.status to 'editing' before propagating):
      1. Validate draft ownership and status.
      2. Set draft.status = committing (commit-lock).
      3. Call repo-service sync-blobs → upload EFS files to S3.
      4. Build commit tree (RepoTreeRoot + RepoTreeEntry rows).
      5. Compute changes_summary vs parent tree.
      6. Insert RepoCommit row.
      7. Set draft.status = pending, draft.commit_hash = commit_hash.
    """
    passport, role = membership

    if role not in (RepoRole.admin, RepoRole.author):
        raise HTTPException(
            status_code=403,
            detail="Only admins and authors can submit commits.",
        )

    # Step 1 — load and validate draft
    draft = db.exec(
        select(Draft).where(
            Draft.id == body.draft_id,
            Draft.repo_id == repo_id,
        )
    ).first()
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    if draft.user_id != passport.user_id:
        raise HTTPException(status_code=403, detail="You do not own this draft.")
    if draft.status not in (DraftStatus.editing, DraftStatus.needs_rebase):
        raise HTTPException(
            status_code=409,
            detail=f"Draft cannot be submitted from status '{draft.status.value}'.",
        )

    repo_head = db.exec(select(RepoHead).where(RepoHead.id == repo_id)).first()
    if repo_head is None:
        raise HTTPException(status_code=404, detail="Repository not found.")

    parent_commit_hash = repo_head.latest_commit_hash

    # Step 2 — commit-lock: prevent concurrent submissions of the same draft
    draft.status = DraftStatus.committing
    draft.base_commit_hash = parent_commit_hash
    db.add(draft)
    db.commit()

    # Step 3 — sync blobs; compensate on failure
    try:
        blob_map = repo_client.sync_blobs(
            draft_id=body.draft_id,
            repo_id=repo_id,
            user_id=passport.user_id,
        )
    except HTTPException:
        draft.status = DraftStatus.editing
        db.add(draft)
        db.commit()
        raise

    if not blob_map:
        draft.status = DraftStatus.editing
        db.add(draft)
        db.commit()
        raise HTTPException(status_code=422, detail="Draft has no files to commit.")

    # Steps 4-7 — build tree, write commit row, update draft; compensate on failure
    try:
        tree_id, tree_hash = _build_tree(blob_map, db)
        changes_summary = _compute_changes_summary(blob_map, parent_commit_hash, db)

        now = datetime.now(timezone.utc)
        commit_hash = hashlib.sha256(
            f"{tree_hash}{repo_id}{body.draft_id}{now.isoformat()}".encode()
        ).hexdigest()

        commit = RepoCommit(
            commit_hash=commit_hash,
            repo_id=repo_id,
            owner_id=passport.user_id,
            parent_commit_hash=parent_commit_hash,
            tree_id=tree_id,
            draft_id=body.draft_id,
            status=CommitStatus.pending,
            changes_summary=changes_summary,
            author_email=passport.email,
        )
        db.add(commit)
        db.flush()

        draft.status = DraftStatus.pending
        draft.commit_hash = commit_hash
        draft.changes_summary = changes_summary
        db.add(draft)
        db.commit()
        db.refresh(commit)

    except Exception:
        db.rollback()
        try:
            draft.status = DraftStatus.editing
            db.add(draft)
            db.commit()
        except Exception:
            log.exception("create_commit_compensation_failed", draft_id=str(body.draft_id))
        log.exception("create_commit_tree_failed", draft_id=str(body.draft_id))
        raise HTTPException(status_code=500, detail="Failed to build commit tree. Please try again.")

    log.info(
        "commit_created",
        commit_hash=commit_hash,
        repo_id=str(repo_id),
        draft_id=str(body.draft_id),
        changes_summary=changes_summary,
    )

    return CommitResponse(
        commit_hash=commit.commit_hash,
        status=commit.status,
        changes_summary=commit.changes_summary,
        owner_id=commit.owner_id,
        timestamp=commit.timestamp,
        draft_id=commit.draft_id,
    )


# ---------------------------------------------------------------------------
# GET /v1/repos/{repo_id}/commits — List pending commits (reviewer queue)
# ---------------------------------------------------------------------------

@router.get(
    "",
    response_model=list[CommitListItem],
    responses={
        403: {"description": "Not a member or insufficient role"},
    },
)
def list_commits(
    repo_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    membership: tuple[TokenData, str] = Depends(deps.require_member),
):
    """Return all pending commits for the repo, newest first. Reviewer/admin only."""
    _, role = membership

    if role not in (RepoRole.admin, RepoRole.reviewer):
        raise HTTPException(
            status_code=403,
            detail="Only admins and reviewers can view the commit queue.",
        )

    commits = db.exec(
        select(RepoCommit)
        .where(
            RepoCommit.repo_id == repo_id,
            RepoCommit.status == CommitStatus.pending,
        )
        .order_by(RepoCommit.timestamp.desc())  # type: ignore[union-attr]
    ).all()

    return [
        CommitListItem(
            commit_hash=c.commit_hash,
            status=c.status,
            changes_summary=c.changes_summary,
            owner_id=c.owner_id,
            timestamp=c.timestamp,
            draft_id=c.draft_id,
        )
        for c in commits
    ]


# ---------------------------------------------------------------------------
# POST /v1/repos/{repo_id}/commits/{commit_hash}/approve — 8-step transaction
# ---------------------------------------------------------------------------

@router.post(
    "/{commit_hash}/approve",
    response_model=ApproveResponse,
    status_code=status.HTTP_200_OK,
    responses={
        403: {"description": "Not a reviewer/admin, or attempting self-approval"},
        404: {"description": "Commit not found"},
        409: {"description": "Commit is not pending, stale, or concurrent reviewer conflict"},
    },
)
def approve_commit(
    repo_id: uuid.UUID,
    commit_hash: str,
    db: Session = Depends(deps.get_db),
    membership: tuple[TokenData, str] = Depends(deps.require_member),
):
    """
    Approve a pending commit in an 8-step atomic transaction.

    Step 1: Lock the commit row.
    Step 2: Validate parent_commit_hash == repo_head.latest_commit_hash.
    Step 3: Mark commit approved.
    Step 4: Mark sibling pending commits as sibling_rejected.
    Step 5: Advance repo_heads.latest_commit_hash (optimistic lock on version).
    Step 6: Mark sibling drafts as sibling_rejected.
    Step 7: Mark stale editing drafts as needs_rebase (SKIP LOCKED).
    Step 8: Mark the approved commit's draft as approved.

    After commit: call repo-service to wipe the draft EFS directory (best-effort).
    """
    passport, role = membership

    if role not in (RepoRole.admin, RepoRole.reviewer):
        raise HTTPException(
            status_code=403,
            detail="Only admins and reviewers can approve commits.",
        )

    # Step 1 — load commit, validate it's pending and not self-approval
    commit = db.exec(
        select(RepoCommit).where(
            RepoCommit.commit_hash == commit_hash,
            RepoCommit.repo_id == repo_id,
        )
    ).first()
    if commit is None:
        raise HTTPException(status_code=404, detail="Commit not found.")
    if commit.status != CommitStatus.pending:
        raise HTTPException(
            status_code=409,
            detail=f"Commit is not pending (current status: '{commit.status.value}').",
        )
    if commit.owner_id == passport.user_id:
        raise HTTPException(status_code=403, detail="You cannot approve your own commit.")

    # Step 2 — load repo_head, validate staleness
    repo_head = db.exec(select(RepoHead).where(RepoHead.id == repo_id)).first()
    if repo_head is None:
        raise HTTPException(status_code=404, detail="Repository not found.")

    if commit.parent_commit_hash != repo_head.latest_commit_hash:
        raise HTTPException(
            status_code=409,
            detail="stale_at_approval: This commit is no longer based on the latest version. The author must rebase.",
        )

    expected_version = repo_head.version

    # Collect sibling commits before modifying anything
    sibling_commits = db.exec(
        select(RepoCommit).where(
            RepoCommit.repo_id == repo_id,
            RepoCommit.status == CommitStatus.pending,
            RepoCommit.commit_hash != commit_hash,
        )
    ).all()
    sibling_commit_hashes = [c.commit_hash for c in sibling_commits]
    sibling_draft_ids = [c.draft_id for c in sibling_commits if c.draft_id is not None]

    # Step 3 — approve this commit
    commit.status = CommitStatus.approved
    db.add(commit)

    # Step 4 — sibling_reject other pending commits
    for sibling in sibling_commits:
        sibling.status = CommitStatus.sibling_rejected
        db.add(sibling)

    # Step 5 — advance repo_head with optimistic lock
    updated = db.exec(  # type: ignore[arg-type]
        text(
            "UPDATE repo_heads "
            "SET latest_commit_hash = :h, version = version + 1 "
            "WHERE id = :repo_id AND version = :v "
            "RETURNING version"
        ).bindparams(h=commit_hash, repo_id=repo_id, v=expected_version)
    ).first()

    if updated is None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="concurrent_reviewer: Another reviewer approved a commit at the same time. Please refresh and try again.",
        )

    # Step 6 — mark sibling drafts as sibling_rejected
    if sibling_draft_ids:
        sibling_drafts = db.exec(
            select(Draft).where(Draft.id.in_(sibling_draft_ids))  # type: ignore[attr-defined]
        ).all()
        for d in sibling_drafts:
            d.status = DraftStatus.sibling_rejected
            db.add(d)

    # Step 7 — mark stale editing drafts as needs_rebase (SKIP LOCKED)
    # FOR UPDATE SKIP LOCKED is a SELECT clause; use a subquery to avoid locking conflicts.
    db.exec(  # type: ignore[arg-type]
        text(
            "UPDATE drafts SET status = 'needs_rebase', base_commit_hash = :h "
            "WHERE id IN ("
            "  SELECT id FROM drafts "
            "  WHERE repo_id = :repo_id "
            "    AND status = 'editing' "
            "    AND (base_commit_hash IS NULL OR base_commit_hash != :h) "
            "  FOR UPDATE SKIP LOCKED"
            ")"
        ).bindparams(h=commit_hash, repo_id=repo_id)
    )

    # Step 8 — mark approved commit's draft as approved
    approved_draft_user_id: Optional[str] = None
    if commit.draft_id is not None:
        approved_draft = db.exec(
            select(Draft).where(Draft.id == commit.draft_id)
        ).first()
        if approved_draft is not None:
            approved_draft_user_id = approved_draft.user_id
            approved_draft.status = DraftStatus.approved
            db.add(approved_draft)

    db.commit()

    log.info(
        "commit_approved",
        commit_hash=commit_hash,
        repo_id=str(repo_id),
        approver_id=passport.user_id,
        siblings_rejected=len(sibling_commit_hashes),
    )

    # Post-commit notifications (best-effort — outside the transaction)
    send_notification(
        event="approved",
        recipient_email=commit.author_email,
        repo_name=repo_head.repo_name,
        commit_hash=commit_hash,
        from_email=settings.SES_FROM_EMAIL,
    )
    for sibling in sibling_commits:
        send_notification(
            event="sibling_rejected",
            recipient_email=sibling.author_email,
            repo_name=repo_head.repo_name,
            from_email=settings.SES_FROM_EMAIL,
        )

    # Post-commit: wipe EFS directory (best-effort — do not fail the request on error)
    if commit.draft_id is not None and approved_draft_user_id is not None:
        repo_client.wipe_draft(
            draft_id=commit.draft_id,
            repo_id=repo_id,
            user_id=approved_draft_user_id,
        )

    db.refresh(repo_head)

    return ApproveResponse(
        commit_hash=commit_hash,
        status=CommitStatus.approved,
        latest_commit_hash=repo_head.latest_commit_hash,
    )


# ---------------------------------------------------------------------------
# POST /v1/repos/{repo_id}/commits/{commit_hash}/reject — Reviewer rejection
# ---------------------------------------------------------------------------

@router.post(
    "/{commit_hash}/reject",
    response_model=RejectResponse,
    status_code=status.HTTP_200_OK,
    responses={
        403: {"description": "Not a reviewer/admin"},
        404: {"description": "Commit not found"},
        409: {"description": "Commit is not pending"},
    },
)
def reject_commit(
    repo_id: uuid.UUID,
    commit_hash: str,
    body: RejectRequest,
    db: Session = Depends(deps.get_db),
    membership: tuple[TokenData, str] = Depends(deps.require_member),
):
    """Reject a pending commit with an optional reviewer comment."""
    passport, role = membership

    if role not in (RepoRole.admin, RepoRole.reviewer):
        raise HTTPException(
            status_code=403,
            detail="Only admins and reviewers can reject commits.",
        )

    commit = db.exec(
        select(RepoCommit).where(
            RepoCommit.commit_hash == commit_hash,
            RepoCommit.repo_id == repo_id,
        )
    ).first()
    if commit is None:
        raise HTTPException(status_code=404, detail="Commit not found.")
    if commit.status != CommitStatus.pending:
        raise HTTPException(
            status_code=409,
            detail=f"Commit is not pending (current status: '{commit.status.value}').",
        )

    commit.status = CommitStatus.rejected
    commit.reviewer_comment = body.comment
    db.add(commit)

    if commit.draft_id is not None:
        draft = db.exec(select(Draft).where(Draft.id == commit.draft_id)).first()
        if draft is not None:
            draft.status = DraftStatus.rejected
            db.add(draft)

    db.commit()

    log.info(
        "commit_rejected",
        commit_hash=commit_hash,
        repo_id=str(repo_id),
        reviewer_id=passport.user_id,
        has_comment=body.comment is not None,
    )

    # Post-commit notification (best-effort — outside the transaction)
    repo_head = db.exec(select(RepoHead).where(RepoHead.id == repo_id)).first()
    send_notification(
        event="reviewer_rejected",
        recipient_email=commit.author_email,
        repo_name=repo_head.repo_name if repo_head else str(repo_id),
        commit_hash=commit_hash,
        reviewer_comment=body.comment,
        from_email=settings.SES_FROM_EMAIL,
    )

    return RejectResponse(commit_hash=commit_hash, status=CommitStatus.rejected)
