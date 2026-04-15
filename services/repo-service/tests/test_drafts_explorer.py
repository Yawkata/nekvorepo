"""
Tests for GET /v1/repos/{repo_id}/drafts/{draft_id}/explorer — list files in a draft.

Coverage:
  Happy path  — 200, draft_id + files list, empty when no files
  File listing — live files shown, .deleted markers excluded, folder .deleted excludes subtree
  Error cases — 403 wrong owner, 404 draft not found
  Auth        — no token → 401
"""

_URL = "/v1/repos/{repo_id}/drafts/{draft_id}/explorer"
_USER_ID = "test-user"
_OTHER_ID = "other-user"


def _url(repo_id, draft_id):
    return _URL.format(repo_id=repo_id, draft_id=draft_id)


class TestExplorerSuccess:
    def test_returns_200(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.get(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 200

    def test_response_has_draft_id_and_files(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        data = client.get(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).json()
        assert "draft_id" in data
        assert "files" in data

    def test_empty_list_when_no_files(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        data = client.get(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).json()
        assert data["files"] == []

    def test_returns_live_files(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "readme.txt", b"hello world")
        data = client.get(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).json()
        assert len(data["files"]) == 1
        assert data["files"][0]["path"] == "readme.txt"

    def test_file_item_has_path_size_is_binary(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "note.txt", b"text content")
        item = client.get(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).json()["files"][0]
        assert "path" in item
        assert "size" in item
        assert "is_binary" in item

    def test_excludes_deleted_markers(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file, tmp_efs):
        from pathlib import Path
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "file.txt", b"content")
        # Create a .deleted marker — simulates a deletion
        marker = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "file.txt.deleted"
        marker.touch()
        data = client.get(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).json()
        assert data["files"] == []

    def test_folder_deleted_marker_excludes_subtree(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file, tmp_efs
    ):
        from pathlib import Path
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "src/main.py", b"code")
        seed_file(_USER_ID, str(repo.id), str(draft.id), "src/utils.py", b"utils")
        # Mark entire src/ folder as deleted
        marker = Path(tmp_efs) / _USER_ID / str(repo.id) / str(draft.id) / "src.deleted"
        marker.touch()
        data = client.get(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).json()
        # All files under src/ should be excluded
        assert data["files"] == []


class TestExplorerErrors:
    def test_wrong_owner_returns_403(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_OTHER_ID)
        assert client.get(_url(repo.id, draft.id), headers=auth_headers(user_id=_USER_ID)).status_code == 403

    def test_draft_not_found_returns_404(self, client, mock_identity_client, auth_headers, make_repo):
        import uuid
        repo = make_repo()
        assert client.get(_url(repo.id, uuid.uuid4()), headers=auth_headers()).status_code == 404


class TestExplorerAuth:
    def test_no_token_returns_401(self, client, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        assert client.get(_url(repo.id, draft.id)).status_code == 401
