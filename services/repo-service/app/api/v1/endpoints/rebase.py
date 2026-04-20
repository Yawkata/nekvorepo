"""
Phase 7 — Stale Draft Detection & Rebase Flow.

Three endpoints:

  GET  /{repo_id}/head
      Poll the repo's current HEAD commit hash and timestamp.
      Any repo member may call this; used by the frontend on a 30-second interval
      (with ±5 s jitter + Page Visibility API pause) to detect stale drafts.

  POST /{repo_id}/conflicts
      Three-way diff between the draft EFS tree (rebase mode) or reconstructed
      sibling tree (sibling mode) against the base and a client-supplied HEAD
      commit.  The HEAD hash is pinned at page-load time so the diff stays stable
      while new commits may arrive concurrently.

      Request body: { draft_id, head, mode: "rebase"|"sibling" }
      Response: structured file list, each entry carrying a category from:
        no_conflict, conflict, added_in_head, deleted_in_head, type_collision

  POST /{repo_id}/drafts/{draft_id}/rebase
      Finalise a rebase after the author resolved all conflicts.
      Checks that the HEAD has not moved again (409 if it has), then wipes the
      draft EFS directory, restores HEAD blobs from S3, and overlays the
      author's existing draft files on top before advancing the draft to 'editing'.

URL prefix (applied in api.py): /v1/repos
"""
import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from shared.constants import DraftStatus, RepoRole
from shared.models.repo import Draft
from shared.models.workflow import RepoCommit, RepoHead
from shared.tree_utils import collect_blobs
from app.api import deps
from app.services.efs import EFSService

log = structlog.get_logger()
router = APIRouter()

# .deleted extension is defined in EFSService but not exported — mirror it here
# rather than coupling to a private symbol.
_DELETED_EXT = ".deleted"

# Mode ↔ required draft status mapping (validates that the request makes sense).
_MODE_TO_STATUS: dict[str, DraftStatus] = {
    "rebase": DraftStatus.needs_rebase,
    "sibling": DraftStatus.sibling_rejected,
}

# Both needs_rebase and sibling_rejected use identical wipe-and-rebuild logic.
_REBASE_ELIGIBLE: frozenset[DraftStatus] = frozenset({
    DraftStatus.needs_rebase,
    DraftStatus.sibling_rejected,
})


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class HeadResponse(BaseModel):
    repo_id: uuid.UUID
    latest_commit_hash: str | None
    commit_timestamp: datetime | None


class ConflictsRequest(BaseModel):
    draft_id: uuid.UUID
    head: Annotated[str, Field(min_length=1)]
    mode: Literal["rebase", "sibling"]


class FileConflictEntry(BaseModel):
    path: str
    category: str
    has_draft_changes: bool
    draft_hash: str | None
    head_hash: str | None
    base_hash: str | None


class ConflictsResponse(BaseModel):
    repo_id: uuid.UUID
    draft_id: uuid.UUID
    base_commit_hash: str | None
    head_commit_hash: str
    files: list[FileConflictEntry]


class FileResolution(BaseModel):
    """
    Author's explicit resolution choice for a single path that requires user
    action (category: conflict, deleted_in_head with draft changes, or the root
    path of a type_collision group).

    keep_mine  — the author's EFS version survives into the rebased draft.
    use_theirs — the HEAD version is used; the author's changes to this path
                 are discarded (or, for deleted_in_head, the deletion is accepted).

    save_as (type_collision only): when resolution='use_theirs' and the author
    wants to preserve their draft file's content under a different path before
    the collision is resolved in favour of HEAD.
    """
    path: str = Field(..., min_length=1)
    resolution: Literal["keep_mine", "use_theirs"]
    save_as: str | None = Field(
        default=None,
        description=(
            "Only for type_collision with resolution='use_theirs': "
            "if provided, the author's draft file content is saved under this "
            "new path before the collision is resolved in favour of HEAD."
        ),
    )


class RebaseContinueRequest(BaseModel):
    expected_head_commit_hash: Annotated[str, Field(min_length=1)]
    resolutions: list[FileResolution] = Field(
        default_factory=list,
        description=(
            "Author's explicit resolution choices. Required for: "
            "(1) conflict paths, "
            "(2) deleted_in_head paths where the draft has local changes, "
            "(3) one entry per type_collision group keyed by the group root "
            "(the shortest path involved in the collision). "
            "No entry needed for no_conflict or added_in_head paths — "
            "those are resolved automatically."
        ),
    )


class RebaseContinueResponse(BaseModel):
    draft_id: uuid.UUID
    status: DraftStatus
    base_commit_hash: str | None


# ---------------------------------------------------------------------------
# Shared DB helpers (scoped to this module — mirrors drafts.py helpers)
# ---------------------------------------------------------------------------

def _get_repo_or_404(db: Session, repo_id: uuid.UUID) -> RepoHead:
    repo = db.get(RepoHead, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found.")
    return repo


def _get_draft_or_404(db: Session, repo_id: uuid.UUID, draft_id: uuid.UUID) -> Draft:
    """
    Return the draft if it exists in this repo; 404 if no row at all.

    Unlike the drafts.py helper, we do NOT hide 'deleted' status here.
    The status gate that follows (needs_rebase check) is responsible for
    returning 400 for any non-eligible status, including 'deleted'.
    This matches the spec: "any status other than needs_rebase → 400".
    """
    draft = db.exec(
        select(Draft).where(Draft.id == draft_id, Draft.repo_id == repo_id)
    ).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return draft


def _require_author_or_admin(role: str) -> None:
    if role not in (RepoRole.admin.value, RepoRole.author.value):
        raise HTTPException(
            status_code=403,
            detail="Only authors and admins can manage drafts.",
        )


def _require_draft_access(draft: Draft, user_id: str, role: str) -> None:
    """Allow draft owner (any eligible role) or admin (any user)."""
    if draft.user_id != user_id and role != RepoRole.admin.value:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this draft.",
        )


# ---------------------------------------------------------------------------
# EFS walking helpers
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_path_covered(rel_path: str, deleted: set[str]) -> bool:
    """
    Return True if rel_path or any ancestor segment appears in the deleted set.

    Example: if "docs" is deleted, then "docs/api.md" is covered.
    This mirrors the logic in EFSService so the conflict classifier respects
    the same deletion semantics.
    """
    if rel_path in deleted:
        return True
    parts = rel_path.split("/")
    for i in range(1, len(parts)):
        if "/".join(parts[:i]) in deleted:
            return True
    return False


def _walk_draft_efs_hashes(
    draft_dir: Path,
) -> tuple[dict[str, str], set[str]]:
    """
    Walk the draft EFS directory for the conflicts endpoint.

    Returns
    -------
    blobs   : {relative_path: sha256_hex}  for every live (non-deleted) file
    deleted : set of relative paths covered by a .deleted marker

    Algorithm (two-pass, mirrors EFSService.list_files):
      Pass 1 — collect all .deleted markers → build the deleted set.
      Pass 2 — collect live files, compute SHA-256, skip deleted paths.
    """
    blobs: dict[str, str] = {}
    deleted: set[str] = set()

    if not draft_dir.is_dir():
        return blobs, deleted

    # Pass 1: build deletion index
    for p in draft_dir.rglob("*"):
        if p.is_file():
            rel = p.relative_to(draft_dir).as_posix()
            if rel.endswith(_DELETED_EXT):
                deleted.add(rel[: -len(_DELETED_EXT)])

    # Pass 2: hash live files
    for p in draft_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(draft_dir).as_posix()
        if rel.endswith(_DELETED_EXT):
            continue
        if _is_path_covered(rel, deleted):
            continue
        blobs[rel] = _sha256_hex(p.read_bytes())

    return blobs, deleted


def _snapshot_draft_efs(
    draft_dir: Path,
) -> tuple[dict[str, bytes], set[str]]:
    """
    Snapshot the draft EFS directory for the rebase endpoint.

    Unlike _walk_draft_efs_hashes, this captures the actual file *content*
    so the rebase endpoint can restore it after the wipe-and-rebuild.

    Returns
    -------
    files   : {relative_path: bytes}  for every live (non-deleted) file
    deleted : set of relative paths covered by a .deleted marker
    """
    files: dict[str, bytes] = {}
    deleted: set[str] = set()

    if not draft_dir.is_dir():
        return files, deleted

    # Pass 1: deletion index
    for p in draft_dir.rglob("*"):
        if p.is_file():
            rel = p.relative_to(draft_dir).as_posix()
            if rel.endswith(_DELETED_EXT):
                deleted.add(rel[: -len(_DELETED_EXT)])

    # Pass 2: capture live file content
    for p in draft_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(draft_dir).as_posix()
        if rel.endswith(_DELETED_EXT):
            continue
        if not _is_path_covered(rel, deleted):
            files[rel] = p.read_bytes()

    return files, deleted


# ---------------------------------------------------------------------------
# Three-way diff — conflict classification
# ---------------------------------------------------------------------------

def _detect_type_collisions(
    head_blobs: dict[str, str],
    draft_blobs: dict[str, str],
    draft_deleted: set[str],
) -> set[str]:
    """
    Detect paths that are used as a file in one context and as a directory
    prefix in another context.

    A type collision at path P means:
      - draft has a file at P  AND  head has files under P/ (head uses P as dir)
      - head has a file at P   AND  draft has files under P/ (draft uses P as dir)

    Both the file path and any conflicting sub-paths are added to the returned set
    so every affected entry gets the type_collision category.
    """
    collisions: set[str] = set()
    all_draft_paths = set(draft_blobs) | draft_deleted

    # Draft file at P, head uses P as a directory
    for draft_path in all_draft_paths:
        prefix = draft_path + "/"
        for head_path in head_blobs:
            if head_path.startswith(prefix):
                collisions.add(draft_path)
                collisions.add(head_path)
                break

    # Head file at P, draft uses P as a directory
    for head_path in head_blobs:
        prefix = head_path + "/"
        for draft_path in all_draft_paths:
            if draft_path.startswith(prefix):
                collisions.add(head_path)
                collisions.add(draft_path)
                break

    return collisions


def _classify_path(
    path: str,
    base_blobs: dict[str, str],
    head_blobs: dict[str, str],
    draft_blobs: dict[str, str],
    draft_deleted: set[str],
    collision_paths: set[str],
) -> FileConflictEntry:
    """
    Classify a single path and return its FileConflictEntry.

    Category decision tree (evaluated top-to-bottom):
      1. type_collision   — path involved in a file-vs-directory ambiguity
      2. deleted_in_head  — existed in base, absent in head
           4A has_draft_changes=False — draft matches base (auto-accepted)
           4B has_draft_changes=True  — draft diverged from base (needs review)
      3. added_in_head    — not in base, present in head, absent in draft
      4. conflict         — head changed/added, draft also changed differently
      5. no_conflict      — everything else (one side or neither changed)
           1A has_draft_changes=True  — only draft changed (rebase will keep)
           1B has_draft_changes=False — only head changed (auto-accepted)
           1C has_draft_changes=False — neither changed
    """
    base_hash = base_blobs.get(path)
    head_hash = head_blobs.get(path)
    raw_draft_hash = draft_blobs.get(path)   # None if absent
    is_deleted = path in draft_deleted
    # Effective draft hash: None when author deleted the file
    draft_hash = None if is_deleted else raw_draft_hash

    # ── 1. type_collision ────────────────────────────────────────────────────
    if path in collision_paths:
        has_changes = is_deleted or (
            raw_draft_hash is not None and raw_draft_hash != base_hash
        )
        return FileConflictEntry(
            path=path,
            category="type_collision",
            has_draft_changes=has_changes,
            draft_hash=draft_hash,
            head_hash=head_hash,
            base_hash=base_hash,
        )

    # ── 2. deleted_in_head ───────────────────────────────────────────────────
    if base_hash is not None and head_hash is None:
        has_changes = (
            (raw_draft_hash is not None and raw_draft_hash != base_hash)
            or is_deleted
        )
        return FileConflictEntry(
            path=path,
            category="deleted_in_head",
            has_draft_changes=has_changes,
            draft_hash=draft_hash,
            head_hash=None,
            base_hash=base_hash,
        )

    # ── 3. added_in_head ─────────────────────────────────────────────────────
    if (
        base_hash is None
        and head_hash is not None
        and raw_draft_hash is None
        and not is_deleted
    ):
        return FileConflictEntry(
            path=path,
            category="added_in_head",
            has_draft_changes=False,
            draft_hash=None,
            head_hash=head_hash,
            base_hash=None,
        )

    # ── 4. conflict ──────────────────────────────────────────────────────────
    # Head changed the file (modified or added) AND draft also changed differently.
    head_changed = head_hash is not None and head_hash != base_hash
    draft_changed = is_deleted or (
        raw_draft_hash is not None and raw_draft_hash != base_hash
    )

    if head_changed and draft_changed:
        # If both sides arrived at the same hash, they converge → no_conflict.
        if is_deleted or raw_draft_hash != head_hash:
            return FileConflictEntry(
                path=path,
                category="conflict",
                has_draft_changes=True,
                draft_hash=draft_hash,
                head_hash=head_hash,
                base_hash=base_hash,
            )

    # ── 5. no_conflict ───────────────────────────────────────────────────────
    return FileConflictEntry(
        path=path,
        category="no_conflict",
        has_draft_changes=draft_changed,
        draft_hash=draft_hash,
        head_hash=head_hash,
        base_hash=base_hash,
    )


# ---------------------------------------------------------------------------
# Rebase resolution helpers
# ---------------------------------------------------------------------------

def _find_collision_roots(type_collision_paths: set[str]) -> set[str]:
    """
    A collision root is a type_collision path P where no shorter type_collision
    path is a strict prefix of P.

    Example: for {"lib", "lib/core.py"}, "lib" is the root because "lib" is a
    strict prefix of "lib/core.py" but no type_collision path is a prefix of "lib".
    """
    roots: set[str] = set()
    for path in type_collision_paths:
        parts = path.split("/")
        is_root = True
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in type_collision_paths:
                is_root = False
                break
        if is_root:
            roots.add(path)
    return roots


def _find_collision_root_for(path: str, collision_roots: set[str]) -> str | None:
    """Return the collision root that this path belongs to, or None."""
    if path in collision_roots:
        return path
    parts = path.split("/")
    for i in range(len(parts) - 1, 0, -1):
        prefix = "/".join(parts[:i])
        if prefix in collision_roots:
            return prefix
    return None


def _build_final_files(
    conflict_entries: list[FileConflictEntry],
    draft_files: dict[str, bytes],
    head_content: dict[str, bytes],
    resolution_map: dict[str, FileResolution],
    collision_roots: set[str],
    collision_root_meta: dict[str, dict[str, bool]],
) -> dict[str, bytes]:
    """
    Build the complete set of {path: bytes} to write to EFS after rebase,
    applying the author's resolution choices.

    Decision per category:

      no_conflict, has_draft_changes=True
          Sub-case A: only the draft changed → keep draft version.
          If draft deleted the file → absent (deletion kept).

      no_conflict, has_draft_changes=False
          Sub-case B/C: only HEAD changed or neither changed → HEAD version.

      conflict, keep_mine
          Author's EFS version wins.  If draft deleted the file → absent.

      conflict, use_theirs
          HEAD version overwrites the draft change.

      added_in_head
          HEAD blob written automatically — informational for the author.

      deleted_in_head 4A (has_draft_changes=False)
          Deletion auto-accepted — path absent from final state.

      deleted_in_head 4B (has_draft_changes=True), keep_mine
          Author keeps their modified version.

      deleted_in_head 4B, use_theirs
          Author accepts the deletion — path absent.

      type_collision root, keep_mine
          If draft has root as a plain file → draft file kept; HEAD dir entries
          for this root are dropped.
          If HEAD has root as a plain file → HEAD file dropped; draft dir entries
          under root are kept (handled by the non-root iteration).

      type_collision root, use_theirs
          If draft has root as a plain file → draft file dropped (optionally
          saved under save_as); HEAD dir entries for root are kept.
          If HEAD has root as a plain file → HEAD file kept; draft dir entries
          under root are dropped.

      type_collision non-root (dir side entries)
          Implied by the root resolution — kept or dropped accordingly.
    """
    final: dict[str, bytes] = {}

    for entry in conflict_entries:
        path = entry.path

        if entry.category == "no_conflict":
            if entry.has_draft_changes:
                # Sub-case A: only draft changed → keep draft version
                if path in draft_files:
                    final[path] = draft_files[path]
                # else draft deleted it → absent (deletion kept)
            elif entry.head_hash is not None:
                # Sub-case B or C → use HEAD version
                if path in head_content:
                    final[path] = head_content[path]

        elif entry.category == "conflict":
            res = resolution_map.get(path)
            if res and res.resolution == "keep_mine":
                if path in draft_files:
                    final[path] = draft_files[path]
                # else draft deleted it → keep deletion (absent)
            else:  # use_theirs (or safety default when res is None)
                if path in head_content:
                    final[path] = head_content[path]

        elif entry.category == "added_in_head":
            if path in head_content:
                final[path] = head_content[path]

        elif entry.category == "deleted_in_head":
            if entry.has_draft_changes:
                # 4B: requires explicit resolution
                res = resolution_map.get(path)
                if res and res.resolution == "keep_mine":
                    if path in draft_files:
                        final[path] = draft_files[path]
                # use_theirs → accept deletion, path stays absent
            # 4A: auto-accepted deletion → absent

        elif entry.category == "type_collision":
            root = _find_collision_root_for(path, collision_roots)
            if root is None:
                continue
            meta = collision_root_meta.get(root, {})
            draft_is_file = meta.get("draft_is_file", False)
            res = resolution_map.get(root)
            if res is None:
                continue

            if path == root:
                # Root path — the "file" side of the collision
                if draft_is_file:
                    # Draft has root as a plain file; HEAD uses root as a dir prefix.
                    if res.resolution == "keep_mine":
                        if root in draft_files:
                            final[root] = draft_files[root]
                        # HEAD dir entries under root will be skipped in non-root iteration
                    else:  # use_theirs: HEAD dir wins, draft file discarded
                        if res.save_as and root in draft_files:
                            final[res.save_as] = draft_files[root]
                        # HEAD dir entries are kept via the non-root iteration
                else:
                    # HEAD has root as a plain file; draft uses root as a dir prefix.
                    if res.resolution == "use_theirs":
                        if root in head_content:
                            final[root] = head_content[root]
                        # Draft dir entries under root will be skipped in non-root iteration
                    # keep_mine: HEAD file discarded; draft dir entries kept via non-root

            else:
                # Non-root path — part of the "dir" side of the collision
                if draft_is_file:
                    # These are HEAD's dir entries under root
                    if res.resolution == "use_theirs":
                        if path in head_content:
                            final[path] = head_content[path]
                    # keep_mine: HEAD dir entries discarded
                else:
                    # These are draft's dir entries under root
                    if res.resolution == "keep_mine":
                        if path in draft_files:
                            final[path] = draft_files[path]
                    # use_theirs: draft dir entries discarded

    return final


# ---------------------------------------------------------------------------
# GET /v1/repos/{repo_id}/head
# ---------------------------------------------------------------------------

@router.get(
    "/{repo_id}/head",
    response_model=HeadResponse,
    summary="Get the current HEAD commit hash and timestamp",
)
def get_head(
    repo_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    member: tuple = Depends(deps.require_member),
) -> HeadResponse:
    """
    Returns the repo's `latest_commit_hash` and the timestamp of that commit.

    Called by the frontend on a ~30-second interval to detect whether another
    author's commit has advanced the HEAD past the current draft's base, triggering
    the stale-draft banner and the Conflict Review flow.

    Accessible to all repo members (any role).  Returns null fields when the repo
    has no commits yet.
    """
    repo = _get_repo_or_404(db, repo_id)

    commit_timestamp: datetime | None = None
    if repo.latest_commit_hash:
        commit = db.exec(
            select(RepoCommit).where(
                RepoCommit.commit_hash == repo.latest_commit_hash
            )
        ).first()
        if commit:
            commit_timestamp = commit.timestamp

    log.debug("head_polled", repo_id=str(repo_id), hash=repo.latest_commit_hash)

    return HeadResponse(
        repo_id=repo.id,
        latest_commit_hash=repo.latest_commit_hash,
        commit_timestamp=commit_timestamp,
    )


# ---------------------------------------------------------------------------
# POST /v1/repos/{repo_id}/conflicts
# ---------------------------------------------------------------------------

@router.post(
    "/{repo_id}/conflicts",
    response_model=ConflictsResponse,
    summary="Classify file conflicts for a stale draft",
)
def classify_conflicts(
    repo_id: uuid.UUID,
    body: ConflictsRequest,
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
    member: tuple = Depends(deps.require_member),
) -> ConflictsResponse:
    """
    Computes a three-way diff between:
      - Base tree   : the commit the draft was forked from  (from DB, Table 3/4)
      - HEAD tree   : the client-supplied `head` commit     (from DB, Table 3/4)
      - Draft state : current EFS files (rebase) or S3 snapshot (sibling)

    The `head` commit hash is supplied by the client, pinned when the Conflict
    Review Screen was opened.  The server diffs against this exact commit rather
    than re-reading `repo.latest_commit_hash`, so the classification remains
    stable even if another commit is approved concurrently.

    Requires author or admin role.  The caller must own the draft (or be admin).
    """
    passport, role = member
    _require_author_or_admin(role)

    repo = _get_repo_or_404(db, repo_id)

    draft = _get_draft_or_404(db, repo_id, body.draft_id)
    _require_draft_access(draft, passport.user_id, role)

    # Mode must match the draft's current status
    required_status = _MODE_TO_STATUS[body.mode]
    if draft.status != required_status:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Mode '{body.mode}' requires draft status "
                f"'{required_status.value}', got '{draft.status.value}'."
            ),
        )

    # Validate that the client-supplied HEAD commit belongs to this repo
    head_commit = db.exec(
        select(RepoCommit).where(RepoCommit.commit_hash == body.head)
    ).first()
    if not head_commit or head_commit.repo_id != repo_id:
        raise HTTPException(
            status_code=404,
            detail="HEAD commit not found in this repository.",
        )

    # ── Load base tree (empty if the draft has no base commit) ───────────────
    base_blobs: dict[str, str] = {}
    if draft.base_commit_hash:
        base_commit = db.exec(
            select(RepoCommit).where(
                RepoCommit.commit_hash == draft.base_commit_hash
            )
        ).first()
        if base_commit:
            base_blobs = collect_blobs(base_commit.tree_id, db)

    # ── Load HEAD tree ────────────────────────────────────────────────────────
    head_blobs = collect_blobs(head_commit.tree_id, db)

    # ── Load draft EFS state ─────────────────────────────────────────────────
    # Use draft.user_id (from DB) — not passport.user_id — so that admins
    # calling this endpoint on another user's draft get the correct EFS path.
    draft_dir = efs.draft_dir(
        draft.user_id, str(repo_id), str(body.draft_id)
    )
    draft_blobs, draft_deleted = _walk_draft_efs_hashes(draft_dir)

    # ── Detect type collisions across draft and head ──────────────────────────
    collision_paths = _detect_type_collisions(head_blobs, draft_blobs, draft_deleted)

    # ── Build the union of all paths and classify each one ───────────────────
    all_paths = (
        set(base_blobs)
        | set(head_blobs)
        | set(draft_blobs)
        | draft_deleted
    )

    files = [
        _classify_path(
            path,
            base_blobs,
            head_blobs,
            draft_blobs,
            draft_deleted,
            collision_paths,
        )
        for path in sorted(all_paths)
    ]

    log.info(
        "conflicts_classified",
        repo_id=str(repo_id),
        draft_id=str(body.draft_id),
        mode=body.mode,
        head_commit_hash=body.head,
        base_commit_hash=draft.base_commit_hash,
        total_paths=len(files),
    )

    return ConflictsResponse(
        repo_id=repo_id,
        draft_id=body.draft_id,
        base_commit_hash=draft.base_commit_hash,
        head_commit_hash=body.head,
        files=files,
    )


# ---------------------------------------------------------------------------
# POST /v1/repos/{repo_id}/drafts/{draft_id}/rebase
# ---------------------------------------------------------------------------

@router.post(
    "/{repo_id}/drafts/{draft_id}/rebase",
    response_model=RebaseContinueResponse,
    summary="Rebase a draft to the current HEAD after conflict resolution",
)
def rebase_continue(
    repo_id: uuid.UUID,
    draft_id: uuid.UUID,
    body: RebaseContinueRequest,
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
    member: tuple = Depends(deps.require_member),
) -> RebaseContinueResponse:
    """
    Finalises the rebase flow after the author has resolved all conflicts.

    Accepts drafts in 'needs_rebase' (pre-commit stale draft, Phase 7) or
    'sibling_rejected' (post-approval sibling conflict, Phase 8).  The
    wipe-and-rebuild logic is identical for both statuses.

    Steps:
      1. Verify draft is in 'needs_rebase' or 'sibling_rejected'.
      2. Verify caller owns the draft (or is admin).
      3. Compare `expected_head_commit_hash` with the current repo HEAD.
         → 409 {error: head_moved_again, new_head_commit_hash} if the HEAD has
           advanced again since the Conflict Review Screen was opened.
      4. Snapshot the current draft EFS directory (file content + deletion markers).
      5. Wipe the draft EFS directory.
      6. Download every blob from the HEAD commit tree via S3 → write to EFS.
      7. Overlay the snapshotted draft files on top (author's resolved state wins).
      8. Re-apply the author's deletion markers (files the author deleted survive
         even if HEAD had them).
      9. Advance draft.base_commit_hash → current HEAD; set status = 'editing'.

    The wipe-and-rebuild in steps 5–8 eliminates any partial state from a previous
    failed rebase attempt before the clean state is established.
    """
    passport, role = member
    _require_author_or_admin(role)

    repo = _get_repo_or_404(db, repo_id)
    draft = _get_draft_or_404(db, repo_id, draft_id)
    _require_draft_access(draft, passport.user_id, role)

    if draft.status not in _REBASE_ELIGIBLE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Rebase requires 'needs_rebase' or 'sibling_rejected' status, "
                f"got '{draft.status.value}'."
            ),
        )

    # ── HEAD-moved-again guard ────────────────────────────────────────────────
    if repo.latest_commit_hash != body.expected_head_commit_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "head_moved_again",
                "new_head_commit_hash": repo.latest_commit_hash,
            },
        )

    # Use draft.user_id (from DB) — not passport.user_id — so that admins
    # calling this endpoint on another user's draft get the correct EFS path.
    user_id = draft.user_id
    repo_id_str = str(repo_id)
    draft_id_str = str(draft_id)

    # ── Load base tree ────────────────────────────────────────────────────────
    base_blobs: dict[str, str] = {}
    if draft.base_commit_hash:
        base_commit = db.exec(
            select(RepoCommit).where(
                RepoCommit.commit_hash == draft.base_commit_hash
            )
        ).first()
        if base_commit:
            base_blobs = collect_blobs(base_commit.tree_id, db)

    # ── Load HEAD tree ────────────────────────────────────────────────────────
    head_commit = db.exec(
        select(RepoCommit).where(
            RepoCommit.commit_hash == body.expected_head_commit_hash
        )
    ).first()
    if not head_commit:
        raise HTTPException(status_code=404, detail="HEAD commit not found.")
    head_blob_map: dict[str, str] = collect_blobs(head_commit.tree_id, db)

    # ── Snapshot current draft EFS state (content + deletion markers) ─────────
    draft_dir = efs.draft_dir(user_id, repo_id_str, draft_id_str)
    draft_files, draft_deleted = _snapshot_draft_efs(draft_dir)
    draft_hashes: dict[str, str] = {p: _sha256_hex(c) for p, c in draft_files.items()}

    # ── Re-classify conflicts (same algorithm as /conflicts endpoint) ──────────
    collision_paths = _detect_type_collisions(head_blob_map, draft_hashes, draft_deleted)
    all_paths = set(base_blobs) | set(head_blob_map) | set(draft_hashes) | draft_deleted
    conflict_entries = [
        _classify_path(p, base_blobs, head_blob_map, draft_hashes, draft_deleted, collision_paths)
        for p in sorted(all_paths)
    ]

    # ── Find type_collision group roots ───────────────────────────────────────
    type_collision_paths_set = {
        e.path for e in conflict_entries if e.category == "type_collision"
    }
    collision_roots = _find_collision_roots(type_collision_paths_set)

    # ── Determine which paths require an explicit resolution ──────────────────
    required: set[str] = set()
    for entry in conflict_entries:
        if entry.category == "conflict":
            required.add(entry.path)
        elif entry.category == "deleted_in_head" and entry.has_draft_changes:
            required.add(entry.path)
        elif entry.category == "type_collision" and entry.path in collision_roots:
            required.add(entry.path)

    # ── Validate that all required resolutions are present ───────────────────
    resolution_map: dict[str, FileResolution] = {r.path: r for r in body.resolutions}
    missing = required - set(resolution_map)
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_resolutions",
                "paths": sorted(missing),
                "message": (
                    "Explicit resolution is required for these paths "
                    "before rebase can proceed."
                ),
            },
        )

    # ── Precompute which side of each collision has the plain file ────────────
    collision_root_meta: dict[str, dict[str, bool]] = {
        root: {
            "draft_is_file": root in draft_files,
            "head_is_file": root in head_blob_map,
        }
        for root in collision_roots
    }

    # ── Download HEAD blob content from S3 (parallel) ─────────────────────────
    from app.services.storage import StorageManager  # lazy import avoids boto3 at module load
    from concurrent.futures import ThreadPoolExecutor

    storage = StorageManager()

    def _fetch(item: tuple[str, str]) -> tuple[str, bytes]:
        p, h = item
        return p, storage.download_blob(h)

    with ThreadPoolExecutor(max_workers=min(len(head_blob_map) or 1, 8)) as pool:
        head_content: dict[str, bytes] = dict(pool.map(_fetch, head_blob_map.items()))

    # ── Build the deterministic final file state ──────────────────────────────
    final_files = _build_final_files(
        conflict_entries=conflict_entries,
        draft_files=draft_files,
        head_content=head_content,
        resolution_map=resolution_map,
        collision_roots=collision_roots,
        collision_root_meta=collision_root_meta,
    )

    # ── Wipe and cleanly rebuild EFS from the final state ────────────────────
    # Building final_files first (before touching EFS) ensures that a
    # classification error or S3 failure aborts cleanly before any data is lost.
    efs.delete_dir(user_id, repo_id_str, draft_id_str)
    efs.create_dir(user_id, repo_id_str, draft_id_str)
    for path, content in final_files.items():
        efs.write_file(user_id, repo_id_str, draft_id_str, path, content)

    # ── Advance draft state ───────────────────────────────────────────────────
    draft.base_commit_hash = repo.latest_commit_hash
    draft.status = DraftStatus.editing
    db.add(draft)
    db.commit()
    db.refresh(draft)

    log.info(
        "rebase_complete",
        repo_id=str(repo_id),
        draft_id=str(draft_id),
        new_base=repo.latest_commit_hash,
        files_written=len(final_files),
    )

    return RebaseContinueResponse(
        draft_id=draft.id,
        status=draft.status,
        base_commit_hash=draft.base_commit_hash,
    )
