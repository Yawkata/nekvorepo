"""
EFS draft storage service.

Directory layout on the mount point:
  {drafts_root}/{user_id}/{repo_id}/{draft_id}/
      <files and directories as authored>
      src/main.py
      src/main.py.deleted    ← deletion marker for src/main.py
      docs/                  ← a real folder
      docs.deleted           ← marks the entire docs/ folder as deleted

Deletion markers:
  - A zero-byte file at "{original_path}.deleted" marks that path as deleted.
  - If the original path is a folder, the marker covers the entire subtree.
  - Marker files (.deleted) are never returned to callers.
  - Writing a file removes any stale deletion marker for that path.
  - The .deleted extension is reserved; callers may not create files ending in it.

Local vs production:
  - Local (docker-compose): host directory bind-mounted at /mnt/efs/drafts.
  - Production (EKS):       AWS EFS volume mounted at /mnt/efs/drafts via CSI driver.
  The service code is identical for both environments.
"""
import shutil
from dataclasses import dataclass
from pathlib import Path

_DELETED_EXT = ".deleted"
_LARGE_FILE_BYTES = 1 * 1024 * 1024  # 1 MB — matches Phase 4 spec warning threshold


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExplorerFile:
    path: str        # Relative path from draft root, forward slashes
    size: int        # File size in bytes
    is_binary: bool  # True when content is not valid UTF-8


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_binary(sample: bytes) -> bool:
    """Detect binary content by scanning up to 8 KB for null bytes or invalid UTF-8."""
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def _validate_rel_path(path: str) -> None:
    """
    Reject paths that are empty, absolute, or attempt directory traversal.
    Raises ValueError with a caller-safe message.
    """
    if not path or not path.strip():
        raise ValueError("File path must not be empty.")
    if path.startswith("/") or path.startswith("\\"):
        raise ValueError("File path must be relative, not absolute.")
    # Normalise and check for traversal components
    import os
    normalised = os.path.normpath(path.replace("\\", "/"))
    if normalised.startswith(".."):
        raise ValueError("File path must not escape the draft directory.")


def _resolve_safe(draft_dir: Path, rel_path: str) -> Path:
    """
    Normalise a relative path and verify it stays inside draft_dir.
    Uses Path.is_relative_to() (Python 3.9+) which handles platform-specific
    path separators correctly on both Linux (EKS) and Windows (local dev).
    Raises ValueError on traversal attempts.
    """
    _validate_rel_path(rel_path)
    resolved_dir = draft_dir.resolve()
    target = (draft_dir / rel_path).resolve()
    if target != resolved_dir and not target.is_relative_to(resolved_dir):
        raise ValueError("File path must not escape the draft directory.")
    return target


def _is_path_deleted(rel_path: str, deleted: set[str]) -> bool:
    """
    Return True if rel_path or any ancestor segment appears in the deleted set.
    Example: if "docs" is in deleted, then "docs/api.md" returns True.
    """
    if rel_path in deleted:
        return True
    parts = rel_path.split("/")
    for i in range(1, len(parts)):
        if "/".join(parts[:i]) in deleted:
            return True
    return False


# ---------------------------------------------------------------------------
# EFS service
# ---------------------------------------------------------------------------

class EFSService:
    """
    Encapsulates all EFS draft file operations.
    Instantiated once at startup; injected into endpoints via FastAPI dependency.
    """

    def __init__(self, drafts_root: str) -> None:
        self._root = Path(drafts_root)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def draft_dir(self, user_id: str, repo_id: str, draft_id: str) -> Path:
        return self._root / user_id / str(repo_id) / str(draft_id)

    # ------------------------------------------------------------------
    # Directory lifecycle
    # ------------------------------------------------------------------

    def create_dir(self, user_id: str, repo_id: str, draft_id: str) -> None:
        """
        Create an empty draft directory.
        Raises OSError if the directory already exists or the parent is not writable.
        """
        path = self.draft_dir(user_id, repo_id, draft_id)
        path.mkdir(parents=True, exist_ok=False)

    def delete_dir(self, user_id: str, repo_id: str, draft_id: str) -> None:
        """
        Delete a draft directory and all its contents.
        Idempotent — a missing directory is treated as success (matches SQS cleanup
        consumer semantics: the directory may have been cleaned up already).
        """
        path = self.draft_dir(user_id, repo_id, draft_id)
        if path.exists():
            shutil.rmtree(path)

    def copy_dir(
        self,
        src_user_id: str,
        src_repo_id: str,
        src_draft_id: str,
        dst_user_id: str,
        dst_repo_id: str,
        dst_draft_id: str,
    ) -> None:
        """
        Copy an entire draft directory tree into a new draft directory.

        If the source directory does not exist (e.g. the EFS dir was wiped after
        approval) an empty destination directory is created instead so the caller
        always gets a valid, usable draft directory back.

        Raises OSError if the destination already exists.
        """
        src = self.draft_dir(src_user_id, src_repo_id, src_draft_id)
        dst = self.draft_dir(dst_user_id, dst_repo_id, dst_draft_id)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=False)

    def dir_exists(self, user_id: str, repo_id: str, draft_id: str) -> bool:
        return self.draft_dir(user_id, repo_id, draft_id).is_dir()

    # ------------------------------------------------------------------
    # Explorer
    # ------------------------------------------------------------------

    def list_files(self, user_id: str, repo_id: str, draft_id: str) -> list[ExplorerFile]:
        """
        Walk the draft directory and return the resolved file tree.

        Resolution rules:
          1. Collect every .deleted marker and record what it covers.
          2. Walk all real files; skip those covered by a marker and the markers themselves.
          3. Return only live (non-deleted) files with size and binary flag.
        """
        draft = self.draft_dir(user_id, repo_id, draft_id)
        if not draft.is_dir():
            return []

        # Pass 1 — build the set of deleted paths (files and folders)
        deleted: set[str] = set()
        for p in draft.rglob("*"):
            if p.is_file():
                rel = p.relative_to(draft).as_posix()
                if rel.endswith(_DELETED_EXT):
                    # Strip the marker extension to get the original path
                    deleted.add(rel[: -len(_DELETED_EXT)])

        # Pass 2 — collect live files
        result: list[ExplorerFile] = []
        for p in sorted(draft.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(draft).as_posix()
            if rel.endswith(_DELETED_EXT):
                continue  # skip marker files themselves
            if _is_path_deleted(rel, deleted):
                continue  # covered by a deletion marker
            stat = p.stat()
            with p.open("rb") as fh:
                sample = fh.read(8192)
            result.append(ExplorerFile(
                path=rel,
                size=stat.st_size,
                is_binary=_is_binary(sample),
            ))

        return result

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def read_file(self, user_id: str, repo_id: str, draft_id: str, rel_path: str) -> bytes:
        """
        Return raw bytes for a file.  Raises FileNotFoundError if the path does not
        exist in EFS; callers map this to HTTP 404.
        """
        draft = self.draft_dir(user_id, repo_id, draft_id)
        target = _resolve_safe(draft, rel_path)
        if not target.is_file():
            raise FileNotFoundError(rel_path)
        return target.read_bytes()

    def write_file(
        self,
        user_id: str,
        repo_id: str,
        draft_id: str,
        rel_path: str,
        content: bytes,
    ) -> int:
        """
        Write content to a file, creating parent directories as needed.
        Any stale deletion marker for the same path is removed so the file
        appears as live in the next explorer call.
        Returns the number of bytes written.
        """
        draft = self.draft_dir(user_id, repo_id, draft_id)
        target = _resolve_safe(draft, rel_path)

        # If any ancestor path component exists as a plain file, physically
        # remove it before calling mkdir.  This occurs when a file (e.g. "lib")
        # was previously marked deleted via mark_deleted() and the author now
        # writes under it as a directory (e.g. "lib/core.py").  The .deleted
        # marker already records the logical deletion; the raw bytes can go.
        check = draft
        for part in target.parent.relative_to(draft).parts:
            check = check / part
            if check.is_file():
                check.unlink()
                break  # mkdir(parents=True) will create the remaining dirs

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        # Remove the direct deletion marker for this path, if one exists.
        marker = Path(str(target) + _DELETED_EXT)
        if marker.exists():
            marker.unlink()
        # Remove any ancestor deletion markers so that the new file is not
        # suppressed by a parent-folder marker (e.g. writing "lib/core.py"
        # after "lib" was marked deleted must remove "lib.deleted").
        ancestor = draft
        for part in target.relative_to(draft).parts[:-1]:
            ancestor = ancestor / part
            ancestor_marker = Path(str(ancestor) + _DELETED_EXT)
            if ancestor_marker.exists():
                ancestor_marker.unlink()
        return len(content)

    def mark_deleted(
        self, user_id: str, repo_id: str, draft_id: str, rel_path: str
    ) -> None:
        """
        Create a zero-byte .deleted marker for a file or folder path.
        The original bytes are not removed from EFS; the marker is the authoritative
        signal.  This keeps the design append-only on the data plane — only the
        sync-blobs step (Phase 5) needs to walk and interpret the markers.
        """
        draft = self.draft_dir(user_id, repo_id, draft_id)
        marker = _resolve_safe(draft, rel_path + _DELETED_EXT)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"")

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def is_large(size: int) -> bool:
        """True when file size exceeds the 1 MB large-file warning threshold."""
        return size > _LARGE_FILE_BYTES
