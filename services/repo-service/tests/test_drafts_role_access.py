"""
Role-based access control tests for repo-service draft endpoints.

The existing test suite exercises all endpoints with the admin role. This file
verifies that the role hierarchy is correctly enforced:

  admin   → full access
  author  → can create/edit drafts and files
  reviewer → read-only; cannot create drafts or write files
  reader  → read-only; cannot create drafts or write files
  None    → non-member; 403 on all protected endpoints

Coverage:
  Draft creation  — admin/author → 201; reviewer/reader/non-member → 403
  File save       — admin/author → 200; reviewer/reader → 403
  File delete     — admin/author → 204; reviewer/reader → 403
  Directory mkdir — admin/author → 201; reviewer/reader → 403
  File read       — all roles    → 200 (read access for all members)
  Draft list      — all roles    → 200 (read access for all members)
"""

from pathlib import Path

import pytest

_DRAFT_URL  = "/v1/repos/{repo_id}/drafts"
_SAVE_URL   = "/v1/repos/{repo_id}/drafts/{draft_id}/save"
_DELETE_URL = "/v1/repos/{repo_id}/drafts/{draft_id}/files/{path}"
_MKDIR_URL  = "/v1/repos/{repo_id}/drafts/{draft_id}/mkdir"
_READ_URL   = "/v1/repos/{repo_id}/drafts/{draft_id}/files/{path}"
_LIST_URL   = "/v1/repos/{repo_id}/drafts"

_USER_ID = "test-user"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _draft_url(repo_id):
    return _DRAFT_URL.format(repo_id=repo_id)

def _save_url(repo_id, draft_id):
    return _SAVE_URL.format(repo_id=repo_id, draft_id=draft_id)

def _delete_url(repo_id, draft_id, path="file.txt"):
    return _DELETE_URL.format(repo_id=repo_id, draft_id=draft_id, path=path)

def _mkdir_url(repo_id, draft_id):
    return _MKDIR_URL.format(repo_id=repo_id, draft_id=draft_id)

def _read_url(repo_id, draft_id, path="file.txt"):
    return _READ_URL.format(repo_id=repo_id, draft_id=draft_id, path=path)

def _list_url(repo_id):
    return _LIST_URL.format(repo_id=repo_id)


# ---------------------------------------------------------------------------
# Draft creation — role checks
# ---------------------------------------------------------------------------

class TestCreateDraftRoleAccess:
    def test_admin_can_create_draft(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        r = client.post(_draft_url(repo.id), json={}, headers=auth_headers())
        assert r.status_code == 201

    def test_author_can_create_draft(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        r = client.post(_draft_url(repo.id), json={}, headers=auth_headers())
        assert r.status_code == 201

    def test_reviewer_cannot_create_draft(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        r = client.post(_draft_url(repo.id), json={}, headers=auth_headers())
        assert r.status_code == 403

    def test_reader_cannot_create_draft(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        r = client.post(_draft_url(repo.id), json={}, headers=auth_headers())
        assert r.status_code == 403

    def test_non_member_cannot_create_draft(self, client, mock_identity_client, auth_headers, make_repo):
        mock_identity_client.return_value = None  # not a member
        repo = make_repo()
        r = client.post(_draft_url(repo.id), json={}, headers=auth_headers())
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# File save — role checks
# ---------------------------------------------------------------------------

class TestFileSaveRoleAccess:
    def test_admin_can_save_file(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _save_url(repo.id, draft.id),
            json={"path": "readme.txt", "content": "hello"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 200

    def test_author_can_save_file(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _save_url(repo.id, draft.id),
            json={"path": "readme.txt", "content": "hello"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 200

    def test_reviewer_cannot_save_file(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        # Draft owned by a different user; reviewer should still be 403 for role reasons
        draft = make_draft(repo_id=repo.id, user_id="another-author")
        r = client.post(
            _save_url(repo.id, draft.id),
            json={"path": "readme.txt", "content": "hello"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 403

    def test_reader_cannot_save_file(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="another-author")
        r = client.post(
            _save_url(repo.id, draft.id),
            json={"path": "readme.txt", "content": "hello"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# File delete — role checks
# ---------------------------------------------------------------------------

class TestFileDeleteRoleAccess:
    def test_author_can_delete_own_file(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file
    ):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "file.txt", b"content")
        r = client.delete(
            _delete_url(repo.id, draft.id, "file.txt"),
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 204

    def test_reviewer_cannot_delete_file(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file
    ):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="another-author")
        r = client.delete(
            _delete_url(repo.id, draft.id, "file.txt"),
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 403

    def test_reader_cannot_delete_file(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft
    ):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="another-author")
        r = client.delete(
            _delete_url(repo.id, draft.id, "file.txt"),
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# mkdir — role checks
# ---------------------------------------------------------------------------

class TestMkdirRoleAccess:
    def test_admin_can_mkdir(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _mkdir_url(repo.id, draft.id),
            json={"path": "src"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 201

    def test_author_can_mkdir(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.post(
            _mkdir_url(repo.id, draft.id),
            json={"path": "src"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 201

    def test_reviewer_cannot_mkdir(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="another-author")
        r = client.post(
            _mkdir_url(repo.id, draft.id),
            json={"path": "src"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 403

    def test_reader_cannot_mkdir(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id="another-author")
        r = client.post(
            _mkdir_url(repo.id, draft.id),
            json={"path": "src"},
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Read operations — all members can read
# ---------------------------------------------------------------------------

class TestReadAccessAllRoles:
    @pytest.mark.parametrize("role", ["admin", "author", "reviewer", "reader"])
    def test_all_roles_can_list_drafts(self, client, mock_identity_client, auth_headers, make_repo, role):
        mock_identity_client.return_value = role
        repo = make_repo()
        r = client.get(_list_url(repo.id), headers=auth_headers())
        # List returns the caller's own drafts; even empty list is 200
        assert r.status_code == 200

    @pytest.mark.parametrize("role", ["admin", "author", "reviewer", "reader"])
    def test_all_roles_can_read_file(
        self, client, mock_identity_client, auth_headers, make_repo, make_draft, seed_file, role
    ):
        mock_identity_client.return_value = role
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        seed_file(_USER_ID, str(repo.id), str(draft.id), "file.txt", b"hello")
        r = client.get(
            _read_url(repo.id, draft.id, "file.txt"),
            headers=auth_headers(user_id=_USER_ID),
        )
        assert r.status_code == 200
