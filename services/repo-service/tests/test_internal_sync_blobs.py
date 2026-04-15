"""
Tests for POST /v1/internal/sync-blobs.

Coverage:
  Happy path   — 200, blobs dict returned, Blob rows upserted in DB
  Blob upload  — upload_blob called per unique file, skips .deleted markers
  Idempotent   — ON CONFLICT DO NOTHING means duplicate calls don't fail
  Edge cases   — empty draft dir → {}, missing/nonexistent dir → {} (graceful)
  Validation   — missing required fields → 422
  No auth required
"""

import hashlib
import uuid

from sqlmodel import select

from shared.models.repo import Blob

_URL = "/v1/internal/sync-blobs"


class TestSyncBlobsSuccess:
    def test_returns_200(self, client, mock_storage, make_repo, make_draft, seed_file, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="user-1")
        seed_file("user-1", str(repo.id), str(draft.id), "file.txt", b"hello")
        r = client.post(_URL, json={
            "draft_id": str(draft.id),
            "repo_id": str(repo.id),
            "user_id": "user-1",
        })
        assert r.status_code == 200

    def test_returns_blob_map(self, client, mock_storage, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="user-1")
        seed_file("user-1", str(repo.id), str(draft.id), "readme.txt", b"content")
        data = client.post(_URL, json={
            "draft_id": str(draft.id),
            "repo_id": str(repo.id),
            "user_id": "user-1",
        }).json()
        assert "blobs" in data
        expected_hash = hashlib.sha256(b"content").hexdigest()
        assert data["blobs"]["readme.txt"] == expected_hash

    def test_blob_row_created_in_db(self, client, mock_storage, make_repo, make_draft, seed_file, db_session):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="user-1")
        content = b"file content"
        expected_hash = hashlib.sha256(content).hexdigest()
        seed_file("user-1", str(repo.id), str(draft.id), "main.py", content)
        client.post(_URL, json={
            "draft_id": str(draft.id),
            "repo_id": str(repo.id),
            "user_id": "user-1",
        })
        db_session.expire_all()
        blob = db_session.exec(select(Blob).where(Blob.blob_hash == expected_hash)).first()
        assert blob is not None
        assert blob.size == len(content)

    def test_upload_blob_called_per_file(self, client, mock_storage, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="user-1")
        seed_file("user-1", str(repo.id), str(draft.id), "a.txt", b"aaa")
        seed_file("user-1", str(repo.id), str(draft.id), "b.txt", b"bbb")
        client.post(_URL, json={
            "draft_id": str(draft.id),
            "repo_id": str(repo.id),
            "user_id": "user-1",
        })
        assert mock_storage.upload_blob.call_count == 2

    def test_skips_deleted_marker_files(self, client, mock_storage, make_repo, make_draft, seed_file, tmp_efs):
        from pathlib import Path
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="user-1")
        seed_file("user-1", str(repo.id), str(draft.id), "real.txt", b"real")
        # Manually create a .deleted marker (not a real file)
        marker = Path(tmp_efs) / "user-1" / str(repo.id) / str(draft.id) / "ghost.txt.deleted"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        data = client.post(_URL, json={
            "draft_id": str(draft.id),
            "repo_id": str(repo.id),
            "user_id": "user-1",
        }).json()
        # Only the real file should appear in the blob map
        assert "real.txt" in data["blobs"]
        assert "ghost.txt.deleted" not in data["blobs"]
        assert mock_storage.upload_blob.call_count == 1

    def test_empty_draft_returns_empty_dict(self, client, mock_storage, make_repo, make_draft, tmp_efs):
        from pathlib import Path
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="user-1")
        # Create the draft dir but leave it empty
        draft_dir = Path(tmp_efs) / "user-1" / str(repo.id) / str(draft.id)
        draft_dir.mkdir(parents=True, exist_ok=True)
        data = client.post(_URL, json={
            "draft_id": str(draft.id),
            "repo_id": str(repo.id),
            "user_id": "user-1",
        }).json()
        assert data["blobs"] == {}

    def test_missing_draft_dir_returns_empty_dict(self, client, mock_storage, make_repo, make_draft):
        """Non-existent EFS directory — graceful empty response."""
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="user-1")
        # Don't seed any files — directory doesn't exist
        data = client.post(_URL, json={
            "draft_id": str(draft.id),
            "repo_id": str(repo.id),
            "user_id": "user-1",
        }).json()
        assert data["blobs"] == {}

    def test_idempotent_duplicate_call(self, client, mock_storage, make_repo, make_draft, seed_file):
        """Calling sync-blobs twice on the same draft must not fail (ON CONFLICT DO NOTHING)."""
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="user-1")
        seed_file("user-1", str(repo.id), str(draft.id), "file.txt", b"data")
        body = {"draft_id": str(draft.id), "repo_id": str(repo.id), "user_id": "user-1"}
        r1 = client.post(_URL, json=body)
        r2 = client.post(_URL, json=body)
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_multiple_files_all_in_response(self, client, mock_storage, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="user-1")
        seed_file("user-1", str(repo.id), str(draft.id), "src/a.py", b"aaa")
        seed_file("user-1", str(repo.id), str(draft.id), "src/b.py", b"bbb")
        seed_file("user-1", str(repo.id), str(draft.id), "README.md", b"readme")
        data = client.post(_URL, json={
            "draft_id": str(draft.id),
            "repo_id": str(repo.id),
            "user_id": "user-1",
        }).json()
        assert len(data["blobs"]) == 3

    def test_no_auth_required(self, client, mock_storage, make_repo, make_draft):
        """Internal endpoints don't require a Passport JWT."""
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="user-1")
        r = client.post(_URL, json={
            "draft_id": str(draft.id),
            "repo_id": str(repo.id),
            "user_id": "user-1",
        })
        # Should not be 401
        assert r.status_code != 401


class TestSyncBlobsValidation:
    def test_missing_draft_id_returns_422(self, client, mock_storage):
        r = client.post(_URL, json={"repo_id": str(uuid.uuid4()), "user_id": "u"})
        assert r.status_code == 422

    def test_missing_repo_id_returns_422(self, client, mock_storage):
        r = client.post(_URL, json={"draft_id": str(uuid.uuid4()), "user_id": "u"})
        assert r.status_code == 422

    def test_missing_user_id_returns_422(self, client, mock_storage):
        r = client.post(_URL, json={"draft_id": str(uuid.uuid4()), "repo_id": str(uuid.uuid4())})
        assert r.status_code == 422
