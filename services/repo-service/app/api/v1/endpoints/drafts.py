"""
Draft management and EFS file operation endpoints.

URL prefix (applied in api.py): /v1/repos

All endpoints require a valid Passport JWT.  Role requirements per operation:

  Operation                     Minimum role     Ownership check
  ─────────────────────────────────────────────────────────────────
  POST   /drafts                author OR admin  —
  GET    /drafts                any member       returns only caller's own drafts
  PATCH  /drafts/{id}           author OR admin  author must own the draft
  DELETE /drafts/{id}           author OR admin  author must own the draft
  GET    /drafts/{id}/explorer  author OR admin  author must own the draft
  GET    /drafts/{id}/files/…   author OR admin  author must own the draft
  POST   /drafts/{id}/save      author OR admin  author must own the draft
  POST   /drafts/{id}/upload    author OR admin  author must own the draft
  DELETE /drafts/{id}/files/…   author OR admin  author must own the draft
  POST   /drafts/{id}/reconstruct author OR admin author must own the draft

  Reviewer / reader roles can list repos and see commits (Phase 5) but do not
  interact with individual draft file trees in Phase 4.

Draft status gate for write operations:
  - status == committing → 409  (spec-mandated; sync-blobs is in progress)
  - status not in {editing, needs_rebase} → 400
"""
import uuid
from datetime import datetime, timezone
from typing import Annotated

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from shared.constants import DraftStatus, RepoRole
from shared.models.workflow import Draft, RepoHead
from app.api import deps
from app.services.efs import EFSService

log = structlog.get_logger()
router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DRAFT_LABEL_MAX = 100
_PATH_MAX = 4096
_SAVE_BODY_MAX = 5 * 1024 * 1024    # 5 MB text content
_UPLOAD_MAX = 100 * 1024 * 1024     # 100 MB binary upload

# Statuses that allow a draft to be hard-deleted (spec, line 135)
_DELETABLE_STATUSES = {
    DraftStatus.editing,
    DraftStatus.needs_rebase,
    DraftStatus.approved,
    DraftStatus.rejected,
}

# Statuses that allow file writes
_WRITABLE_STATUSES = {DraftStatus.editing, DraftStatus.needs_rebase}


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CreateDraftRequest(BaseModel):
    label: str | None = Field(
        default=None,
        max_length=_DRAFT_LABEL_MAX,
        description="Optional label. Defaults to 'Draft — <ISO timestamp>'.",
    )


class UpdateDraftRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=_DRAFT_LABEL_MAX)


class DraftResponse(BaseModel):
    draft_id: uuid.UUID
    repo_id: uuid.UUID
    user_id: str
    label: str | None
    status: DraftStatus
    base_commit_hash: str | None
    commit_hash: str | None
    changes_summary: str | None
    created_at: datetime
    updated_at: datetime


class ExplorerFileItem(BaseModel):
    path: str
    size: int
    is_binary: bool


class ExplorerResponse(BaseModel):
    draft_id: uuid.UUID
    files: list[ExplorerFileItem]


class SaveFileRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=_PATH_MAX)
    content: str = Field(..., description="UTF-8 text content of the file.")


class SaveFileResponse(BaseModel):
    path: str
    size: int
    large_file_warning: bool


class UploadFileResponse(BaseModel):
    path: str
    size: int
    is_binary: bool


class ReconstructResponse(BaseModel):
    task_id: str
    draft_id: uuid.UUID
    status: DraftStatus


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _draft_to_response(draft: Draft) -> DraftResponse:
    return DraftResponse(
        draft_id=draft.id,
        repo_id=draft.repo_id,
        user_id=draft.user_id,
        label=draft.label,
        status=draft.status,
        base_commit_hash=draft.base_commit_hash,
        commit_hash=draft.commit_hash,
        changes_summary=draft.changes_summary,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def _default_label() -> str:
    return "Draft — " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _get_repo_or_404(db: Session, repo_id: uuid.UUID) -> RepoHead:
    repo = db.get(RepoHead, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found.")
    return repo


def _get_draft_or_404(db: Session, repo_id: uuid.UUID, draft_id: uuid.UUID) -> Draft:
    draft = db.exec(
        select(Draft).where(Draft.id == draft_id, Draft.repo_id == repo_id)
    ).first()
    if not draft or draft.status == DraftStatus.deleted:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return draft


def _require_draft_access(draft: Draft, user_id: str, role: str) -> None:
    """
    Raise 403 if the caller neither owns the draft nor is an admin.
    Authors can only access their own drafts; admins can access any.
    """
    if draft.user_id != user_id and role != RepoRole.admin.value:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this draft.",
        )


def _require_author_or_admin(role: str) -> None:
    """Raise 403 if the role is not author or admin."""
    if role not in (RepoRole.admin.value, RepoRole.author.value):
        raise HTTPException(
            status_code=403,
            detail="Only authors and admins can manage drafts.",
        )


def _require_writable(draft: Draft) -> None:
    """Enforce the write-gate rules on save/upload/mark-deleted."""
    if draft.status == DraftStatus.committing:
        raise HTTPException(
            status_code=409,
            detail="Draft is being committed. Please wait until the commit completes.",
        )
    if draft.status not in _WRITABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Draft is not editable in status '{draft.status.value}'.",
        )


def _reject_deleted_ext(path: str) -> None:
    """Reject any path ending in .deleted — the extension is reserved for markers."""
    if path.endswith(".deleted"):
        raise HTTPException(
            status_code=400,
            detail="File paths ending in '.deleted' are reserved for internal deletion markers.",
        )


# ---------------------------------------------------------------------------
# Draft status ordering helper for list sorting
# ---------------------------------------------------------------------------

_STATUS_GROUP: dict[DraftStatus, int] = {
    DraftStatus.editing: 0,
    DraftStatus.needs_rebase: 0,
    DraftStatus.pending: 1,
    DraftStatus.committing: 1,
    DraftStatus.reconstructing: 1,
    DraftStatus.approved: 2,
    DraftStatus.rejected: 2,
    DraftStatus.sibling_rejected: 2,
}


def _sort_key(draft: Draft) -> tuple[int, float]:
    group = _STATUS_GROUP.get(draft.status, 3)
    # Negate timestamp so that sorted() gives descending order within each group
    ts = draft.updated_at.timestamp() if draft.updated_at else 0.0
    return (group, -ts)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# ── POST /v1/repos/{repo_id}/drafts ─────────────────────────────────────────

@router.post(
    "/{repo_id}/drafts",
    response_model=DraftResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft",
)
def create_draft(
    repo_id: uuid.UUID,
    body: CreateDraftRequest,
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
    member: tuple = Depends(deps.require_member),
):
    """
    Creates a Table 8 row and an EFS directory atomically.

    Saga:
      1. Validate role and repo existence.
      2. Insert Draft row (status=editing).
      3. Create EFS directory at /mnt/efs/drafts/{user_id}/{repo_id}/{draft_id}/.
         If step 3 fails: delete the Draft row and return 500.
    """
    passport, role = member
    _require_author_or_admin(role)

    repo = _get_repo_or_404(db, repo_id)

    draft = Draft(
        repo_id=repo_id,
        user_id=passport.user_id,
        label=body.label or _default_label(),
        base_commit_hash=repo.latest_commit_hash,
        status=DraftStatus.editing,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    try:
        efs.create_dir(passport.user_id, str(repo_id), str(draft.id))
    except Exception as exc:
        log.error("efs_create_failed", draft_id=str(draft.id), error=str(exc))
        db.delete(draft)
        db.commit()
        raise HTTPException(
            status_code=500,
            detail="Failed to initialise draft storage. Please try again.",
        )

    log.info("draft_created", draft_id=str(draft.id), repo_id=str(repo_id))
    return _draft_to_response(draft)


# ── GET /v1/repos/{repo_id}/drafts ──────────────────────────────────────────

@router.get(
    "/{repo_id}/drafts",
    response_model=list[DraftResponse],
    summary="List caller's drafts for a repo",
)
def list_drafts(
    repo_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    member: tuple = Depends(deps.require_member),
):
    """
    Returns all non-deleted Table 8 rows owned by the calling user for this repo,
    ordered by status group then updated_at descending:

      Group 0 (active)  : editing, needs_rebase
      Group 1 (in-flight): pending, committing, reconstructing
      Group 2 (resolved) : approved, rejected, sibling_rejected
    """
    passport, _role = member
    drafts = db.exec(
        select(Draft).where(
            Draft.repo_id == repo_id,
            Draft.user_id == passport.user_id,
            Draft.status != DraftStatus.deleted,
        )
    ).all()

    sorted_drafts = sorted(drafts, key=_sort_key)
    return [_draft_to_response(d) for d in sorted_drafts]


# ── PATCH /v1/repos/{repo_id}/drafts/{draft_id} ─────────────────────────────

@router.patch(
    "/{repo_id}/drafts/{draft_id}",
    response_model=DraftResponse,
    summary="Rename a draft",
)
def update_draft(
    repo_id: uuid.UUID,
    draft_id: uuid.UUID,
    body: UpdateDraftRequest,
    db: Session = Depends(deps.get_db),
    member: tuple = Depends(deps.require_member),
):
    """
    Updates the draft label.  Last-write-wins — no locking required for this
    cosmetic field (per spec).
    """
    passport, role = member
    draft = _get_draft_or_404(db, repo_id, draft_id)
    _require_draft_access(draft, passport.user_id, role)

    draft.label = body.label
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return _draft_to_response(draft)


# ── DELETE /v1/repos/{repo_id}/drafts/{draft_id} ────────────────────────────

@router.delete(
    "/{repo_id}/drafts/{draft_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a draft",
)
def delete_draft(
    repo_id: uuid.UUID,
    draft_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
    member: tuple = Depends(deps.require_member),
):
    """
    Hard-deletes the Table 8 row and EFS directory.

    Allowed statuses: editing, needs_rebase, approved, rejected.
    Not allowed:      pending, committing, reconstructing, sibling_rejected.
    """
    passport, role = member
    draft = _get_draft_or_404(db, repo_id, draft_id)
    _require_draft_access(draft, passport.user_id, role)
    _require_author_or_admin(role)

    if draft.status not in _DELETABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot delete a draft in status '{draft.status.value}'. "
                "Wait for the current operation to complete."
            ),
        )

    # Best-effort EFS cleanup — delete the directory before the DB row so that
    # a crash between the two leaves an orphaned directory (cleaned by Phase 5
    # SQS consumer) rather than a DB row pointing at a missing directory.
    try:
        efs.delete_dir(passport.user_id, str(repo_id), str(draft_id))
    except Exception as exc:
        log.error("efs_delete_failed", draft_id=str(draft_id), error=str(exc))
        # Non-fatal for the DB delete; the SQS consumer will clean up the orphaned dir.

    db.delete(draft)
    db.commit()
    log.info("draft_deleted", draft_id=str(draft_id), repo_id=str(repo_id))


# ── GET /v1/repos/{repo_id}/drafts/{draft_id}/explorer ──────────────────────

@router.get(
    "/{repo_id}/drafts/{draft_id}/explorer",
    response_model=ExplorerResponse,
    summary="List files in a draft",
)
def get_explorer(
    repo_id: uuid.UUID,
    draft_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
    member: tuple = Depends(deps.require_member),
):
    """
    Walks the EFS directory for this draft, resolves .deleted markers, and
    returns the live file tree with size and binary flags.

    Phase 4: lists EFS contents only (no S3 overlay — base_commit_hash is null
    for all newly created repos).
    Phase 5: will overlay EFS edits on top of the S3 snapshot at base_commit_hash.
    """
    passport, role = member
    draft = _get_draft_or_404(db, repo_id, draft_id)
    _require_draft_access(draft, passport.user_id, role)

    files = efs.list_files(passport.user_id, str(repo_id), str(draft_id))
    return ExplorerResponse(
        draft_id=draft_id,
        files=[
            ExplorerFileItem(path=f.path, size=f.size, is_binary=f.is_binary)
            for f in files
        ],
    )


# ── GET /v1/repos/{repo_id}/drafts/{draft_id}/files/{path:path} ─────────────

@router.get(
    "/{repo_id}/drafts/{draft_id}/files/{path:path}",
    summary="Read a file from a draft",
)
def get_file(
    repo_id: uuid.UUID,
    draft_id: uuid.UUID,
    path: str,
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
    member: tuple = Depends(deps.require_member),
):
    """
    Returns the raw content of a file from EFS.

    - Text files: returned as text/plain (UTF-8).
    - Binary files: returned as application/octet-stream.
    - 400 if the path ends in .deleted (reserved extension).
    - 404 if the file does not exist in EFS.
    - X-Large-File-Warning: true header when the file exceeds 1 MB.
    """
    passport, role = member
    _reject_deleted_ext(path)
    draft = _get_draft_or_404(db, repo_id, draft_id)
    _require_draft_access(draft, passport.user_id, role)

    try:
        content = efs.read_file(passport.user_id, str(repo_id), str(draft_id), path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File '{path}' not found in draft.")

    sample = content[:8192]
    is_binary = b"\x00" in sample or not _try_decode(sample)
    media_type = "application/octet-stream" if is_binary else "text/plain; charset=utf-8"

    headers: dict[str, str] = {}
    if efs.is_large(len(content)):
        headers["X-Large-File-Warning"] = "true"

    return Response(content=content, media_type=media_type, headers=headers)


def _try_decode(b: bytes) -> bool:
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


# ── POST /v1/repos/{repo_id}/drafts/{draft_id}/save ─────────────────────────

@router.post(
    "/{repo_id}/drafts/{draft_id}/save",
    response_model=SaveFileResponse,
    summary="Save a text file to a draft",
)
def save_file(
    repo_id: uuid.UUID,
    draft_id: uuid.UUID,
    body: SaveFileRequest,
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
    member: tuple = Depends(deps.require_member),
):
    """
    Writes text content to a file in EFS.

    - 400 if path ends in .deleted.
    - 409 if draft status is committing (sync-blobs is in progress).
    - 400 if draft is not in an editable state.
    - 413 if content exceeds 5 MB.
    - X-Large-File-Warning: true response header when content exceeds 1 MB.
    """
    passport, role = member
    _reject_deleted_ext(body.path)
    _require_author_or_admin(role)

    draft = _get_draft_or_404(db, repo_id, draft_id)
    _require_draft_access(draft, passport.user_id, role)
    _require_writable(draft)

    encoded = body.content.encode("utf-8")
    if len(encoded) > _SAVE_BODY_MAX:
        raise HTTPException(
            status_code=413,
            detail=f"File content exceeds the 5 MB limit ({len(encoded)} bytes).",
        )

    try:
        size = efs.write_file(
            passport.user_id, str(repo_id), str(draft_id), body.path, encoded
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        log.error("efs_write_failed", draft_id=str(draft_id), path=body.path, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to write file to storage.")

    large = efs.is_large(size)
    headers = {"X-Large-File-Warning": "true"} if large else {}

    log.info("file_saved", draft_id=str(draft_id), path=body.path, size=size)
    return SaveFileResponse(path=body.path, size=size, large_file_warning=large)


# ── POST /v1/repos/{repo_id}/drafts/{draft_id}/upload ───────────────────────

@router.post(
    "/{repo_id}/drafts/{draft_id}/upload",
    response_model=UploadFileResponse,
    summary="Upload a binary file to a draft",
)
def upload_file(
    repo_id: uuid.UUID,
    draft_id: uuid.UUID,
    path: Annotated[str, Form(min_length=1, max_length=_PATH_MAX)],
    file: Annotated[UploadFile, File()],
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
    member: tuple = Depends(deps.require_member),
):
    """
    Accepts a multipart/form-data upload and writes the binary to EFS.

    Form fields:
      path  — relative destination path within the draft
      file  — binary file content

    - 400 if path ends in .deleted.
    - 409 if draft status is committing.
    - 413 if file exceeds 100 MB.
    """
    passport, role = member
    _reject_deleted_ext(path)
    _require_author_or_admin(role)

    draft = _get_draft_or_404(db, repo_id, draft_id)
    _require_draft_access(draft, passport.user_id, role)
    _require_writable(draft)

    content = file.file.read()
    if len(content) > _UPLOAD_MAX:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the 100 MB upload limit ({len(content)} bytes).",
        )

    try:
        size = efs.write_file(
            passport.user_id, str(repo_id), str(draft_id), path, content
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        log.error("efs_upload_failed", draft_id=str(draft_id), path=path, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to write file to storage.")

    sample = content[:8192]
    is_binary = b"\x00" in sample or not _try_decode(sample)

    log.info("file_uploaded", draft_id=str(draft_id), path=path, size=size)
    return UploadFileResponse(path=path, size=size, is_binary=is_binary)


# ── DELETE /v1/repos/{repo_id}/drafts/{draft_id}/files/{path:path} ──────────

@router.delete(
    "/{repo_id}/drafts/{draft_id}/files/{path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark a file or folder as deleted",
)
def delete_file(
    repo_id: uuid.UUID,
    draft_id: uuid.UUID,
    path: str,
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
    member: tuple = Depends(deps.require_member),
):
    """
    Creates a zero-byte .deleted marker at '{path}.deleted' in EFS.
    The original file bytes are NOT removed — the marker is the authoritative
    deletion signal used by sync-blobs (Phase 5) to exclude the path from the
    committed tree.

    If path refers to a folder, the marker covers the entire subtree (any
    child whose ancestor has a .deleted marker is excluded by the explorer).

    - 409 if draft status is committing.
    - 400 if draft is not editable.
    - 400 if path ends in .deleted (cannot double-mark).
    """
    passport, role = member
    _reject_deleted_ext(path)
    _require_author_or_admin(role)

    draft = _get_draft_or_404(db, repo_id, draft_id)
    _require_draft_access(draft, passport.user_id, role)
    _require_writable(draft)

    try:
        efs.mark_deleted(passport.user_id, str(repo_id), str(draft_id), path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        log.error("efs_mark_deleted_failed", draft_id=str(draft_id), path=path, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to mark file as deleted.")

    log.info("file_marked_deleted", draft_id=str(draft_id), path=path)


# ── POST /v1/repos/{repo_id}/drafts/{draft_id}/reconstruct ──────────────────

@router.post(
    "/{repo_id}/drafts/{draft_id}/reconstruct",
    response_model=ReconstructResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reconstruct a rejected draft's file tree from S3",
)
def reconstruct_draft(
    repo_id: uuid.UUID,
    draft_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    efs: EFSService = Depends(deps.get_efs),
    member: tuple = Depends(deps.require_member),
):
    """
    Reopens a rejected or sibling-rejected draft by reconstructing its EFS
    directory from the S3 commit snapshot.

    Phase 4 behaviour:
      - Sets status to reconstructing immediately (second concurrent call → 409).
      - Wipes any partial EFS content.
      - If base_commit_hash is null (new repo, no commits yet): sets status back
        to editing synchronously and returns.
      - If base_commit_hash is set: S3 blob fetching is Phase 5.  The background
        task resets status to editing immediately as a placeholder; a note field
        indicates that actual blob restoration will be wired in Phase 5.

    Phase 5 will replace the background task body with the real S3 fetch logic.
    """
    passport, role = member
    _require_author_or_admin(role)
    draft = _get_draft_or_404(db, repo_id, draft_id)
    _require_draft_access(draft, passport.user_id, role)

    allowed = {DraftStatus.rejected, DraftStatus.sibling_rejected, DraftStatus.approved}
    if draft.status == DraftStatus.reconstructing:
        raise HTTPException(status_code=409, detail="Draft is already being reconstructed.")
    if draft.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot reconstruct a draft in status '{draft.status.value}'. "
                "Only rejected, sibling_rejected, or approved drafts can be reopened."
            ),
        )

    # Immediately set reconstructing status so concurrent calls return 409
    draft.status = DraftStatus.reconstructing
    db.add(draft)
    db.commit()

    # Wipe any partial EFS content from the previous session
    efs.delete_dir(passport.user_id, str(repo_id), str(draft_id))
    efs.create_dir(passport.user_id, str(repo_id), str(draft_id))

    task_id = str(uuid.uuid4())
    background_tasks.add_task(
        _reconstruct_task,
        draft_id=draft.id,
        base_commit_hash=draft.base_commit_hash,
    )

    log.info(
        "reconstruct_started",
        draft_id=str(draft_id),
        base_commit_hash=draft.base_commit_hash,
        task_id=task_id,
    )
    return ReconstructResponse(
        task_id=task_id,
        draft_id=draft_id,
        status=DraftStatus.reconstructing,
    )


def _reconstruct_task(draft_id: uuid.UUID, base_commit_hash: str | None) -> None:
    """
    Background task: restore draft status after reconstruction.

    Phase 4:
      - If base_commit_hash is None (empty repo), there is nothing to fetch from
        S3 — transition directly to editing.
      - If base_commit_hash is set, Phase 5 will wire in the actual blob-fetch
        logic here.  For now, transition to editing so the author is not stuck.

    Phase 5 will:
      1. Walk repo_tree_roots / repo_tree_entries for base_commit_hash.
      2. Download each blob from S3 (content_hash is the S3 key).
      3. Write each file to the draft's EFS directory.
      4. Set status = editing (or needs_rebase if a newer commit exists).
    """
    from shared.database import engine
    from sqlmodel import Session as DBSession

    with DBSession(engine) as db:
        draft = db.get(Draft, draft_id)
        if draft is None or draft.status != DraftStatus.reconstructing:
            return  # already handled or stale task

        # Phase 4: no S3 available yet — set editing immediately
        draft.status = DraftStatus.editing
        db.add(draft)
        db.commit()
        log.info(
            "reconstruct_complete",
            draft_id=str(draft_id),
            base_commit_hash=base_commit_hash,
            note="S3 blob restoration wired in Phase 5",
        )
