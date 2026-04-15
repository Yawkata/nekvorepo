"""
Path security tests — prevent directory traversal and path injection.

Any endpoint that accepts a file path must reject:
  - Parent-directory traversal (../)
  - Absolute paths (/etc/passwd)
  - Null bytes in paths
  - Paths that resolve outside the draft's EFS root

Coverage:
  Save endpoint   — POST .../save with malicious path in JSON body
  Read endpoint   — GET  .../files/{path} with malicious path in URL
  Delete endpoint — DELETE .../files/{path} with malicious path in URL
  Mkdir endpoint  — POST .../mkdir with malicious path in JSON body
  Rename endpoint — POST .../rename with malicious from_path / to_path in JSON body
  Upload endpoint — POST .../upload with malicious path in multipart form field

The service should return 400, 404, or 422 for all these cases.
A 404 "draft not found" is also acceptable as it means the path was rejected
before hitting the filesystem — but a 200/201/204 is never acceptable.
"""

import pytest

_SAVE_URL   = "/v1/repos/{repo_id}/drafts/{draft_id}/save"
_READ_URL   = "/v1/repos/{repo_id}/drafts/{draft_id}/files/{path}"
_DELETE_URL = "/v1/repos/{repo_id}/drafts/{draft_id}/files/{path}"
_MKDIR_URL  = "/v1/repos/{repo_id}/drafts/{draft_id}/mkdir"
_RENAME_URL = "/v1/repos/{repo_id}/drafts/{draft_id}/rename"
_UPLOAD_URL = "/v1/repos/{repo_id}/drafts/{draft_id}/upload"

_USER_ID = "test-user"

_SAFE_STATUS_CODES = {400, 404, 422}
_FORBIDDEN_STATUS_CODES = {200, 201, 204}


def _save_url(repo_id, draft_id):
    return _SAVE_URL.format(repo_id=repo_id, draft_id=draft_id)

def _read_url(repo_id, draft_id, path):
    return _READ_URL.format(repo_id=repo_id, draft_id=draft_id, path=path)

def _delete_url(repo_id, draft_id, path):
    return _DELETE_URL.format(repo_id=repo_id, draft_id=draft_id, path=path)

def _mkdir_url(repo_id, draft_id):
    return _MKDIR_URL.format(repo_id=repo_id, draft_id=draft_id)

def _rename_url(repo_id, draft_id):
    return _RENAME_URL.format(repo_id=repo_id, draft_id=draft_id)

def _upload_url(repo_id, draft_id):
    return _UPLOAD_URL.format(repo_id=repo_id, draft_id=draft_id)


# ---------------------------------------------------------------------------
# Save endpoint — path traversal via JSON body
# ---------------------------------------------------------------------------

class TestSavePathTraversal:
    @pytest.mark.parametrize("malicious_path", [
        "../../../etc/passwd",
        "../../secret.txt",
        "../sibling-draft/stolen.txt",
        "./../../outside.txt",
    ])
    def test_parent_traversal_rejected(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, malicious_path
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _save_url(repo.id, draft.id),
            json={"path": malicious_path, "content": "pwned"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code not in _FORBIDDEN_STATUS_CODES, (
            f"Expected rejection for path={malicious_path!r}, got {r.status_code}"
        )

    @pytest.mark.parametrize("malicious_path", [
        "/etc/passwd",
        "/absolute/path.txt",
        "//double-slash.txt",
    ])
    def test_absolute_path_rejected(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, malicious_path
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _save_url(repo.id, draft.id),
            json={"path": malicious_path, "content": "pwned"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code not in _FORBIDDEN_STATUS_CODES, (
            f"Expected rejection for path={malicious_path!r}, got {r.status_code}"
        )

    def test_null_byte_in_path_rejected(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft
    ):
        """Null bytes can be used to truncate C-string paths in OS calls."""
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _save_url(repo.id, draft.id),
            json={"path": "file\x00.txt", "content": "pwned"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code not in _FORBIDDEN_STATUS_CODES

    def test_empty_path_rejected(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _save_url(repo.id, draft.id),
            json={"path": "", "content": "x"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code not in _FORBIDDEN_STATUS_CODES


# ---------------------------------------------------------------------------
# Read endpoint — path traversal via URL segment
# ---------------------------------------------------------------------------

class TestReadPathTraversal:
    @pytest.mark.parametrize("malicious_path", [
        "../../../etc/passwd",
        "../../secret.txt",
    ])
    def test_parent_traversal_rejected(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, malicious_path
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.get(
            _read_url(repo.id, draft.id, malicious_path),
            headers=auth_headers(user_id=_USER_ID),
        )
        # 404 is fine here (path doesn't exist); 200 with secret content is not
        assert r.status_code not in _FORBIDDEN_STATUS_CODES


# ---------------------------------------------------------------------------
# Delete endpoint — path traversal via URL segment
# ---------------------------------------------------------------------------

class TestDeletePathTraversal:
    @pytest.mark.parametrize("malicious_path", [
        "../../../etc/passwd",
        "../../important.txt",
    ])
    def test_parent_traversal_rejected(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, malicious_path
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.delete(
            _delete_url(repo.id, draft.id, malicious_path),
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code not in _FORBIDDEN_STATUS_CODES


# ---------------------------------------------------------------------------
# Mkdir endpoint — path traversal via JSON body
# ---------------------------------------------------------------------------

class TestMkdirPathTraversal:
    @pytest.mark.parametrize("malicious_path", [
        "../../../tmp/evil",
        "../../other-draft/injected",
        "/absolute/dir",
    ])
    def test_parent_traversal_rejected(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, malicious_path
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _mkdir_url(repo.id, draft.id),
            json={"path": malicious_path},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code not in _FORBIDDEN_STATUS_CODES, (
            f"Expected rejection for path={malicious_path!r}, got {r.status_code}"
        )


# ---------------------------------------------------------------------------
# Rename endpoint — path traversal via JSON body (from_path and to_path)
# ---------------------------------------------------------------------------

class TestRenamePathTraversal:
    @pytest.mark.parametrize("malicious_from", [
        "../../../etc/passwd",
        "../../secret.txt",
        "/absolute/source.txt",
    ])
    def test_malicious_from_path_rejected(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, malicious_from
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _rename_url(repo.id, draft.id),
            json={"from_path": malicious_from, "to_path": "safe/dest.txt"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code not in _FORBIDDEN_STATUS_CODES, (
            f"Expected rejection for from_path={malicious_from!r}, got {r.status_code}"
        )

    @pytest.mark.parametrize("malicious_to", [
        "../../../tmp/evil.txt",
        "../../other-draft/stolen.txt",
        "/absolute/dest.txt",
    ])
    def test_malicious_to_path_rejected(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, malicious_to
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _rename_url(repo.id, draft.id),
            json={"from_path": "safe/source.txt", "to_path": malicious_to},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code not in _FORBIDDEN_STATUS_CODES, (
            f"Expected rejection for to_path={malicious_to!r}, got {r.status_code}"
        )


# ---------------------------------------------------------------------------
# Upload endpoint — path traversal via multipart form field
# ---------------------------------------------------------------------------

class TestUploadPathTraversal:
    @pytest.mark.parametrize("malicious_path", [
        "../../../etc/passwd",
        "../../secret.bin",
        "/absolute/path.bin",
        "../sibling-draft/stolen.bin",
    ])
    def test_parent_traversal_rejected(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, malicious_path
    ):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _upload_url(repo.id, draft.id),
            data={"path": malicious_path},
            files={"file": ("filename", b"\x00\x01\x02", "application/octet-stream")},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code not in _FORBIDDEN_STATUS_CODES, (
            f"Expected rejection for path={malicious_path!r}, got {r.status_code}"
        )

    def test_null_byte_in_path_rejected(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft
    ):
        """Null bytes must be rejected before reaching the filesystem."""
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _upload_url(repo.id, draft.id),
            data={"path": "file\x00.bin"},
            files={"file": ("filename", b"\x01\x02", "application/octet-stream")},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code not in _FORBIDDEN_STATUS_CODES
