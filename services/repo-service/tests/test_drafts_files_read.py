"""
Tests for GET /v1/repos/{repo_id}/drafts/{draft_id}/files/{path} — read a file.

Coverage:
  Happy path  — raw bytes, correct Content-Type, large file warning header
  Error cases — 404 file not found, 400 .deleted path, 403 wrong owner
  Auth        — no token → 401
"""

_URL = "/v1/repos/{repo_id}/drafts/{draft_id}/files/{path}"
_USER_ID = "test-user"
_OTHER_ID = "other-user"


def _url(repo_id, draft_id, path):
    return _URL.format(repo_id=repo_id, draft_id=draft_id, path=path)


class TestReadFileSuccess:
    def test_returns_200(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "hello.txt", b"hello world")
        r = client.get(_url(repo.id, draft.id, "hello.txt"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 200

    def test_returns_correct_content(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "data.txt", b"test content")
        r = client.get(_url(repo.id, draft.id, "data.txt"), headers=auth_headers(user_id=_USER_ID))
        assert r.content == b"test content"

    def test_text_file_content_type(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "text.txt", b"readable text")
        r = client.get(_url(repo.id, draft.id, "text.txt"), headers=auth_headers(user_id=_USER_ID))
        assert "text/plain" in r.headers["content-type"]

    def test_binary_file_content_type(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "img.bin", b"\x00\x01\x02\x03")
        r = client.get(_url(repo.id, draft.id, "img.bin"), headers=auth_headers(user_id=_USER_ID))
        assert "application/octet-stream" in r.headers["content-type"]

    def test_large_file_warning_header(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        # Write a file larger than 1 MB
        large_content = b"x" * (1024 * 1024 + 1)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "big.txt", large_content)
        r = client.get(_url(repo.id, draft.id, "big.txt"), headers=auth_headers(user_id=_USER_ID))
        assert r.headers.get("x-large-file-warning") == "true"

    def test_no_large_file_warning_for_small_file(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "small.txt", b"tiny")
        r = client.get(_url(repo.id, draft.id, "small.txt"), headers=auth_headers(user_id=_USER_ID))
        assert "x-large-file-warning" not in r.headers

    def test_nested_path_readable(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "src/utils/helper.py", b"code")
        r = client.get(_url(repo.id, draft.id, "src/utils/helper.py"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 200
        assert r.content == b"code"


class TestReadFileErrors:
    def test_file_not_found_returns_404(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.get(_url(repo.id, draft.id, "nonexistent.txt"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 404

    def test_deleted_extension_returns_400(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.get(_url(repo.id, draft.id, "file.txt.deleted"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 400

    def test_wrong_owner_returns_403(self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_OTHER_ID)
        r = client.get(_url(repo.id, draft.id, "file.txt"), headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 403


class TestReadFileAuth:
    def test_no_token_returns_401(self, client, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        assert client.get(_url(repo.id, draft.id, "file.txt")).status_code == 401
