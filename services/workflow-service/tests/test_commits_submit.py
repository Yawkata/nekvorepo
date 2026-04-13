"""
Tests for POST /v1/repos/{repo_id}/commits — submit draft for review.

Saga steps exercised:
  1. Validate draft ownership and status
  2. Set draft.status = committing (commit-lock)
  3. Call repo_client.sync_blobs → blob map
  4. Build RepoTreeRoot + RepoTreeEntry rows
  5. Compute changes_summary vs parent commit
  6. Insert RepoCommit row
  7. Set draft.status = pending, draft.commit_hash = commit_hash

Coverage:
  Happy path        — 201, response shape, DB state, tree rows
  Tree assertions   — RepoTreeRoot/Entry created, nested dirs, deduplication
  Role checks       — admin/author allowed; reviewer/reader/non-member → 403
  Draft validation  — wrong owner, bad status variants
  Payload           — missing/blank/oversized fields
  External failures — sync_blobs 502, empty blobs, no-change diff
  Auth              — no token, expired, wrong secret
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlmodel import select

from shared.constants import CommitStatus, DraftStatus, NodeType
from shared.models.workflow import RepoCommit, RepoHead, RepoTreeEntry, RepoTreeRoot
from shared.models.repo import Draft

_URL = "/v1/repos/{repo_id}/commits"
_BLOB_HASH_A = "a" * 64
_BLOB_HASH_B = "b" * 64
_BLOB_HASH_C = "c" * 64


def _url(repo_id):
    return _URL.format(repo_id=repo_id)


def _body(draft_id, summary="Add readme", description=None):
    payload = {"draft_id": str(draft_id), "commit_summary": summary}
    if description is not None:
        payload["commit_description"] = description
    return payload


# ── Happy path ────────────────────────────────────────────────────────────────

class TestSubmitCommitSuccess:
    def test_returns_201(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        assert r.status_code == 201

    def test_response_has_commit_hash(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        assert "commit_hash" in r.json()

    def test_response_status_is_pending(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        assert r.json()["status"] == "pending"

    def test_response_shape(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id, description="Extended body"), headers=auth_headers())
        data = r.json()
        for field in ("commit_hash", "status", "commit_summary", "commit_description",
                      "changes_summary", "owner_id", "timestamp", "draft_id"):
            assert field in data

    def test_commit_summary_matches(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id, summary="My feature"), headers=auth_headers())
        assert r.json()["commit_summary"] == "My feature"

    def test_commit_summary_stripped(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id, summary="  trimmed  "), headers=auth_headers())
        assert r.json()["commit_summary"] == "trimmed"

    def test_changes_summary_populated(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        assert r.json()["changes_summary"] is not None

    def test_first_commit_changes_summary(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        assert "added" in r.json()["changes_summary"]

    def test_repo_commit_persisted(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, db_session):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        commit_hash = r.json()["commit_hash"]
        commit = db_session.exec(select(RepoCommit).where(RepoCommit.commit_hash == commit_hash)).first()
        assert commit is not None
        assert commit.status == CommitStatus.pending

    def test_draft_status_updated_to_pending(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, db_session):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        db_session.expire_all()
        updated = db_session.get(Draft, draft.id)
        assert updated.status == DraftStatus.pending

    def test_draft_commit_hash_set(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, db_session):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        db_session.expire_all()
        updated = db_session.get(Draft, draft.id)
        assert updated.commit_hash == r.json()["commit_hash"]

    def test_needs_rebase_draft_can_be_submitted(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, status=DraftStatus.needs_rebase)
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        assert r.status_code == 201


# ── Tree assertions ───────────────────────────────────────────────────────────

class TestSubmitCommitTree:
    def test_tree_root_created(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, db_session):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        roots = db_session.exec(select(RepoTreeRoot)).all()
        assert len(roots) == 1
        assert len(roots[0].tree_hash) == 64

    def test_tree_entry_created_for_blob(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, db_session):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        # Default mock returns {"readme.txt": _BLOB_HASH_A}
        client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        entries = db_session.exec(select(RepoTreeEntry)).all()
        assert len(entries) == 1
        assert entries[0].name == "readme.txt"
        assert entries[0].type == NodeType.blob
        assert entries[0].content_hash == _BLOB_HASH_A

    def test_nested_path_creates_subtree_root(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, db_session):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        # src/main.py → produces root tree + src/ subtree
        mock_sync, _ = mock_repo_client
        mock_sync.return_value = {"src/main.py": _BLOB_HASH_A}
        client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        roots = db_session.exec(select(RepoTreeRoot)).all()
        assert len(roots) == 2  # root + src/ subtree

    def test_nested_path_root_has_tree_entry(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, db_session):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        mock_sync, _ = mock_repo_client
        mock_sync.return_value = {"src/main.py": _BLOB_HASH_A}
        client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        # Root tree should have one entry of type 'tree' pointing at the src/ subtree
        entries = db_session.exec(select(RepoTreeEntry)).all()
        tree_entries = [e for e in entries if e.type == NodeType.tree]
        blob_entries = [e for e in entries if e.type == NodeType.blob]
        assert len(tree_entries) == 1
        assert tree_entries[0].name == "src"
        assert len(blob_entries) == 1
        assert blob_entries[0].name == "main.py"

    def test_identical_subtree_is_deduplicated(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, db_session):
        """Two repos with the same src/ content share one RepoTreeRoot for that subtree."""
        mock_sync, _ = mock_repo_client
        mock_sync.return_value = {"src/helper.py": _BLOB_HASH_A}

        repo_a = make_repo(repo_name="repo-a")
        draft_a = make_draft(repo_id=repo_a.id)
        client.post(_url(repo_a.id), json=_body(draft_a.id), headers=auth_headers())

        repo_b = make_repo(repo_name="repo-b", owner_id="other-user")
        draft_b = make_draft(repo_id=repo_b.id, user_id="other-user")
        client.post(_url(repo_b.id), json=_body(draft_b.id), headers=auth_headers(user_id="other-user"))

        # Both repos have the same blob map → same tree hashes → 2 unique roots, not 4
        roots = db_session.exec(select(RepoTreeRoot)).all()
        assert len(roots) == 2  # one root-level tree + one src/ subtree, shared


# ── Role checks ───────────────────────────────────────────────────────────────

class TestSubmitCommitRoles:
    def test_admin_can_submit(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        assert client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers()).status_code == 201

    def test_author_can_submit(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        assert client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers()).status_code == 201

    def test_reviewer_cannot_submit(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        assert client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers()).status_code == 403

    def test_reader_cannot_submit(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        assert client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers()).status_code == 403

    def test_non_member_cannot_submit(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = None
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        assert client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers()).status_code == 403


# ── Draft validation ──────────────────────────────────────────────────────────

class TestSubmitCommitDraftValidation:
    def test_draft_not_found_returns_404(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo):
        repo = make_repo()
        r = client.post(_url(repo.id), json=_body(uuid.uuid4()), headers=auth_headers())
        assert r.status_code == 404

    def test_wrong_owner_returns_403(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="other-user")
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        assert r.status_code == 403

    def test_committing_status_returns_409(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, status=DraftStatus.committing)
        assert client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers()).status_code == 409

    def test_pending_status_returns_409(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, status=DraftStatus.pending)
        assert client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers()).status_code == 409

    def test_approved_status_returns_409(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, status=DraftStatus.approved)
        assert client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers()).status_code == 409

    def test_rejected_status_returns_409(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, status=DraftStatus.rejected)
        assert client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers()).status_code == 409


# ── Payload validation ────────────────────────────────────────────────────────

class TestSubmitCommitPayloadValidation:
    def test_missing_draft_id_returns_422(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo):
        repo = make_repo()
        r = client.post(_url(repo.id), json={"commit_summary": "x"}, headers=auth_headers())
        assert r.status_code == 422

    def test_missing_commit_summary_returns_422(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json={"draft_id": str(draft.id)}, headers=auth_headers())
        assert r.status_code == 422

    def test_blank_commit_summary_returns_422(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id, summary="   "), headers=auth_headers())
        assert r.status_code == 422

    def test_commit_summary_too_long_returns_422(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id, summary="x" * 201), headers=auth_headers())
        assert r.status_code == 422

    def test_commit_description_too_long_returns_422(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id, description="x" * 5001), headers=auth_headers())
        assert r.status_code == 422

    def test_invalid_uuid_draft_id_returns_422(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo):
        repo = make_repo()
        r = client.post(_url(repo.id), json={"draft_id": "not-a-uuid", "commit_summary": "x"}, headers=auth_headers())
        assert r.status_code == 422


# ── External service failures ─────────────────────────────────────────────────

class TestSubmitCommitExternalFailures:
    def test_sync_blobs_502_returns_502(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        mock_sync, _ = mock_repo_client
        mock_sync.side_effect = HTTPException(status_code=502, detail="repo service down")
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        assert r.status_code == 502

    def test_sync_blobs_502_reverts_draft_to_editing(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, db_session):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        mock_sync, _ = mock_repo_client
        mock_sync.side_effect = HTTPException(status_code=502, detail="repo service down")
        client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        db_session.expire_all()
        assert db_session.get(Draft, draft.id).status == DraftStatus.editing

    def test_empty_blob_map_returns_422(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        mock_sync, _ = mock_repo_client
        mock_sync.return_value = {}
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        assert r.status_code == 422

    def test_empty_blob_map_reverts_draft_to_editing(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, db_session):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        mock_sync, _ = mock_repo_client
        mock_sync.return_value = {}
        client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers())
        db_session.expire_all()
        assert db_session.get(Draft, draft.id).status == DraftStatus.editing

    def test_no_changes_vs_parent_returns_422(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, make_commit, db_session):
        """Submitting the exact same files as the parent commit is rejected."""
        repo = make_repo(latest_commit_hash=None)
        # First commit: one file
        draft_a = make_draft(repo_id=repo.id)
        mock_sync, _ = mock_repo_client
        mock_sync.return_value = {"file.txt": _BLOB_HASH_A}
        r = client.post(_url(repo.id), json=_body(draft_a.id), headers=auth_headers())
        assert r.status_code == 201
        first_commit_hash = r.json()["commit_hash"]

        # Manually advance repo head to simulate approval
        repo_head = db_session.get(RepoHead, repo.id)
        repo_head.latest_commit_hash = first_commit_hash
        db_session.commit()

        # Second draft: identical files → "No changes"
        draft_b = make_draft(repo_id=repo.id, status=DraftStatus.editing)
        mock_sync.return_value = {"file.txt": _BLOB_HASH_A}
        r2 = client.post(_url(repo.id), json=_body(draft_b.id), headers=auth_headers())
        assert r2.status_code == 422

    def test_no_changes_reverts_draft_to_editing(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft, make_commit, db_session):
        repo = make_repo(latest_commit_hash=None)
        draft_a = make_draft(repo_id=repo.id)
        mock_sync, _ = mock_repo_client
        mock_sync.return_value = {"file.txt": _BLOB_HASH_A}
        r = client.post(_url(repo.id), json=_body(draft_a.id), headers=auth_headers())
        first_commit_hash = r.json()["commit_hash"]

        repo_head = db_session.get(RepoHead, repo.id)
        repo_head.latest_commit_hash = first_commit_hash
        db_session.commit()

        draft_b = make_draft(repo_id=repo.id, status=DraftStatus.editing)
        mock_sync.return_value = {"file.txt": _BLOB_HASH_A}
        client.post(_url(repo.id), json=_body(draft_b.id), headers=auth_headers())
        db_session.expire_all()
        assert db_session.get(Draft, draft_b.id).status == DraftStatus.editing


# ── Auth ──────────────────────────────────────────────────────────────────────

class TestSubmitCommitAuth:
    def test_no_token_returns_401(self, client, make_repo):
        repo = make_repo()
        assert client.post(_url(repo.id), json=_body(uuid.uuid4())).status_code == 401

    def test_expired_token_returns_401(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers(expired=True))
        assert r.status_code == 401

    def test_wrong_secret_returns_401(self, client, mock_identity_client, mock_repo_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id)
        r = client.post(_url(repo.id), json=_body(draft.id), headers=auth_headers(wrong_secret=True))
        assert r.status_code == 401
