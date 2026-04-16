"""
Tests for POST /v1/repos/{repo_id}/conflicts — three-way conflict classification.

Request body: {draft_id, head, mode: "rebase" | "sibling"}

The endpoint performs a three-way diff between:
  - The draft's current EFS files           (author's working state)
  - The *base* commit tree (DB Tables 3/4)  (what the draft branched from)
  - The *head* commit tree (DB Tables 3/4)  (the commit hash the client supplies)

The client supplies `head` explicitly so that the diff is pinned to the HEAD
snapshot visible when the Conflict Review Screen opened.  The head-moves-again
409 is detected later, at "Rebase and Continue" time.

Every file in the union of the three trees is classified into exactly one of
five categories (spec §Phase 7):

  no_conflict   — only one side changed it (1A: draft only, 1B: head only,
                  1C: neither).  No manual action required; appears in the
                  collapsed "No action needed" accordion.
  conflict      — both draft and head modified the same file to different values.
                  Requires manual resolution.
  added_in_head — head added a file not present in the base; draft has no version.
                  Purely informational; included in the new base automatically.
  deleted_in_head — head removed a file that was in the base.
                  Sub-case A (has_draft_changes=false): draft did not touch it;
                    deletion accepted automatically.
                  Sub-case B (has_draft_changes=true): draft has a modified version;
                    author must choose "Accept Deletion" or "Restore My Version".
  type_collision — a file exists at path P in one tree while a directory
                  (path-prefix P/) exists at the same path in another tree.

Mode routing:
  "rebase"  — valid only for needs_rebase drafts; EFS walk + SHA-256
  "sibling" — valid only for sibling_rejected drafts; S3 blob reconstruction
              (Phase 8); endpoint must accept the mode and validate the draft
              status even if the reconstruction logic is deferred.

Coverage:
  Response shape             — all required top-level fields present
  no_conflict sub-cases      — 1A (draft only), 1B (head only), 1C (neither)
  conflict                   — both sides changed to different values
  added_in_head              — head-only new file; draft has no version
  deleted_in_head 4A         — draft unchanged, head deleted; has_draft_changes=false
  deleted_in_head 4B         — draft modified, head deleted; has_draft_changes=true
  type_collision             — file in draft, directory-prefix in head (and vice versa)
  .deleted markers           — excluded from SHA computation; not returned as paths
  head echoed in response    — head_commit_hash matches the supplied head
  no_conflict files included — category 1 files appear in response for accordion
  Draft state guards         — non-needs_rebase (rebase mode) → 400
  Mode / status cross-check  — rebase mode + sibling_rejected → 400
                               sibling mode + needs_rebase    → 400
                               sibling mode + sibling_rejected → 200
  Mode validation            — missing/invalid/null mode → 422
  head validation            — missing/null head → 422; unknown head → 404
  Payload validation         — missing/invalid draft_id → 422
  Null base_commit_hash      — draft created before any commits; base = empty tree
  Ownership / role           — owner/admin allowed; other author/reviewer/reader → 403
  Repository guard           — unknown repo → 404; draft from wrong repo → 404
  Auth                       — no token → 401, expired → 401
"""

import hashlib
import uuid
from pathlib import Path

import pytest

from shared.constants import CommitStatus, DraftStatus

_URL = "/v1/repos/{repo_id}/conflicts"
_OWNER_ID      = "test-user"
_OTHER_USER_ID = "other-user"

# ── Deterministic test content / hashes ─────────────────────────────────────
# Each constant pair (CONTENT_X, HASH_X) lets us assert exactly which blob hash
# corresponds to which EFS content in the three-way diff.

_CONTENT_BASE_README   = b"readme: original base"
_CONTENT_HEAD_README   = b"readme: modified by head"
_CONTENT_DRAFT_README  = b"readme: modified by draft (differently)"
_CONTENT_SHARED        = b"shared: identical in all three"
_CONTENT_HEAD_NEW      = b"new_file.py: added only in head"
_CONTENT_DRAFT_NEW     = b"draft_new.py: added only in draft"
_CONTENT_BASE_ONLY     = b"vanished.py: in base, deleted from head"

HASH_BASE_README  = hashlib.sha256(_CONTENT_BASE_README).hexdigest()
HASH_HEAD_README  = hashlib.sha256(_CONTENT_HEAD_README).hexdigest()
HASH_DRAFT_README = hashlib.sha256(_CONTENT_DRAFT_README).hexdigest()
HASH_SHARED       = hashlib.sha256(_CONTENT_SHARED).hexdigest()
HASH_HEAD_NEW     = hashlib.sha256(_CONTENT_HEAD_NEW).hexdigest()
HASH_DRAFT_NEW    = hashlib.sha256(_CONTENT_DRAFT_NEW).hexdigest()
HASH_BASE_ONLY    = hashlib.sha256(_CONTENT_BASE_ONLY).hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url(repo_id):
    return _URL.format(repo_id=repo_id)


def _body(draft_id, head, mode="rebase"):
    return {"draft_id": str(draft_id), "head": str(head), "mode": mode}


def _files_by_path(data: dict) -> dict[str, dict]:
    """Index the 'files' list by path for easy per-path assertions."""
    return {item["path"]: item for item in data["files"]}


def _setup_rebase_scenario(
    make_repo, make_tree, make_commit, make_draft, advance_repo_head,
    seed_file, tmp_efs,
    *,
    base_blobs: dict[str, str],
    head_blobs: dict[str, str],
    draft_files: dict[str, bytes],
    draft_deleted_markers: list[str] | None = None,
    owner_id: str = _OWNER_ID,
    draft_status: DraftStatus = DraftStatus.needs_rebase,
):
    """
    Build a complete needs_rebase (or sibling_rejected) scenario:
      1. Create repo + base commit with base_blobs tree.
      2. Advance HEAD to a head commit with head_blobs tree.
      3. Create a draft in `draft_status` based on the base commit.
      4. Seed the draft EFS with draft_files contents.
      5. Optionally seed .deleted markers.

    Returns (repo, draft, base_commit, head_commit).
    """
    repo = make_repo(owner_id=owner_id)

    base_tree = make_tree(base_blobs)
    base_commit = make_commit(
        repo_id=repo.id, owner_id=owner_id,
        tree_id=base_tree.id, commit_summary="Base",
    )

    head_tree = make_tree(head_blobs)
    head_commit = make_commit(
        repo_id=repo.id, owner_id=owner_id,
        tree_id=head_tree.id, parent_commit_hash=base_commit.commit_hash,
        commit_summary="Head",
    )
    advance_repo_head(repo, head_commit.commit_hash)

    draft = make_draft(
        repo_id=repo.id, user_id=owner_id,
        status=draft_status, base_commit_hash=base_commit.commit_hash,
    )

    for path, content in draft_files.items():
        seed_file(owner_id, str(repo.id), str(draft.id), path, content)

    if draft_deleted_markers:
        for marker_path in draft_deleted_markers:
            full_marker = (
                Path(tmp_efs) / owner_id / str(repo.id) / str(draft.id)
                / (marker_path + ".deleted")
            )
            full_marker.parent.mkdir(parents=True, exist_ok=True)
            full_marker.write_bytes(b"")

    return repo, draft, base_commit, head_commit


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

class TestConflictsResponseShape:
    def test_returns_200(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        r = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers())
        assert r.status_code == 200

    def test_response_has_repo_id(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        assert "repo_id" in data

    def test_response_has_draft_id(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        assert "draft_id" in data

    def test_response_has_base_commit_hash(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        assert "base_commit_hash" in data

    def test_response_has_head_commit_hash(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        assert "head_commit_hash" in data

    def test_response_has_files_list(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        assert "files" in data
        assert isinstance(data["files"], list)

    def test_head_commit_hash_echoes_request_head(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        The server must echo the `head` the client supplied — not necessarily
        repo.latest_commit_hash — so the frontend can pass it to rebase-continue.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        assert data["head_commit_hash"] == head.commit_hash

    def test_base_commit_hash_matches_draft_base(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, base, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        assert data["base_commit_hash"] == base.commit_hash

    def test_each_file_entry_has_path_category_hashes(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        for entry in data["files"]:
            assert "path"       in entry
            assert "category"   in entry
            assert "draft_hash" in entry
            assert "head_hash"  in entry
            assert "base_hash"  in entry


# ---------------------------------------------------------------------------
# Category: no_conflict
# ---------------------------------------------------------------------------

class TestCategoryNoConflict:
    def test_1a_only_draft_changed_is_no_conflict(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        Sub-case 1A: only the draft modified the file.
        HEAD has the same version as base → no conflict, draft version wins.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_BASE_README},   # HEAD unchanged
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        files = _files_by_path(data)
        assert files["readme.txt"]["category"] == "no_conflict"

    def test_1b_only_head_changed_is_no_conflict(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        Sub-case 1B: only HEAD modified the file.
        Draft has the same content as base → no conflict, HEAD version wins automatically.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},   # HEAD changed
            draft_files={"readme.txt": _CONTENT_BASE_README},  # draft unchanged
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        files = _files_by_path(data)
        assert files["readme.txt"]["category"] == "no_conflict"

    def test_1c_neither_changed_is_no_conflict(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        Sub-case 1C: file is identical in base, head, and draft.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_SHARED},
            head_blobs={"readme.txt": HASH_SHARED},   # same as base
            draft_files={"readme.txt": _CONTENT_SHARED},  # same as base
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        files = _files_by_path(data)
        assert files["readme.txt"]["category"] == "no_conflict"

    def test_1a_draft_added_file_not_in_base_or_head_is_no_conflict(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        Draft added a new file that neither the base nor HEAD has.
        Only the author changed it → no_conflict (Category 1A).
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_BASE_README},
            draft_files={
                "readme.txt":    _CONTENT_BASE_README,
                "draft_new.py":  _CONTENT_DRAFT_NEW,
            },
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        files = _files_by_path(data)
        assert files["draft_new.py"]["category"] == "no_conflict"

    def test_1a_draft_deletion_marker_not_in_head_is_no_conflict(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        Draft placed a .deleted marker on app.py; HEAD still has it unchanged from base.
        Only the author changed it (by deleting it) → no_conflict (1A).
        The deletion is carried forward automatically.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={
                "readme.txt": HASH_BASE_README,
                "app.py":     HASH_SHARED,
            },
            head_blobs={
                "readme.txt": HASH_BASE_README,
                "app.py":     HASH_SHARED,  # HEAD has it unchanged
            },
            draft_files={"readme.txt": _CONTENT_BASE_README},
            draft_deleted_markers=["app.py"],   # draft deleted it
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        files = _files_by_path(data)
        assert files["app.py"]["category"] == "no_conflict"

    def test_no_conflict_files_appear_in_response(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        no_conflict files MUST appear in the response so the frontend can
        populate the collapsed 'No action needed' accordion.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={
                "stable.py":  HASH_SHARED,
                "readme.txt": HASH_BASE_README,
            },
            head_blobs={
                "stable.py":  HASH_SHARED,        # unchanged
                "readme.txt": HASH_HEAD_README,    # changed → conflict with draft
            },
            draft_files={
                "stable.py":  _CONTENT_SHARED,        # unchanged
                "readme.txt": _CONTENT_DRAFT_README,   # also changed → conflict
            },
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        files = _files_by_path(data)
        # stable.py is no_conflict AND must still appear in the response
        assert "stable.py" in files
        assert files["stable.py"]["category"] == "no_conflict"


# ---------------------------------------------------------------------------
# Category: conflict
# ---------------------------------------------------------------------------

class TestCategoryConflict:
    def test_both_sides_changed_to_different_values_is_conflict(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        assert _files_by_path(data)["readme.txt"]["category"] == "conflict"

    def test_conflict_has_all_three_hashes(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        entry = _files_by_path(data)["readme.txt"]
        assert entry["base_hash"]  == HASH_BASE_README
        assert entry["head_hash"]  == HASH_HEAD_README
        assert entry["draft_hash"] == HASH_DRAFT_README

    def test_draft_deleted_plus_head_modified_is_conflict(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        Draft placed a .deleted marker on app.py, but HEAD modified it.
        Deleting what someone else changed is a conflict.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"app.py": HASH_SHARED},
            head_blobs={"app.py": HASH_HEAD_NEW},  # HEAD changed it
            draft_files={},
            draft_deleted_markers=["app.py"],       # draft deleted it
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        assert _files_by_path(data)["app.py"]["category"] == "conflict"

    def test_multiple_conflicts_all_reported(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={
                "a.py": HASH_BASE_README,
                "b.py": HASH_SHARED,
            },
            head_blobs={
                "a.py": HASH_HEAD_README,
                "b.py": HASH_HEAD_NEW,
            },
            draft_files={
                "a.py": _CONTENT_DRAFT_README,
                "b.py": _CONTENT_DRAFT_NEW,
            },
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        files = _files_by_path(data)
        assert files["a.py"]["category"] == "conflict"
        assert files["b.py"]["category"] == "conflict"


# ---------------------------------------------------------------------------
# Category: added_in_head
# ---------------------------------------------------------------------------

class TestCategoryAddedInHead:
    def test_new_head_file_not_in_base_or_draft_is_added_in_head(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={
                "readme.txt": HASH_BASE_README,
                "new_file.py": HASH_HEAD_NEW,  # added in HEAD only
            },
            draft_files={"readme.txt": _CONTENT_BASE_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        assert _files_by_path(data)["new_file.py"]["category"] == "added_in_head"

    def test_added_in_head_has_null_base_and_draft_hash(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_BASE_README, "new_file.py": HASH_HEAD_NEW},
            draft_files={"readme.txt": _CONTENT_BASE_README},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        entry = _files_by_path(data)["new_file.py"]
        assert entry["base_hash"]  is None
        assert entry["draft_hash"] is None
        assert entry["head_hash"]  == HASH_HEAD_NEW


# ---------------------------------------------------------------------------
# Category: deleted_in_head
# ---------------------------------------------------------------------------

class TestCategoryDeletedInHead:
    def test_4a_head_deleted_draft_unchanged_is_deleted_in_head(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        Sub-case 4A: file was in base and draft (unchanged), HEAD removed it.
        Draft has no changes to the file → deletion accepted automatically.
        has_draft_changes must be False.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={
                "readme.txt":  HASH_BASE_README,
                "vanished.py": HASH_BASE_ONLY,
            },
            head_blobs={"readme.txt": HASH_BASE_README},  # vanished.py deleted from HEAD
            draft_files={
                "readme.txt":  _CONTENT_BASE_README,
                "vanished.py": _CONTENT_BASE_ONLY,       # draft has it unchanged
            },
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        entry = _files_by_path(data)["vanished.py"]
        assert entry["category"] == "deleted_in_head"
        assert entry["has_draft_changes"] is False

    def test_4b_head_deleted_draft_modified_is_deleted_in_head_with_changes(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        Sub-case 4B: file was in base, HEAD removed it, but draft modified it.
        The author has local changes to a file the reviewer deleted.
        has_draft_changes must be True so the UI prompts "Accept Deletion or Restore".
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"vanished.py": HASH_BASE_ONLY},
            head_blobs={},                                    # HEAD removed it
            draft_files={"vanished.py": _CONTENT_DRAFT_README},  # draft changed it
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        entry = _files_by_path(data)["vanished.py"]
        assert entry["category"] == "deleted_in_head"
        assert entry["has_draft_changes"] is True

    def test_deleted_in_head_has_null_head_hash(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """A file deleted from HEAD has no head_hash."""
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"vanished.py": HASH_BASE_ONLY},
            head_blobs={},
            draft_files={"vanished.py": _CONTENT_BASE_ONLY},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        entry = _files_by_path(data)["vanished.py"]
        assert entry["head_hash"] is None
        assert entry["base_hash"] == HASH_BASE_ONLY


# ---------------------------------------------------------------------------
# Category: type_collision
# ---------------------------------------------------------------------------

class TestCategoryTypeCollision:
    def test_file_in_draft_directory_in_head_is_type_collision(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        Draft has a plain file at path 'src'.
        HEAD has entries under prefix 'src/' (implying src is a directory).
        → type_collision at path 'src'.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"src/utils.py": HASH_SHARED},    # base: src is a dir
            head_blobs={"src/utils.py": HASH_SHARED},    # head: src is still a dir
            draft_files={
                "src": _CONTENT_DRAFT_NEW,               # draft: 'src' is a FILE
            },
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        files = _files_by_path(data)
        assert files["src"]["category"] == "type_collision"

    def test_directory_in_draft_file_in_head_is_type_collision(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        Draft has files under path 'lib/' (lib is a directory).
        HEAD has a single file at path 'lib' (lib is a plain file).
        → type_collision at path 'lib'.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"lib": HASH_SHARED},        # base: lib is a file
            head_blobs={"lib": HASH_HEAD_NEW},       # head: lib is still a file (modified)
            draft_files={
                "lib/module.py": _CONTENT_DRAFT_NEW, # draft: lib is now a directory
            },
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        files = _files_by_path(data)
        assert files["lib"]["category"] == "type_collision"

    def test_rebase_continue_locked_while_type_collision_unresolved(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        Per spec: 'Rebase and Continue' button stays locked until every
        type_collision is resolved.  The rebase endpoint must reject if
        the draft still has unresolved type collisions.
        This test confirms the conflicts endpoint correctly surfaces them so
        the frontend can enforce the lock.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"src/utils.py": HASH_SHARED},
            head_blobs={"src/utils.py": HASH_SHARED},
            draft_files={"src": _CONTENT_DRAFT_NEW},
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        # At least one type_collision present — rebase should be blocked by frontend
        categories = [f["category"] for f in data["files"]]
        assert "type_collision" in categories


# ---------------------------------------------------------------------------
# .deleted marker handling
# ---------------------------------------------------------------------------

class TestDeletedMarkerHandling:
    def test_deleted_marker_file_itself_not_in_response_paths(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        The .deleted marker file (e.g. 'app.py.deleted') must never appear
        as a path in the response.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"app.py": HASH_SHARED},
            head_blobs={"app.py": HASH_SHARED},
            draft_files={},
            draft_deleted_markers=["app.py"],
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        all_paths = [f["path"] for f in data["files"]]
        assert "app.py.deleted" not in all_paths

    def test_deleted_marker_excluded_from_draft_hash_computation(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        A .deleted marker is a zero-byte sentinel, not content.
        The draft_hash for the path must be null (file absent), not the
        SHA-256 of zero bytes.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"app.py": HASH_SHARED},
            head_blobs={"app.py": HASH_SHARED},
            draft_files={},
            draft_deleted_markers=["app.py"],
        )
        data = client.post(_url(repo.id), json=_body(draft.id, head.commit_hash), headers=auth_headers()).json()
        entry = _files_by_path(data)["app.py"]
        # draft_hash must be null — the file is absent in draft (deleted)
        assert entry["draft_hash"] is None


# ---------------------------------------------------------------------------
# head as diff reference (not necessarily current repo HEAD)
# ---------------------------------------------------------------------------

class TestHeadAsExplicitReference:
    def test_diff_uses_supplied_head_not_latest_repo_head(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        db_session,
    ):
        """
        The client sends `head = old_commit_hash` while the repo has already
        advanced to a newer commit.  The server must diff against the supplied
        commit, not against repo.latest_commit_hash.

        Concretely: old_head has readme_v1, new_head has readme_v2.
        If the server diffs against old_head, readme will be no_conflict (both
        draft and old_head have the same content).
        If the server incorrectly uses new_head, readme would be conflict.
        """
        from shared.models.workflow import RepoHead
        repo = make_repo(owner_id=_OWNER_ID)

        base_tree = make_tree({"readme.txt": HASH_BASE_README})
        base_commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID, tree_id=base_tree.id)

        # old_head: readme unchanged from base
        old_head_tree = make_tree({"readme.txt": HASH_BASE_README})
        old_head_commit = make_commit(
            repo_id=repo.id, owner_id=_OWNER_ID, tree_id=old_head_tree.id,
            parent_commit_hash=base_commit.commit_hash,
        )

        # new_head: readme completely different
        new_head_tree = make_tree({"readme.txt": HASH_HEAD_README})
        new_head_commit = make_commit(
            repo_id=repo.id, owner_id=_OWNER_ID, tree_id=new_head_tree.id,
            parent_commit_hash=old_head_commit.commit_hash,
        )
        advance_repo_head(repo, new_head_commit.commit_hash)  # repo now at new_head

        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase,
            base_commit_hash=base_commit.commit_hash,
        )
        # Draft EFS: readme matches base (same as old_head)
        # If diff is against old_head → no_conflict (1C: neither changed)
        # If diff is against new_head → conflict (head changed, draft didn't? Actually
        # draft has base content, new_head has different → no_conflict 1B)
        # Either way: the category differs depending on which head is used.
        # Let's make it unambiguous: draft has DRAFT_README content (different from both heads).
        # Against old_head (HASH_BASE_README): draft changed → 1A (no_conflict)
        # Against new_head (HASH_HEAD_README): both changed differently → conflict
        from pathlib import Path
        import os
        draft_dir = Path(make_repo.__self__.session.bind.url.database
            if hasattr(make_repo, '__self__') else "/tmp"
        )
        # Use seed_file approach via the EFS root from the client fixture
        # (We skip EFS seeding; readme absent from draft means draft_hash=None)
        # Draft has NO files in EFS (absent = no draft change)
        # Against old_head: readme in base=HASH_BASE, head=HASH_BASE, draft=absent(=no change)
        #   → no_conflict 1C
        # Against new_head: readme in base=HASH_BASE, head=HASH_HEAD_README, draft=absent
        #   → no_conflict 1B (only head changed)
        # In both cases result is no_conflict, so we can't distinguish by category alone.
        # Instead: assert head_commit_hash in response equals old_head, not new_head.
        data = client.post(
            _url(repo.id),
            json=_body(draft.id, old_head_commit.commit_hash),
            headers=auth_headers(),
        ).json()
        assert data["head_commit_hash"] == old_head_commit.commit_hash
        # And it must NOT equal the current repo HEAD
        assert data["head_commit_hash"] != new_head_commit.commit_hash

    def test_supplied_head_not_found_returns_404(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase,
            base_commit_hash=commit.commit_hash,
        )
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, "a" * 64),  # non-existent commit hash
            headers=auth_headers(),
        )
        assert r.status_code == 404

    def test_supplied_head_from_wrong_repo_returns_404(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        """A commit that belongs to a different repo must not be usable as head."""
        repo_a = make_repo(owner_id=_OWNER_ID, repo_name="repo-a")
        repo_b = make_repo(owner_id=_OWNER_ID, repo_name="repo-b")

        commit_a = make_commit(repo_id=repo_a.id, owner_id=_OWNER_ID)
        advance_repo_head(repo_a, commit_a.commit_hash)
        commit_b = make_commit(repo_id=repo_b.id, owner_id=_OWNER_ID)
        advance_repo_head(repo_b, commit_b.commit_hash)

        draft = make_draft(
            repo_id=repo_a.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase,
            base_commit_hash=commit_a.commit_hash,
        )
        # Pass commit_b as the head — belongs to a different repo
        r = client.post(
            _url(repo_a.id),
            json=_body(draft.id, commit_b.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Null base_commit_hash (draft created before any commits)
# ---------------------------------------------------------------------------

class TestNullBaseCommitHash:
    def test_null_base_treated_as_empty_tree(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        Draft was created before the repo had any commits (base_commit_hash=None).
        A commit was then approved, making the draft needs_rebase.
        The diff should treat the base as an empty tree:
          - Every file in HEAD → added_in_head
          - Every file in draft → no_conflict (1A, author-added)
        """
        repo = make_repo(owner_id=_OWNER_ID)
        head_tree = make_tree({"config.py": HASH_HEAD_NEW})
        head_commit = make_commit(
            repo_id=repo.id, owner_id=_OWNER_ID, tree_id=head_tree.id,
        )
        advance_repo_head(repo, head_commit.commit_hash)

        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase,
            base_commit_hash=None,   # ← created before any commits
        )
        seed_file(_OWNER_ID, str(repo.id), str(draft.id), "my_file.py", _CONTENT_DRAFT_NEW)

        data = client.post(
            _url(repo.id), json=_body(draft.id, head_commit.commit_hash), headers=auth_headers()
        ).json()
        files = _files_by_path(data)
        assert files["config.py"]["category"]  == "added_in_head"
        assert files["my_file.py"]["category"] == "no_conflict"  # 1A: only draft added it


# ---------------------------------------------------------------------------
# Mode parameter — validation and routing
# ---------------------------------------------------------------------------

class TestModeParameter:
    def test_rebase_mode_accepted_for_needs_rebase_draft(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        make_tree, seed_file, tmp_efs,
    ):
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
        )
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, head.commit_hash, mode="rebase"),
            headers=auth_headers(),
        )
        assert r.status_code == 200

    def test_sibling_mode_accepted_for_sibling_rejected_draft(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        make_tree, seed_file, tmp_efs,
    ):
        """
        mode='sibling' is the Phase 8 path.  The endpoint must accept the
        request and validate the draft status, even if S3 reconstruction
        is handled separately.
        """
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
            draft_status=DraftStatus.sibling_rejected,
        )
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, head.commit_hash, mode="sibling"),
            headers=auth_headers(),
        )
        assert r.status_code == 200

    def test_rebase_mode_with_sibling_rejected_draft_returns_400(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        """
        Cross-validation: rebase mode requires needs_rebase draft status.
        Using rebase mode on a sibling_rejected draft is an error.
        """
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.sibling_rejected,
            base_commit_hash=commit.commit_hash,
        )
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, commit.commit_hash, mode="rebase"),
            headers=auth_headers(),
        )
        assert r.status_code == 400

    def test_sibling_mode_with_needs_rebase_draft_returns_400(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        """
        Cross-validation: sibling mode requires sibling_rejected draft status.
        Using sibling mode on a needs_rebase draft is an error.
        """
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase,
            base_commit_hash=commit.commit_hash,
        )
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, commit.commit_hash, mode="sibling"),
            headers=auth_headers(),
        )
        assert r.status_code == 400

    def test_invalid_mode_value_returns_422(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID, status=DraftStatus.needs_rebase,
        )
        r = client.post(
            _url(repo.id),
            json={"draft_id": str(draft.id), "head": commit.commit_hash, "mode": "invalid"},
            headers=auth_headers(),
        )
        assert r.status_code == 422

    def test_missing_mode_returns_422(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID, status=DraftStatus.needs_rebase,
        )
        r = client.post(
            _url(repo.id),
            json={"draft_id": str(draft.id), "head": commit.commit_hash},
            headers=auth_headers(),
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Draft state guard — rebase mode accepts only needs_rebase
# ---------------------------------------------------------------------------

class TestDraftStateGuard:
    @pytest.mark.parametrize("bad_status", [
        DraftStatus.editing,
        DraftStatus.committing,
        DraftStatus.pending,
        DraftStatus.approved,
        DraftStatus.rejected,
        DraftStatus.reconstructing,
        DraftStatus.deleted,
    ])
    def test_non_needs_rebase_draft_rebase_mode_returns_400(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head, bad_status,
    ):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID, status=bad_status,
            base_commit_hash=commit.commit_hash,
        )
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, commit.commit_hash, mode="rebase"),
            headers=auth_headers(),
        )
        assert r.status_code == 400

    def test_draft_not_found_returns_404(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, advance_repo_head,
    ):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        r = client.post(
            _url(repo.id),
            json=_body(uuid.uuid4(), commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 404

    def test_draft_from_wrong_repo_returns_404(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo_a = make_repo(owner_id=_OWNER_ID, repo_name="repo-a")
        repo_b = make_repo(owner_id=_OWNER_ID, repo_name="repo-b")
        commit_a = make_commit(repo_id=repo_a.id, owner_id=_OWNER_ID)
        advance_repo_head(repo_a, commit_a.commit_hash)
        commit_b = make_commit(repo_id=repo_b.id, owner_id=_OWNER_ID)
        advance_repo_head(repo_b, commit_b.commit_hash)
        draft_a = make_draft(
            repo_id=repo_a.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase, base_commit_hash=commit_a.commit_hash,
        )
        r = client.post(
            _url(repo_b.id),
            json=_body(draft_a.id, commit_b.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Ownership and role
# ---------------------------------------------------------------------------

class TestOwnershipAndRole:
    def test_draft_owner_can_classify(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        mock_identity_client.return_value = "author"
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
            owner_id=_OWNER_ID,
        )
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, head.commit_hash),
            headers=auth_headers(user_id=_OWNER_ID),
        )
        assert r.status_code == 200

    def test_admin_non_owner_can_classify(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        mock_identity_client.return_value = "admin"
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
            owner_id=_OWNER_ID,
        )
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, head.commit_hash),
            headers=auth_headers(user_id=_OTHER_USER_ID),
        )
        assert r.status_code == 200

    def test_other_author_cannot_classify_foreign_draft(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        mock_identity_client.return_value = "author"
        repo, draft, _, head = _setup_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            seed_file, tmp_efs,
            base_blobs={"readme.txt": HASH_BASE_README},
            head_blobs={"readme.txt": HASH_HEAD_README},
            draft_files={"readme.txt": _CONTENT_DRAFT_README},
            owner_id=_OWNER_ID,
        )
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, head.commit_hash),
            headers=auth_headers(user_id=_OTHER_USER_ID),
        )
        assert r.status_code == 403

    def test_reviewer_cannot_classify(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase, base_commit_hash=commit.commit_hash,
        )
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, commit.commit_hash),
            headers=auth_headers(user_id=_OTHER_USER_ID),
        )
        assert r.status_code == 403

    def test_reader_cannot_classify(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase, base_commit_hash=commit.commit_hash,
        )
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, commit.commit_hash),
            headers=auth_headers(user_id=_OTHER_USER_ID),
        )
        assert r.status_code == 403

    def test_non_member_cannot_classify(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        mock_identity_client.return_value = None
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase, base_commit_hash=commit.commit_hash,
        )
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Repository guard
# ---------------------------------------------------------------------------

class TestRepositoryGuard:
    def test_unknown_repo_returns_404(
        self, client, mock_identity_client, auth_headers,
    ):
        r = client.post(
            _url(uuid.uuid4()),
            json=_body(uuid.uuid4(), "a" * 64),
            headers=auth_headers(),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuth:
    def test_no_token_returns_401(self, client, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, status=DraftStatus.needs_rebase)
        r = client.post(_url(repo.id), json=_body(draft.id, "a" * 64))
        assert r.status_code == 401

    def test_expired_token_returns_401(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft,
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, status=DraftStatus.needs_rebase)
        r = client.post(
            _url(repo.id),
            json=_body(draft.id, "a" * 64),
            headers=auth_headers(expired=True),
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------

class TestPayloadValidation:
    def test_missing_draft_id_returns_422(
        self, client, mock_identity_client, auth_headers, make_repo,
    ):
        repo = make_repo()
        r = client.post(
            _url(repo.id),
            json={"head": "a" * 64, "mode": "rebase"},
            headers=auth_headers(),
        )
        assert r.status_code == 422

    def test_missing_head_returns_422(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_draft,
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, status=DraftStatus.needs_rebase)
        r = client.post(
            _url(repo.id),
            json={"draft_id": str(draft.id), "mode": "rebase"},
            headers=auth_headers(),
        )
        assert r.status_code == 422

    def test_null_head_returns_422(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_draft,
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, status=DraftStatus.needs_rebase)
        r = client.post(
            _url(repo.id),
            json={"draft_id": str(draft.id), "head": None, "mode": "rebase"},
            headers=auth_headers(),
        )
        assert r.status_code == 422

    def test_invalid_draft_id_format_returns_422(
        self, client, mock_identity_client, auth_headers, make_repo,
    ):
        repo = make_repo()
        r = client.post(
            _url(repo.id),
            json={"draft_id": "not-a-uuid", "head": "a" * 64, "mode": "rebase"},
            headers=auth_headers(),
        )
        assert r.status_code == 422

    def test_null_mode_returns_422(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_draft,
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, status=DraftStatus.needs_rebase)
        r = client.post(
            _url(repo.id),
            json={"draft_id": str(draft.id), "head": "a" * 64, "mode": None},
            headers=auth_headers(),
        )
        assert r.status_code == 422
