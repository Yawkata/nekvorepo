"""
Phase 8 — Sibling-Rejected Rebase: POST /v1/repos/{repo_id}/drafts/{draft_id}/rebase

When another author's commit is approved while a draft was pending review the
draft moves to sibling_rejected.  The owner (or an admin) then resolves any
conflicts through the Conflict Review Screen and calls this endpoint to finalize
the rebase: the draft's EFS directory is wiped, HEAD blobs are restored from S3,
the author's resolved files are overlaid on top, and the draft advances to editing.

The endpoint accepts both 'needs_rebase' (Phase 7) and 'sibling_rejected' (Phase 8)
statuses; the wipe-and-rebuild logic is identical for both.

Coverage
--------
Happy path          — 200, status → editing, base_commit_hash advanced to HEAD
EFS rebuild         — HEAD blobs written; draft overlay survives; conflict resolutions applied
head_moved_again    — 409 when HEAD advances between conflict review and rebase submit
Status guards       — all non-eligible statuses → 400; needs_rebase regression check
Ownership / role    — owner/admin allowed; other-author → 403; reviewer/reader → 403
Admin EFS path      — admin rebasing another user's draft uses draft.user_id for EFS
Resource guards     — unknown repo → 403/404; unknown draft → 404
Auth                — no token → 401; expired → 401
Payload             — missing expected_head → 422; missing required resolutions → 422
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
_OWNER_ID = "owner-user"
_OTHER_USER_ID = "other-user"
_ADMIN_ID = "admin-user"

# ---------------------------------------------------------------------------
# Deterministic blob content / hashes for EFS rebuild tests
# ---------------------------------------------------------------------------

_CONTENT_HEAD_A = b"head: content of alpha.txt"
_CONTENT_HEAD_B = b"head: content of beta.txt"
_CONTENT_DRAFT_C = b"draft: file added by author, draft-only (no_conflict)"
_CONTENT_DRAFT_A = b"draft: author's version of alpha.txt"

_HASH_HEAD_A = hashlib.sha256(_CONTENT_HEAD_A).hexdigest()
_HASH_HEAD_B = hashlib.sha256(_CONTENT_HEAD_B).hexdigest()
_HASH_DRAFT_C = hashlib.sha256(_CONTENT_DRAFT_C).hexdigest()
_HASH_DRAFT_A = hashlib.sha256(_CONTENT_DRAFT_A).hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url(repo_id, draft_id) -> str:
    return _URL.format(repo_id=repo_id, draft_id=draft_id)


def _body(expected_head_commit_hash: str, resolutions: list | None = None) -> dict:
    payload: dict = {"expected_head_commit_hash": expected_head_commit_hash}
    if resolutions is not None:
        payload["resolutions"] = resolutions
    return payload


def _setup_sibling_scenario(
    make_repo, make_commit, make_draft, advance_repo_head,
    *,
    owner_id: str = _OWNER_ID,
):
    """
    Seed a standard sibling-rejected scenario:
      base_commit    — the approved commit the draft was forked from
      sibling_commit — approved by another author, causing sibling rejection
      draft          — based on base_commit, now in sibling_rejected status

    Returns (repo, draft, base_commit, sibling_commit).
    """
    repo = make_repo(owner_id=owner_id)
    base_commit = make_commit(repo_id=repo.id, owner_id=owner_id, commit_summary="Base")
    sibling_commit = make_commit(
        repo_id=repo.id,
        owner_id="sibling-user",
        parent_commit_hash=base_commit.commit_hash,
        commit_summary="Sibling approved commit",
    )
    advance_repo_head(repo, sibling_commit.commit_hash)
    draft = make_draft(
        repo_id=repo.id,
        user_id=owner_id,
        status=DraftStatus.sibling_rejected,
        base_commit_hash=base_commit.commit_hash,
    )
    return repo, draft, base_commit, sibling_commit


# ---------------------------------------------------------------------------
# Happy path — basic (empty HEAD tree, no EFS content)
# ---------------------------------------------------------------------------

class TestSiblingRebaseHappyPath:
    def test_returns_200_and_editing_status(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, draft, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = "author"

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "editing"
        assert data["draft_id"] == str(draft.id)

    def test_base_commit_hash_advanced_to_sibling_head(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head, db_session,
    ):
        repo, draft, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = "author"

        client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_OWNER_ID),
        )

        db_session.refresh(draft)
        assert draft.status == DraftStatus.editing
        assert draft.base_commit_hash == sibling_commit.commit_hash

    def test_response_contains_new_base_commit_hash(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, draft, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = "author"

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.json()["base_commit_hash"] == sibling_commit.commit_hash


# ---------------------------------------------------------------------------
# EFS rebuild — HEAD blobs written; draft files overlaid
# ---------------------------------------------------------------------------

class TestSiblingRebaseEFSRebuild:
    """
    HEAD tree: alpha.txt (HEAD_A), beta.txt (HEAD_B)
    Draft EFS: alpha.txt (DRAFT_A — conflict with HEAD), gamma.txt (draft-only)
    """

    def _setup(
        self, make_repo, make_blob, make_tree, make_commit, make_draft,
        advance_repo_head, seed_file, owner_id: str = _OWNER_ID,
    ):
        repo = make_repo(owner_id=owner_id)
        base_commit = make_commit(repo_id=repo.id, owner_id=owner_id, commit_summary="Base")

        make_blob(blob_hash=_HASH_HEAD_A, size=len(_CONTENT_HEAD_A))
        make_blob(blob_hash=_HASH_HEAD_B, size=len(_CONTENT_HEAD_B))
        tree = make_tree({"alpha.txt": _HASH_HEAD_A, "beta.txt": _HASH_HEAD_B})
        sibling_commit = make_commit(
            repo_id=repo.id,
            owner_id="sibling-user",
            parent_commit_hash=base_commit.commit_hash,
            commit_summary="Sibling with HEAD tree",
            tree_id=tree.id,
        )
        advance_repo_head(repo, sibling_commit.commit_hash)

        draft = make_draft(
            repo_id=repo.id,
            user_id=owner_id,
            status=DraftStatus.sibling_rejected,
            base_commit_hash=base_commit.commit_hash,
        )
        # alpha.txt: author's version (will conflict with HEAD); gamma.txt: draft-only
        seed_file(owner_id, str(repo.id), str(draft.id), "alpha.txt", _CONTENT_DRAFT_A)
        seed_file(owner_id, str(repo.id), str(draft.id), "gamma.txt", _CONTENT_DRAFT_C)

        return repo, draft, sibling_commit

    def test_head_blobs_written_to_efs(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_blob, make_tree, make_commit, make_draft,
        advance_repo_head, seed_file, tmp_efs,
    ):
        repo, draft, sibling_commit = self._setup(
            make_repo, make_blob, make_tree, make_commit, make_draft,
            advance_repo_head, seed_file,
        )
        mock_identity_client.return_value = "author"
        content_map = {_HASH_HEAD_A: _CONTENT_HEAD_A, _HASH_HEAD_B: _CONTENT_HEAD_B}
        mock_storage_manager.download_blob.side_effect = lambda h: content_map[h]

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash, resolutions=[
                {"path": "alpha.txt", "resolution": "keep_mine"},
            ]),
            headers=auth_headers(_OWNER_ID),
        )
        assert resp.status_code == 200

        draft_dir = Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id)
        # beta.txt is added_in_head — must appear from S3
        assert (draft_dir / "beta.txt").read_bytes() == _CONTENT_HEAD_B

    def test_draft_only_file_survives_rebase(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_blob, make_tree, make_commit, make_draft,
        advance_repo_head, seed_file, tmp_efs,
    ):
        repo, draft, sibling_commit = self._setup(
            make_repo, make_blob, make_tree, make_commit, make_draft,
            advance_repo_head, seed_file,
        )
        mock_identity_client.return_value = "author"
        content_map = {_HASH_HEAD_A: _CONTENT_HEAD_A, _HASH_HEAD_B: _CONTENT_HEAD_B}
        mock_storage_manager.download_blob.side_effect = lambda h: content_map[h]

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash, resolutions=[
                {"path": "alpha.txt", "resolution": "keep_mine"},
            ]),
            headers=auth_headers(_OWNER_ID),
        )
        assert resp.status_code == 200

        draft_dir = Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id)
        # gamma.txt is draft-only (no_conflict, has_draft_changes=True) — must survive
        assert (draft_dir / "gamma.txt").read_bytes() == _CONTENT_DRAFT_C

    def test_keep_mine_resolution_writes_author_version(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_blob, make_tree, make_commit, make_draft,
        advance_repo_head, seed_file, tmp_efs,
    ):
        repo, draft, sibling_commit = self._setup(
            make_repo, make_blob, make_tree, make_commit, make_draft,
            advance_repo_head, seed_file,
        )
        mock_identity_client.return_value = "author"
        content_map = {_HASH_HEAD_A: _CONTENT_HEAD_A, _HASH_HEAD_B: _CONTENT_HEAD_B}
        mock_storage_manager.download_blob.side_effect = lambda h: content_map[h]

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash, resolutions=[
                {"path": "alpha.txt", "resolution": "keep_mine"},
            ]),
            headers=auth_headers(_OWNER_ID),
        )
        assert resp.status_code == 200

        draft_dir = Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id)
        assert (draft_dir / "alpha.txt").read_bytes() == _CONTENT_DRAFT_A

    def test_use_theirs_resolution_writes_head_version(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_blob, make_tree, make_commit, make_draft,
        advance_repo_head, seed_file, tmp_efs,
    ):
        repo, draft, sibling_commit = self._setup(
            make_repo, make_blob, make_tree, make_commit, make_draft,
            advance_repo_head, seed_file,
        )
        mock_identity_client.return_value = "author"
        content_map = {_HASH_HEAD_A: _CONTENT_HEAD_A, _HASH_HEAD_B: _CONTENT_HEAD_B}
        mock_storage_manager.download_blob.side_effect = lambda h: content_map[h]

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash, resolutions=[
                {"path": "alpha.txt", "resolution": "use_theirs"},
            ]),
            headers=auth_headers(_OWNER_ID),
        )
        assert resp.status_code == 200

        draft_dir = Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id)
        assert (draft_dir / "alpha.txt").read_bytes() == _CONTENT_HEAD_A

    def test_efs_wiped_before_rebuild(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_blob, make_tree, make_commit, make_draft,
        advance_repo_head, seed_file, seed_deleted_marker, tmp_efs,
    ):
        """
        Deletion markers from the pre-rebase EFS state must not survive after a
        wipe-and-rebuild.  The rebuild writes only live file content (no .deleted
        markers), so any pre-existing markers are eliminated by the wipe.
        """
        repo, draft, sibling_commit = self._setup(
            make_repo, make_blob, make_tree, make_commit, make_draft,
            advance_repo_head, seed_file,
        )
        # Seed a deletion marker that should be wiped and not recreated by the rebuild
        seed_deleted_marker(_OWNER_ID, str(repo.id), str(draft.id), "stale")

        mock_identity_client.return_value = "author"
        content_map = {_HASH_HEAD_A: _CONTENT_HEAD_A, _HASH_HEAD_B: _CONTENT_HEAD_B}
        mock_storage_manager.download_blob.side_effect = lambda h: content_map[h]

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash, resolutions=[
                {"path": "alpha.txt", "resolution": "keep_mine"},
            ]),
            headers=auth_headers(_OWNER_ID),
        )
        assert resp.status_code == 200

        draft_dir = Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id)
        # The .deleted marker was wiped and the rebuild never writes markers back
        assert not (draft_dir / "stale.deleted").exists()


# ---------------------------------------------------------------------------
# HEAD-moved-again guard
# ---------------------------------------------------------------------------

class TestSiblingRebaseHeadMovedAgain:
    def test_returns_409_with_new_head_hash(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, draft, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = "author"

        # A third commit advances HEAD further after the draft was sibling-rejected
        newer_commit = make_commit(
            repo_id=repo.id,
            owner_id="third-user",
            parent_commit_hash=sibling_commit.commit_hash,
            commit_summary="Newer commit",
        )
        advance_repo_head(repo, newer_commit.commit_hash)

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),  # now stale
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "head_moved_again"
        assert detail["new_head_commit_hash"] == newer_commit.commit_hash

    def test_draft_unchanged_on_409(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head, db_session,
    ):
        repo, draft, base_commit, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = "author"

        newer_commit = make_commit(
            repo_id=repo.id, owner_id="third-user",
            parent_commit_hash=sibling_commit.commit_hash,
            commit_summary="Newer commit",
        )
        advance_repo_head(repo, newer_commit.commit_hash)

        client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_OWNER_ID),
        )

        db_session.refresh(draft)
        assert draft.status == DraftStatus.sibling_rejected
        assert draft.base_commit_hash == base_commit.commit_hash


# ---------------------------------------------------------------------------
# Status guards
# ---------------------------------------------------------------------------

class TestSiblingRebaseStatusGuards:
    @pytest.mark.parametrize("bad_status", [
        DraftStatus.editing,
        DraftStatus.committing,
        DraftStatus.pending,
        DraftStatus.approved,
        DraftStatus.rejected,
        DraftStatus.reconstructing,
        DraftStatus.deleted,
    ])
    def test_non_eligible_status_returns_400(
        self, bad_status,
        client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, _, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = "author"

        draft = make_draft(
            repo_id=repo.id,
            user_id=_OWNER_ID,
            status=bad_status,
            base_commit_hash=sibling_commit.commit_hash,
        )
        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.status_code == 400

    def test_needs_rebase_still_accepted(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        """Regression guard: needs_rebase must still be accepted (Phase 7 path)."""
        repo = make_repo(owner_id=_OWNER_ID)
        base_commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID, commit_summary="Base")
        head_commit = make_commit(
            repo_id=repo.id, owner_id=_OWNER_ID,
            parent_commit_hash=base_commit.commit_hash, commit_summary="Head",
        )
        advance_repo_head(repo, head_commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.needs_rebase,
            base_commit_hash=base_commit.commit_hash,
        )
        mock_identity_client.return_value = "author"

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(head_commit.commit_hash),
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "editing"


# ---------------------------------------------------------------------------
# Ownership and role access
# ---------------------------------------------------------------------------

class TestSiblingRebaseOwnership:
    def test_non_owner_author_returns_403(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, draft, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = "author"

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_OTHER_USER_ID),  # not the draft owner
        )

        assert resp.status_code == 403

    def test_reviewer_returns_403(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, draft, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = "reviewer"

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.status_code == 403

    def test_reader_returns_403(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, draft, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = "reader"

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.status_code == 403

    def test_admin_can_rebase_another_users_sibling_draft(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, draft, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head, owner_id=_OWNER_ID,
        )
        mock_identity_client.return_value = "admin"

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_ADMIN_ID),  # different user, admin role
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "editing"

    def test_admin_uses_draft_owner_efs_path(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_blob, make_tree, make_commit, make_draft,
        advance_repo_head, seed_file, tmp_efs,
    ):
        """
        Files must be written under draft.user_id (the owner), not the admin's
        user_id, so the draft owner's subsequent file reads resolve correctly.
        """
        repo = make_repo(owner_id=_OWNER_ID)
        base_commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID, commit_summary="Base")

        make_blob(blob_hash=_HASH_HEAD_A, size=len(_CONTENT_HEAD_A))
        tree = make_tree({"alpha.txt": _HASH_HEAD_A})
        sibling_commit = make_commit(
            repo_id=repo.id, owner_id="sibling-user",
            parent_commit_hash=base_commit.commit_hash,
            commit_summary="Sibling", tree_id=tree.id,
        )
        advance_repo_head(repo, sibling_commit.commit_hash)
        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.sibling_rejected,
            base_commit_hash=base_commit.commit_hash,
        )

        mock_identity_client.return_value = "admin"
        mock_storage_manager.download_blob.return_value = _CONTENT_HEAD_A

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_ADMIN_ID),
        )
        assert resp.status_code == 200

        owner_dir = Path(tmp_efs) / _OWNER_ID / str(repo.id) / str(draft.id)
        admin_dir = Path(tmp_efs) / _ADMIN_ID / str(repo.id) / str(draft.id)
        assert (owner_dir / "alpha.txt").exists()
        assert not admin_dir.exists()


# ---------------------------------------------------------------------------
# Resource guards
# ---------------------------------------------------------------------------

class TestSiblingRebaseResourceGuards:
    def test_unknown_repo_returns_403_or_404(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        _, draft, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = None  # non-member of the random repo

        resp = client.post(
            _url(uuid.uuid4(), draft.id),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.status_code in (403, 404)

    def test_unknown_draft_returns_404(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, _, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = "author"

        resp = client.post(
            _url(repo.id, uuid.uuid4()),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.status_code == 404

    def test_draft_belonging_to_different_repo_returns_404(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo_a, draft_a, _, sibling_a = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head, owner_id=_OWNER_ID,
        )
        repo_b = make_repo(owner_id=_OWNER_ID, repo_name="repo-b")
        mock_identity_client.return_value = "author"

        # draft_a belongs to repo_a — asking via repo_b must 404
        resp = client.post(
            _url(repo_b.id, draft_a.id),
            json=_body(sibling_a.commit_hash),
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestSiblingRebaseAuth:
    def test_no_token_returns_401(
        self, client,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, draft, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),
        )

        assert resp.status_code == 401

    def test_expired_token_returns_401(
        self, client, auth_headers,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, draft, _, sibling_commit = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash),
            headers=auth_headers(_OWNER_ID, expired=True),
        )

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------

class TestSiblingRebasePayload:
    def test_missing_expected_head_returns_422(
        self, client, auth_headers, mock_identity_client,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, draft, _, _ = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = "author"

        resp = client.post(
            _url(repo.id, draft.id),
            json={},
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.status_code == 422

    def test_empty_string_expected_head_returns_422(
        self, client, auth_headers, mock_identity_client,
        make_repo, make_commit, make_draft, advance_repo_head,
    ):
        repo, draft, _, _ = _setup_sibling_scenario(
            make_repo, make_commit, make_draft, advance_repo_head,
        )
        mock_identity_client.return_value = "author"

        resp = client.post(
            _url(repo.id, draft.id),
            json={"expected_head_commit_hash": ""},
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.status_code == 422

    def test_missing_required_conflict_resolution_returns_422(
        self, client, auth_headers, mock_identity_client, mock_storage_manager,
        make_repo, make_blob, make_tree, make_commit, make_draft,
        advance_repo_head, seed_file,
    ):
        """
        When both HEAD and the draft modified the same path differently a resolution
        is mandatory.  Submitting empty resolutions must return 422 with the path listed.
        """
        repo = make_repo(owner_id=_OWNER_ID)
        base_commit = make_commit(repo_id=repo.id, owner_id=_OWNER_ID, commit_summary="Base")

        # Sibling commit introduces alpha.txt at HASH_HEAD_B
        make_blob(blob_hash=_HASH_HEAD_B)
        sibling_tree = make_tree({"alpha.txt": _HASH_HEAD_B})
        sibling_commit = make_commit(
            repo_id=repo.id, owner_id="sibling-user",
            parent_commit_hash=base_commit.commit_hash,
            commit_summary="Sibling", tree_id=sibling_tree.id,
        )
        advance_repo_head(repo, sibling_commit.commit_hash)

        draft = make_draft(
            repo_id=repo.id, user_id=_OWNER_ID,
            status=DraftStatus.sibling_rejected,
            base_commit_hash=base_commit.commit_hash,
        )
        # Draft EFS has a different version of alpha.txt → conflict with HEAD
        seed_file(_OWNER_ID, str(repo.id), str(draft.id), "alpha.txt", _CONTENT_DRAFT_A)

        mock_identity_client.return_value = "author"
        mock_storage_manager.download_blob.return_value = _CONTENT_HEAD_B

        resp = client.post(
            _url(repo.id, draft.id),
            json=_body(sibling_commit.commit_hash, resolutions=[]),
            headers=auth_headers(_OWNER_ID),
        )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "missing_resolutions"
        assert "alpha.txt" in detail["paths"]
