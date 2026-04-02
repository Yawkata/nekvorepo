"""
View Mode endpoints — read-only access to committed file trees.

URL prefix (applied in api.py): /v1/repos

All endpoints require a valid Passport JWT.  Any repo member role is sufficient
(admin, author, reviewer, or reader).

  GET  /repos/{repo_id}/view                          — list committed file metadata
  GET  /repos/{repo_id}/files/{path}?ref={commit_hash} — generate a fresh S3 presigned URL

The view endpoint never embeds file URLs.  The files endpoint generates a fresh
presigned URL on every call (TTL = 300 s) so URLs are never stale or pre-cached.
"""
import uuid
from typing import Annotated, Optional

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from shared.models.workflow import Blob, RepoCommit, RepoHead, RepoTreeEntry
from shared.storage import StorageManager
from app.api import deps

# Module-level singleton — boto3 client creation is expensive (credential
# resolution, connection pool init). Re-using one instance across requests
# is the standard pattern and is thread-safe for read operations.
_storage = StorageManager()

log = structlog.get_logger()
router = APIRouter()

_PRESIGNED_URL_TTL = 300  # seconds


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class ViewFileItem(BaseModel):
    path: str
    content_type: str
    size: int


class ViewResponse(BaseModel):
    repo_id: uuid.UUID
    commit_hash: Optional[str]
    files: list[ViewFileItem]


class FileUrlResponse(BaseModel):
    url: str
    path: str
    content_type: str
    size: int
    expires_in: int


# ---------------------------------------------------------------------------
# Shared helper — resolve commit from repo + optional ref
# ---------------------------------------------------------------------------

def _resolve_commit(
    repo_id: uuid.UUID,
    ref: Optional[str],
    db: Session,
) -> tuple[Optional[RepoCommit], Optional[str]]:
    """
    Return (RepoCommit | None, commit_hash | None).

    - If ref is given, load that specific commit (404 if not found for this repo).
    - If ref is None, use repo_head.latest_commit_hash (None if repo has no commits yet).
    """
    repo = db.exec(select(RepoHead).where(RepoHead.id == repo_id)).first()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found.")

    commit_hash = ref or repo.latest_commit_hash
    if commit_hash is None:
        return None, None

    commit = db.exec(
        select(RepoCommit).where(
            RepoCommit.commit_hash == commit_hash,
            RepoCommit.repo_id == repo_id,
        )
    ).first()
    if commit is None:
        raise HTTPException(status_code=404, detail="Commit not found.")

    return commit, commit_hash


# ---------------------------------------------------------------------------
# GET /v1/repos/{repo_id}/view
# ---------------------------------------------------------------------------

@router.get(
    "/{repo_id}/view",
    response_model=ViewResponse,
    summary="List committed file metadata",
    responses={
        403: {"description": "Not a repo member"},
        404: {"description": "Repository or commit not found"},
    },
    tags=["View Mode"],
)
def get_view(
    repo_id: uuid.UUID,
    ref: Annotated[Optional[str], Query(description="Commit hash to view. Defaults to latest approved commit.")] = None,
    membership: tuple = Depends(deps.require_member),
    db: Session = Depends(deps.get_db),
) -> ViewResponse:
    """
    Return file metadata for the committed tree at `ref` (or the latest commit).

    Never returns file URLs — call `GET /files/{path}?ref=...` to fetch a
    fresh presigned URL for a specific file.
    """
    commit, commit_hash = _resolve_commit(repo_id, ref, db)

    if commit is None:
        return ViewResponse(repo_id=repo_id, commit_hash=None, files=[])

    entries = db.exec(
        select(RepoTreeEntry).where(RepoTreeEntry.tree_id == commit.tree_id)
    ).all()

    # Bulk-load blobs to get accurate size / content_type
    content_hashes = [e.content_hash for e in entries]
    blobs: dict[str, Blob] = {}
    if content_hashes:
        blob_rows = db.exec(
            select(Blob).where(Blob.blob_hash.in_(content_hashes))  # type: ignore[attr-defined]
        ).all()
        blobs = {b.blob_hash: b for b in blob_rows}

    files = [
        ViewFileItem(
            path=entry.name,
            content_type=blobs[entry.content_hash].content_type
            if entry.content_hash in blobs
            else entry.content_type,
            size=blobs[entry.content_hash].size
            if entry.content_hash in blobs
            else entry.size,
        )
        for entry in sorted(entries, key=lambda e: e.name)
    ]

    log.info(
        "view_mode_listed",
        repo_id=str(repo_id),
        commit_hash=commit_hash,
        file_count=len(files),
    )

    return ViewResponse(repo_id=repo_id, commit_hash=commit_hash, files=files)


# ---------------------------------------------------------------------------
# GET /v1/repos/{repo_id}/files/{path}
# ---------------------------------------------------------------------------

@router.get(
    "/{repo_id}/files/{path:path}",
    response_model=FileUrlResponse,
    summary="Generate a fresh presigned URL for a committed file",
    responses={
        403: {"description": "Not a repo member"},
        404: {"description": "Repository, commit, or file not found"},
        500: {"description": "S3 presigned URL generation failed"},
    },
    tags=["View Mode"],
)
def get_file_url(
    repo_id: uuid.UUID,
    path: str,
    ref: Annotated[Optional[str], Query(description="Commit hash to read from. Defaults to latest approved commit.")] = None,
    membership: tuple = Depends(deps.require_member),
    db: Session = Depends(deps.get_db),
) -> FileUrlResponse:
    """
    Generate a fresh S3 presigned URL for the file at `path` in the committed tree.

    The URL expires in 300 seconds and is never cached — every call generates a new one.
    Call this endpoint only when the user explicitly opens a file.
    """
    commit, commit_hash = _resolve_commit(repo_id, ref, db)
    if commit is None:
        raise HTTPException(status_code=404, detail="Repository has no commits yet.")

    entry = db.exec(
        select(RepoTreeEntry).where(
            RepoTreeEntry.tree_id == commit.tree_id,
            RepoTreeEntry.name == path,
        )
    ).first()
    if entry is None:
        raise HTTPException(status_code=404, detail=f"File '{path}' not found in commit {commit_hash}.")

    blob = db.exec(
        select(Blob).where(Blob.blob_hash == entry.content_hash)
    ).first()
    if blob is None:
        raise HTTPException(status_code=404, detail="Blob record not found for this file.")

    try:
        url = _storage.generate_presigned_url(blob.blob_hash, expires_in=_PRESIGNED_URL_TTL)
    except (BotoCoreError, ClientError) as exc:
        log.error("presigned_url_failed", blob_hash=blob.blob_hash, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Failed to generate presigned URL. Check S3 configuration.",
        ) from exc

    log.info(
        "view_mode_file_url_generated",
        repo_id=str(repo_id),
        commit_hash=commit_hash,
        path=path,
    )

    return FileUrlResponse(
        url=url,
        path=path,
        content_type=blob.content_type,
        size=blob.size,
        expires_in=_PRESIGNED_URL_TTL,
    )
