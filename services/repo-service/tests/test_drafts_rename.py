"""
Tests for POST /v1/repos/{repo_id}/drafts/{draft_id}/rename — rename file/folder.

Coverage:
  Happy path  — 200, from_path/to_path/files_moved, old gone, new readable
  Folder      — all files moved, old subtree marked deleted, files_moved count
  Error cases — 400 same path, 400 self-nesting, 404 source not found,
                409 destination exists, 403 wrong owner
  Auth        — no token → 401
"""

from pathlib import Path

from shared.constants import DraftStatus

_URL = "/v1/repos/{repo_id}/drafts/{draft_id}/rename"
_USER_ID = "test-user"
_OTHER_ID = "other-user"


def _url(repo_id, draft_id):
    return _URL.format(repo_id=repo_id, draft_id=draft_id)


class TestRenameSuccess:
    def test_returns_200(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "old.txt", b"content")
        r = client.post(
            _url(repo.id, draft.id),
            json={"from_path": "old.txt", "to_path": "new.txt"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 200

    def test_response_shape(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "a.txt", b"x")
        data = client.post(
            _url(repo.id, draft.id),
            json={"from_path": "a.txt", "to_path": "b.txt"},
            headers=auth_headers(user_id=_USER_ID),
        ).json()
        assert data["from_path"] == "a.txt"
        assert data["to_path"] == "b.txt"
        assert data["files_moved"] == 1

    def test_file_readable_at_new_path(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "src.txt", b"data")
        client.post(
            _url(repo.id, draft.id),
            json={"from_path": "src.txt", "to_path": "dst.txt"},
            headers=auth_headers(user_id=_USER_ID),
        )
        new_path = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "dst.txt"
        assert new_path.exists()
        assert new_path.read_bytes() == b"data"

    def test_old_path_marked_deleted(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "old.txt", b"data")
        client.post(
            _url(repo.id, draft.id),
            json={"from_path": "old.txt", "to_path": "new.txt"},
            headers=auth_headers(user_id=_USER_ID),
        )
        marker = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "old.txt.deleted"
        assert marker.exists()

    def test_folder_rename_moves_all_files(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "lib/a.py", b"a")
        seed_file(_USER_ID, str(repo.id), str(draft.id), "lib/b.py", b"b")
        data = client.post(
            _url(repo.id, draft.id),
            json={"from_path": "lib", "to_path": "packages"},
            headers=auth_headers(user_id=_USER_ID),
        ).json()
        assert data["files_moved"] == 2
        assert (Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "packages/a.py").exists()
        assert (Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "packages/b.py").exists()

    def test_folder_rename_marks_old_folder_deleted(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file, tmp_efs):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "old_dir/file.py", b"code")
        client.post(
            _url(repo.id, draft.id),
            json={"from_path": "old_dir", "to_path": "new_dir"},
            headers=auth_headers(user_id=_USER_ID),
        )
        marker = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "old_dir.deleted"
        assert marker.exists()


class TestRenameErrors:
    def test_same_path_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "file.txt", b"x")
        r = client.post(
            _url(repo.id, draft.id),
            json={"from_path": "file.txt", "to_path": "file.txt"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 400

    def test_self_nesting_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "src/file.py", b"x")
        r = client.post(
            _url(repo.id, draft.id),
            json={"from_path": "src", "to_path": "src/nested"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 400

    def test_source_not_found_returns_404(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _url(repo.id, draft.id),
            json={"from_path": "nonexistent.txt", "to_path": "other.txt"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 404

    def test_destination_exists_returns_409(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "src.txt", b"a")
        seed_file(_USER_ID, str(repo.id), str(draft.id), "dst.txt", b"b")
        r = client.post(
            _url(repo.id, draft.id),
            json={"from_path": "src.txt", "to_path": "dst.txt"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 409

    def test_wrong_owner_returns_403(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_OTHER_ID)
        r = client.post(
            _url(repo.id, draft.id),
            json={"from_path": "a.txt", "to_path": "b.txt"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 403


class TestRenameAuth:
    def test_no_token_returns_401(self, client, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        assert client.post(
            _url(repo.id, draft.id),
            json={"from_path": "a.txt", "to_path": "b.txt"},
        ).status_code == 401
