"""
Tests for POST /v1/repos/{repo_id}/drafts/{draft_id}/rebase — Rebase and Continue.

After the author has resolved conflicts in the Conflict Review Screen (editing
draft files via the standard save endpoints), they submit this endpoint to
finalise the rebase.  The server must:

  1. Verify the draft is in 'needs_rebase' status.
  2. Verify the caller owns the draft (or is admin).
  3. Compare the current repo HEAD against the caller's expected_head_commit_hash.
     If the HEAD has moved again since the conflict review began → 409 (head_moved_again).
  4. Wipe the draft's EFS directory.
  5. Download each blob in the HEAD commit's tree from S3 and write it to EFS.
  6. Overlay the author's Category 1A (uncontested) draft files and resolved files
     on top of the HEAD state.
  7. Advance the draft's base_commit_hash to the current HEAD.
  8. Set draft.status = 'editing'.

The 'head_moved_again' 409 guard prevents the author from accidentally
rebasing against an already-stale HEAD: if another commit was approved between
the conflict-review page load and the "Rebase and Continue" click, the frontend
must reload and restart conflict resolution against the new HEAD.

Coverage:
  Happy path        — 200, response shape, DB state transitions
  EFS rebuild       — HEAD blobs downloaded from S3 and written to EFS;
                      draft files overlaid on top; draft-only additions survive;
                      draft version wins when same path exists in both HEAD and draft
  head_moved_again  — 409 when HEAD advanced after conflict review;
                      response includes new HEAD hash for frontend redirect;
                      draft not modified on 409
  Draft state guards — all non-needs_rebase statuses → 400; draft not found → 404
  Ownership / role  — owner/admin allowed; other author → 403; reviewer/reader → 403
  Repo guard        — unknown repo → 404
  Auth              — no token → 401, expired → 401
  Payload           — missing/null/invalid expected_head → 422
"""

import hashlib
import uuid
from pathlib import Path

import pytest
from sqlmodel import select

from shared.constants import DraftStatus
from shared.models.repo import Draft
from shared.models.workflow import RepoHead

_URL = "/v1/repos/{repo_id}/drafts/{draft_id}/rebase"
_OWNER_ID      = "test-user"
_OTHER_USER_ID = "other-user"

# ---------------------------------------------------------------------------
# Deterministic blob content / hashes for EFS rebuild tests
# ---------------------------------------------------------------------------

_CONTENT_HEAD_A   = b"head: content of a.txt"
_CONTENT_HEAD_B   = b"head: content of b.txt"
_CONTENT_DRAFT_A  = b"draft: author's version of a.txt"
_CONTENT_DRAFT_C  = b"draft: new file added by author (category 1A)"

_HASH_HEAD_A  = hashlib.sha256(_CONTENT_HEAD_A).hexdigest()
_HASH_HEAD_B  = hashlib.sha256(_CONTENT_HEAD_B).hexdigest()
_HASH_DRAFT_A = hashlib.sha256(_CONTENT_DRAFT_A).hexdigest()
_HASH_DRAFT_C = hashlib.sha256(_CONTENT_DRAFT_C).hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url(repo_id, draft_id):
    return _URL.format(repo_id=repo_id, draft_id=draft_id)


def _body(expected_head_commit_hash: str):
    return {"expected_head_commit_hash": expected_head_commit_hash}


def _setup_rebase_scenario(
    make_repo, make_commit, make_draft, advance_repo_head,
    *,
    owner_id: str = _OWNER_ID,
    status: DraftStatus = DraftStatus.needs_rebase,
):
    """
    Standard 2-commit scenario with no explicit tree (auto-created by make_commit):
      - base_commit: first approved commit
      - head_commit: second approved commit (advances HEAD past draft's base)
      - draft: based on base_commit, in needs_rebase

    Returns (repo, draft, base_commit, head_commit).
    """
    repo = make_repo(owner_id=owner_id)
    base_commit = make_commit(repo_id=repo.id, owner_id=owner_id, commit_summary="Base")
    head_commit = make_commit(
        repo_id=repo.id,
        owner_id=owner_id,
        parent_commit_hash=base_commit.commit_hash,
        commit_summary="Head",
    )
    advance_repo_head(repo, head_commit.commit_hash)
    draft = make_draft(
        repo_id=repo.id,
        user_id=owner_id,
        status=status,
        base_commit_hash=base_commit.commit_hash,
    )
    return repo, draft, base_commit, head_commit


def _setup_efs_rebase_scenario(
    make_repo, make_tree, make_commit, make_draft, advance_repo_head,
    *,
    head_blobs: dict,
    owner_id: str = _OWNER_ID,
):
    """
    Scenario with an explicit HEAD tree for EFS rebuild tests.

    head_blobs:  {path: blob_hash}  — the tree entries for the HEAD commit.

    The base commit uses an empty tree so every HEAD file reads as 'added_in_head'.

    Returns (repo, draft, base_commit, head_commit).
    """
    repo = make_repo(owner_id=owner_id)

    base_tree   = make_tree({})
    base_commit = make_commit(
        repo_id=repo.id,
        owner_id=owner_id,
        tree_id=base_tree.id,
        commit_summary="Base",
    )

    head_tree   = make_tree(head_blobs)
    head_commit = make_commit(
        repo_id=repo.id,
        owner_id=owner_id,
        parent_commit_hash=base_commit.commit_hash,
        tree_id=head_tree.id,
        commit_summary="Head",
    )
    advance_repo_head(repo, head_commit.commit_hash)

    draft = make_draft(
        repo_id=repo.id,
        user_id=owner_id,
        status=DraftStatus.needs_rebase,
        base_commit_hash=base_commit.commit_hash,
    )
    return repo, draft, base_commit, head_commit


# ---------------------------------------------------------------------------
# Happy path — response and DB transitions
# ---------------------------------------------------------------------------

class TestRebaseContinueSuccess:
    def test_returns_200(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        mock_storage_manager,
    ):
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 200

    def test_response_has_draft_id(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        mock_storage_manager,
    ):
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        data = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        ).json()
        assert "draft_id" in data

    def test_response_has_status(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        mock_storage_manager,
    ):
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        data = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        ).json()
        assert "status" in data

    def test_response_has_base_commit_hash(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        mock_storage_manager,
    ):
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        data = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        ).json()
        assert "base_commit_hash" in data

    def test_response_draft_id_matches(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        mock_storage_manager,
    ):
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        data = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        ).json()
        assert data["draft_id"] == str(draft.id)

    def test_response_status_is_editing(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        mock_storage_manager,
    ):
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        data = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        ).json()
        assert data["status"] == "editing"

    def test_response_base_commit_hash_is_head(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        mock_storage_manager,
    ):
        """After rebase, base_commit_hash must equal the HEAD commit hash."""
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        data = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        ).json()
        assert data["base_commit_hash"] == head_commit.commit_hash

    def test_draft_status_updated_to_editing_in_db(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head, db_session,
        mock_storage_manager,
    ):
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        db_session.expire_all()
        updated = db_session.get(Draft, draft.id)
        assert updated.status == DraftStatus.editing

    def test_draft_base_commit_hash_updated_in_db(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head, db_session,
        mock_storage_manager,
    ):
        """base_commit_hash in DB must be updated to the HEAD commit hash."""
        repo, draft, base_commit, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        assert draft.base_commit_hash == base_commit.commit_hash  # pre-condition

        client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        db_session.expire_all()
        updated = db_session.get(Draft, draft.id)
        assert updated.base_commit_hash == head_commit.commit_hash

    def test_idempotent_only_for_first_call(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        mock_storage_manager,
    ):
        """
        The first call transitions to 'editing'.  A second call finds the draft
        in 'editing' status (not 'needs_rebase') and must return 400 — not idempotent.
        """
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        r1 = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        assert r1.status_code == 200

        r2 = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        assert r2.status_code == 400


# ---------------------------------------------------------------------------
# EFS rebuild — HEAD blobs downloaded from S3 and written; draft overlaid
# ---------------------------------------------------------------------------

class TestRebaseContinueEFSRebuild:
    """
    The rebase endpoint wipes the draft EFS directory, downloads the HEAD
    commit's tree blobs from S3, then overlays the author's draft files on top.
    This eliminates partial state from any previous failed rebase attempt.
    """

    def test_head_blob_written_to_efs(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        mock_storage_manager, tmp_efs,
    ):
        """
        A file in the HEAD commit's tree must be downloaded from S3 and
        written to the draft's EFS directory after a successful rebase.
        """
        repo, draft, _, head_commit = _setup_efs_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            head_blobs={"a.txt": _HASH_HEAD_A},
        )
        mock_storage_manager.download_blob.side_effect = (
            lambda h: _CONTENT_HEAD_A if h == _HASH_HEAD_A else b""
        )

        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 200

        efs_file = (
            Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id) / "a.txt"
        )
        assert efs_file.exists(), "HEAD blob a.txt must be written to EFS after rebase"
        assert efs_file.read_bytes() == _CONTENT_HEAD_A

    def test_all_head_blobs_written_to_efs(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        mock_storage_manager, tmp_efs,
    ):
        """
        Every entry in the HEAD commit's tree is downloaded and written to EFS,
        not just the first one.
        """
        content_map = {_HASH_HEAD_A: _CONTENT_HEAD_A, _HASH_HEAD_B: _CONTENT_HEAD_B}
        repo, draft, _, head_commit = _setup_efs_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            head_blobs={"a.txt": _HASH_HEAD_A, "b.txt": _HASH_HEAD_B},
        )
        mock_storage_manager.download_blob.side_effect = lambda h: content_map.get(h, b"")

        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 200

        draft_root = Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id)
        assert (draft_root / "a.txt").read_bytes() == _CONTENT_HEAD_A
        assert (draft_root / "b.txt").read_bytes() == _CONTENT_HEAD_B

    def test_download_blob_called_for_each_head_tree_entry(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        mock_storage_manager,
    ):
        """
        The endpoint must call StorageManager.download_blob once per HEAD tree
        entry — verifying that the S3 integration is wired up, not bypassed.
        """
        repo, draft, _, head_commit = _setup_efs_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            head_blobs={"a.txt": _HASH_HEAD_A, "b.txt": _HASH_HEAD_B},
        )

        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 200

        called_hashes = {call.args[0] for call in mock_storage_manager.download_blob.call_args_list}
        assert _HASH_HEAD_A in called_hashes
        assert _HASH_HEAD_B in called_hashes

    def test_draft_file_overlaid_on_head_blob(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        mock_storage_manager, seed_file, tmp_efs,
    ):
        """
        When the same path exists in both the HEAD tree and the author's draft,
        the draft's version must win (draft overlay takes precedence over HEAD blob).

        This represents a resolved conflict: the author chose their version.
        """
        # HEAD has a.txt with _CONTENT_HEAD_A
        repo, draft, _, head_commit = _setup_efs_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            head_blobs={"a.txt": _HASH_HEAD_A},
        )
        mock_storage_manager.download_blob.return_value = _CONTENT_HEAD_A

        # Author has a.txt in EFS with their own content (resolved or Category 1A)
        seed_file(_OWNER_ID, str(repo.id), str(draft.id), "a.txt", _CONTENT_DRAFT_A)

        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 200

        efs_file = (
            Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id) / "a.txt"
        )
        assert efs_file.read_bytes() == _CONTENT_DRAFT_A, (
            "Draft version must override HEAD blob when both exist at the same path"
        )

    def test_draft_only_file_survives_efs_rebuild(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        mock_storage_manager, seed_file, tmp_efs,
    ):
        """
        A file the author added to the draft that does not exist in the HEAD
        tree (Category 1A — no conflict) must still be present in EFS after
        the rebuild.  The wipe-and-restore must not silently drop author additions.
        """
        # HEAD tree has only a.txt; draft also has c.txt (author addition)
        repo, draft, _, head_commit = _setup_efs_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            head_blobs={"a.txt": _HASH_HEAD_A},
        )
        mock_storage_manager.download_blob.return_value = _CONTENT_HEAD_A

        seed_file(_OWNER_ID, str(repo.id), str(draft.id), "a.txt", _CONTENT_DRAFT_A)
        seed_file(_OWNER_ID, str(repo.id), str(draft.id), "c.txt", _CONTENT_DRAFT_C)

        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 200

        draft_root = Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id)
        assert (draft_root / "c.txt").exists(), (
            "Draft-only file (Category 1A) must survive the EFS rebuild"
        )
        assert (draft_root / "c.txt").read_bytes() == _CONTENT_DRAFT_C

    def test_empty_head_tree_results_in_empty_efs_except_draft_files(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        mock_storage_manager, seed_file, tmp_efs,
    ):
        """
        When the HEAD commit tree is empty (edge case), no S3 downloads happen
        and EFS only contains the author's draft files.
        """
        repo, draft, _, head_commit = _setup_efs_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            head_blobs={},  # empty HEAD tree
        )
        seed_file(_OWNER_ID, str(repo.id), str(draft.id), "c.txt", _CONTENT_DRAFT_C)

        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 200

        mock_storage_manager.download_blob.assert_not_called()

        draft_root = Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id)
        assert (draft_root / "c.txt").read_bytes() == _CONTENT_DRAFT_C

    def test_head_blob_written_into_subdirectory(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_tree, make_commit, make_draft, advance_repo_head,
        mock_storage_manager, tmp_efs,
    ):
        """
        HEAD tree entries with nested paths (e.g. src/utils.py) must be written
        with the correct directory hierarchy in EFS.
        """
        content_nested = b"content of nested file"
        hash_nested = hashlib.sha256(content_nested).hexdigest()

        repo, draft, _, head_commit = _setup_efs_rebase_scenario(
            make_repo, make_tree, make_commit, make_draft, advance_repo_head,
            head_blobs={"src/utils.py": hash_nested},
        )
        mock_storage_manager.download_blob.return_value = content_nested

        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 200

        nested_file = (
            Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id) / "src" / "utils.py"
        )
        assert nested_file.exists(), "HEAD blob at nested path must be created with correct dirs"
        assert nested_file.read_bytes() == content_nested


# ---------------------------------------------------------------------------
# head_moved_again — HEAD advanced between conflict review and rebase submit
# ---------------------------------------------------------------------------

class TestRebaseContinueHeadMoved:
    def test_head_moved_returns_409(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        """
        If a third commit is approved after the conflict review was loaded
        (HEAD moves again), the rebase endpoint must refuse with 409.
        """
        repo, draft, _, head_at_review = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        # Simulate HEAD moving again after conflict review was opened
        later_commit = make_commit(
            repo_id=repo.id,
            owner_id=_OWNER_ID,
            parent_commit_hash=head_at_review.commit_hash,
            commit_summary="Third commit",
        )
        advance_repo_head(repo, later_commit.commit_hash)

        # Author still passes the old HEAD hash from when they opened the review
        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_at_review.commit_hash),  # stale hash
            headers=auth_headers(),
        )
        assert r.status_code == 409

    def test_head_moved_detail_is_head_moved_again(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        """The 409 response detail must identify the error as 'head_moved_again'."""
        repo, draft, _, head_at_review = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        later_commit = make_commit(
            repo_id=repo.id,
            owner_id=_OWNER_ID,
            parent_commit_hash=head_at_review.commit_hash,
        )
        advance_repo_head(repo, later_commit.commit_hash)

        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_at_review.commit_hash),
            headers=auth_headers(),
        )
        detail = r.json()["detail"]
        # detail may be a string or a dict; either way it must convey 'head_moved_again'
        if isinstance(detail, dict):
            assert detail.get("error") == "head_moved_again"
        else:
            assert "head_moved_again" in str(detail)

    def test_head_moved_response_includes_new_head_hash(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        """
        The 409 body must include the current (new) HEAD hash so the frontend
        can compare and decide whether to reload the conflict-review screen.
        """
        repo, draft, _, head_at_review = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        later_commit = make_commit(
            repo_id=repo.id,
            owner_id=_OWNER_ID,
            parent_commit_hash=head_at_review.commit_hash,
        )
        advance_repo_head(repo, later_commit.commit_hash)

        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_at_review.commit_hash),
            headers=auth_headers(),
        )
        detail = r.json()["detail"]
        assert isinstance(detail, dict), (
            "409 head_moved_again response must be a structured dict, "
            f"got: {detail!r}"
        )
        assert detail.get("new_head_commit_hash") == later_commit.commit_hash

    def test_draft_not_modified_after_head_moved_409(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head, db_session,
    ):
        """
        On a 409, no DB changes must be made: draft status and base_commit_hash
        stay as they were before the request.
        """
        repo, draft, base_commit, head_at_review = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        later_commit = make_commit(
            repo_id=repo.id,
            owner_id=_OWNER_ID,
            parent_commit_hash=head_at_review.commit_hash,
        )
        advance_repo_head(repo, later_commit.commit_hash)

        client.post(
            _url(repo.id, draft.id),
            json=_body(head_at_review.commit_hash),
            headers=auth_headers(),
        )

        db_session.expire_all()
        unchanged = db_session.get(Draft, draft.id)
        assert unchanged.status == DraftStatus.needs_rebase
        assert unchanged.base_commit_hash == base_commit.commit_hash

    def test_head_not_modified_after_head_moved_409(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head, db_session,
    ):
        """
        On a 409, the repo HEAD must not be modified (optimistic-lock version
        must not increment).
        """
        repo, draft, _, head_at_review = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        later_commit = make_commit(
            repo_id=repo.id,
            owner_id=_OWNER_ID,
            parent_commit_hash=head_at_review.commit_hash,
        )
        advance_repo_head(repo, later_commit.commit_hash)

        db_session.expire_all()
        version_before = db_session.get(RepoHead, repo.id).version

        client.post(
            _url(repo.id, draft.id),
            json=_body(head_at_review.commit_hash),
            headers=auth_headers(),
        )

        db_session.expire_all()
        assert db_session.get(RepoHead, repo.id).version == version_before

    def test_efs_not_modified_after_head_moved_409(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        seed_file, tmp_efs,
    ):
        """
        On a 409, the draft EFS directory must not be touched: files seeded
        before the call must still exist and be unmodified.
        """
        repo, draft, _, head_at_review = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        later_commit = make_commit(
            repo_id=repo.id,
            owner_id=_OWNER_ID,
            parent_commit_hash=head_at_review.commit_hash,
        )
        advance_repo_head(repo, later_commit.commit_hash)

        seed_file(_OWNER_ID, str(repo.id), str(draft.id), "existing.py", b"my work")

        client.post(
            _url(repo.id, draft.id),
            json=_body(head_at_review.commit_hash),
            headers=auth_headers(),
        )

        efs_file = (
            Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id) / "existing.py"
        )
        assert efs_file.exists()
        assert efs_file.read_bytes() == b"my work"

    def test_exact_expected_hash_match_succeeds(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        mock_storage_manager,
    ):
        """
        Guard: when expected_head_commit_hash exactly equals the current HEAD,
        the rebase must succeed — this is the normal happy-path confirmation.
        (Regression guard against off-by-one comparison bugs.)
        """
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),  # exact match
            headers=auth_headers(),
        )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Draft state guard — only needs_rebase is allowed
# ---------------------------------------------------------------------------

class TestRebaseContinueDraftStateGuard:
    @pytest.mark.parametrize("bad_status", [
        DraftStatus.editing,
        DraftStatus.committing,
        DraftStatus.pending,
        DraftStatus.approved,
        DraftStatus.rejected,
        DraftStatus.sibling_rejected,
        DraftStatus.reconstructing,
        DraftStatus.deleted,
    ])
    def test_non_needs_rebase_draft_returns_400(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head, bad_status,
    ):
        """
        Rebase is only valid for drafts in needs_rebase.
        Any other status must be rejected with 400.
        """
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=bad_status,
            base_commit_hash=commit.commit_hash,
        )
        r = client.post(
            _url(repo.id, draft.id),
            json=_body(commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 400

    def test_draft_not_found_returns_404(
        self, client, mock_identity_client, auth_headers, make_repo, make_commit, advance_repo_head,
    ):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        r = client.post(
            _url(repo.id, uuid.uuid4()),
            json=_body(commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 404

    def test_draft_from_wrong_repo_returns_404(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        """A draft that belongs to repo_a cannot be rebased via repo_b's URL."""
        repo_a = make_repo(owner_id=_OWNER_ID, repo_name="repo-a")
        repo_b = make_repo(owner_id=_OWNER_ID, repo_name="repo-b")

        commit_a = make_commit(repo_id=repo_a.id, owner_id=_OWNER_ID)
        advance_repo_head(repo_a, commit_a.commit_hash)
        commit_b = make_commit(repo_id=repo_b.id, owner_id=_OWNER_ID)
        advance_repo_head(repo_b, commit_b.commit_hash)

        draft_a = make_draft(
            repo_id=repo_a.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase,
            base_commit_hash=commit_a.commit_hash,
        )
        r = client.post(
            _url(repo_b.id, draft_a.id),  # wrong repo
            json=_body(commit_b.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Ownership and role access
# ---------------------------------------------------------------------------

class TestRebaseContinueOwnershipAndRole:
    def test_draft_owner_can_rebase(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        mock_storage_manager,
    ):
        mock_identity_client.return_value = "author"
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
            owner_id=_OWNER_ID,
        )
        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(user_id=_OWNER_ID),
        )
        assert r.status_code == 200

    def test_admin_non_owner_can_rebase(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
        mock_storage_manager,
    ):
        """Admins must be able to rebase any draft in the repo."""
        mock_identity_client.return_value = "admin"
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
            owner_id=_OWNER_ID,
        )
        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(user_id=_OTHER_USER_ID),  # different user, admin role
        )
        assert r.status_code == 200

    def test_other_author_cannot_rebase_foreign_draft(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        """An author who does not own the draft must be denied."""
        mock_identity_client.return_value = "author"
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
            owner_id=_OWNER_ID,
        )
        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(user_id=_OTHER_USER_ID),  # different author
        )
        assert r.status_code == 403

    def test_reviewer_cannot_rebase(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        mock_identity_client.return_value = "reviewer"
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
            owner_id=_OWNER_ID,
        )
        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(user_id=_OTHER_USER_ID),
        )
        assert r.status_code == 403

    def test_reader_cannot_rebase(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        mock_identity_client.return_value = "reader"
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
            owner_id=_OWNER_ID,
        )
        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(user_id=_OTHER_USER_ID),
        )
        assert r.status_code == 403

    def test_non_member_cannot_rebase(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        mock_identity_client.return_value = None
        repo, draft, _, head_commit = _setup_rebase_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
            owner_id=_OWNER_ID,
        )
        r = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(),
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Repository guard
# ---------------------------------------------------------------------------

class TestRebaseContinueRepositoryGuard:
    def test_unknown_repo_returns_404(
        self, client, mock_identity_client, auth_headers
    ):
        r = client.post(
            _url(uuid.uuid4(), uuid.uuid4()),
            json=_body("a" * 64),
            headers=auth_headers(),
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Authentication guard
# ---------------------------------------------------------------------------

class TestRebaseContinueAuth:
    def test_no_token_returns_401(
        self, client, make_repo, make_commit, make_draft, advance_repo_head
    ):
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase,
            base_commit_hash=commit.commit_hash,
        )
        r = client.post(_url(repo.id, draft.id), json=_body(commit.commit_hash))
        assert r.status_code == 401

    def test_expired_token_returns_401(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head
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
            _url(repo.id, draft.id),
            json=_body(commit.commit_hash),
            headers=auth_headers(expired=True),
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------

class TestRebaseContinuePayloadValidation:
    def test_missing_expected_head_returns_422(
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
        r = client.post(_url(repo.id, draft.id), json={}, headers=auth_headers())
        assert r.status_code == 422

    def test_null_expected_head_returns_422(
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
            _url(repo.id, draft.id),
            json={"expected_head_commit_hash": None},
            headers=auth_headers(),
        )
        assert r.status_code == 422

    def test_empty_string_expected_head_returns_422(
        self, client, mock_identity_client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        """An empty string for expected_head_commit_hash is not a valid SHA-256."""
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID)
        advance_repo_head(repo, commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase,
            base_commit_hash=commit.commit_hash,
        )
        r = client.post(
            _url(repo.id, draft.id),
            json={"expected_head_commit_hash": ""},
            headers=auth_headers(),
        )
        assert r.status_code == 422
