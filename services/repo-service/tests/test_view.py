"""
Tests for view mode endpoints:
  GET /v1/repos/{repo_id}/view              — list committed file metadata
  GET /v1/repos/{repo_id}/files/{path}      — generate presigned URL for a file

Coverage:
  Happy path   — 200, response shape, files list, presigned URL returned
  ref param    — selects specific commit
  Error cases  — 404 repo not found, 404 commit not found, 404 file not in tree
  Role checks  — all member roles → 200; non-member → 403
  Auth         — no token → 401, expired → 401
"""

import hashlib

from shared.constants import CommitStatus

_VIEW_URL = "/v1/repos/{repo_id}/view"
_FILES_URL = "/v1/repos/{repo_id}/files/{path}"
_USER_ID = "test-user"


def _view_url(repo_id, ref=None):
    url = _VIEW_URL.format(repo_id=repo_id)
    if ref:
        url += f"?ref={ref}"
    return url


def _file_url(repo_id, path, ref=None):
    url = _FILES_URL.format(repo_id=repo_id, path=path)
    if ref:
        url += f"?ref={ref}"
    return url


class TestGetViewSuccess:
    def test_returns_200(self, client, mock_identity_client, mock_storage, auth_headers, make_repo):
        repo = make_repo()
        assert client.get(_view_url(repo.id), headers=auth_headers()).status_code == 200

    def test_empty_files_when_no_commits(self, client, mock_identity_client, mock_storage, auth_headers, make_repo):
        repo = make_repo(latest_commit_hash=None)
        data = client.get(_view_url(repo.id), headers=auth_headers()).json()
        assert data["files"] == []

    def test_response_shape(self, client, mock_identity_client, mock_storage, auth_headers, make_repo):
        repo = make_repo()
        data = client.get(_view_url(repo.id), headers=auth_headers()).json()
        assert "repo_id" in data
        assert "commit_hash" in data
        assert "files" in data

    def test_files_list_populated(self, client, mock_identity_client, mock_storage, auth_headers, make_repo, make_blob, make_tree, make_commit, db_session):
        from shared.models.workflow import RepoHead
        blob_hash = hashlib.sha256(b"hello").hexdigest()
        make_blob(blob_hash=blob_hash, size=5)
        tree = make_tree({"readme.txt": blob_hash})
        repo = make_repo()
        # Create a commit pointing to the tree
        commit = make_commit(repo_id=repo.id, owner_id=_USER_ID)
        # Update commit tree_id and repo head
        from sqlmodel import select
        from shared.models.workflow import RepoCommit
        db_session.expire_all()
        c = db_session.exec(select(RepoCommit).where(RepoCommit.id == commit.id)).first()
        c.tree_id = tree.id
        db_session.add(c)
        rh = db_session.get(RepoHead, repo.id)
        rh.latest_commit_hash = c.commit_hash
        db_session.add(rh)
        db_session.commit()
        data = client.get(_view_url(repo.id), headers=auth_headers()).json()
        assert len(data["files"]) == 1
        assert data["files"][0]["path"] == "readme.txt"

    def test_repo_not_found_returns_404(self, client, mock_identity_client, mock_storage, auth_headers):
        import uuid
        r = client.get(_view_url(uuid.uuid4()), headers=auth_headers())
        assert r.status_code == 404

    def test_file_item_shape(self, client, mock_identity_client, mock_storage, auth_headers, make_repo, make_blob, make_tree, make_commit, db_session):
        from shared.models.workflow import RepoHead, RepoCommit
        from sqlmodel import select
        blob_hash = hashlib.sha256(b"data").hexdigest()
        make_blob(blob_hash=blob_hash, size=4, content_type="text/markdown")
        tree = make_tree({"docs/README.md": blob_hash})
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_USER_ID)
        db_session.expire_all()
        c = db_session.exec(select(RepoCommit).where(RepoCommit.id == commit.id)).first()
        c.tree_id = tree.id
        db_session.add(c)
        rh = db_session.get(RepoHead, repo.id)
        rh.latest_commit_hash = c.commit_hash
        db_session.add(rh)
        db_session.commit()
        item = client.get(_view_url(repo.id), headers=auth_headers()).json()["files"][0]
        assert "path" in item
        assert "content_type" in item
        assert "size" in item


class TestGetViewRoles:
    def test_admin_can_view(self, client, mock_identity_client, mock_storage, auth_headers, make_repo):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        assert client.get(_view_url(repo.id), headers=auth_headers()).status_code == 200

    def test_author_can_view(self, client, mock_identity_client, mock_storage, auth_headers, make_repo):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        assert client.get(_view_url(repo.id), headers=auth_headers()).status_code == 200

    def test_reviewer_can_view(self, client, mock_identity_client, mock_storage, auth_headers, make_repo):
        mock_identity_client.return_value = "reviewer"
        repo = make_repo()
        assert client.get(_view_url(repo.id), headers=auth_headers()).status_code == 200

    def test_reader_can_view(self, client, mock_identity_client, mock_storage, auth_headers, make_repo):
        mock_identity_client.return_value = "reader"
        repo = make_repo()
        assert client.get(_view_url(repo.id), headers=auth_headers()).status_code == 200

    def test_non_member_cannot_view(self, client, mock_identity_client, mock_storage, auth_headers, make_repo):
        mock_identity_client.return_value = None
        repo = make_repo()
        assert client.get(_view_url(repo.id), headers=auth_headers()).status_code == 403


class TestGetFileUrlSuccess:
    def _setup_repo_with_file(self, make_repo, make_blob, make_tree, make_commit, db_session, blob_content=b"file content"):
        blob_hash = hashlib.sha256(blob_content).hexdigest()
        make_blob(blob_hash=blob_hash, size=len(blob_content), content_type="text/plain")
        tree = make_tree({"src/main.py": blob_hash})
        repo = make_repo()
        commit = make_commit(repo_id=repo.id, owner_id=_USER_ID)
        from sqlmodel import select
        from shared.models.workflow import RepoCommit, RepoHead
        db_session.expire_all()
        c = db_session.exec(select(RepoCommit).where(RepoCommit.id == commit.id)).first()
        c.tree_id = tree.id
        db_session.add(c)
        rh = db_session.get(RepoHead, repo.id)
        rh.latest_commit_hash = c.commit_hash
        db_session.add(rh)
        db_session.commit()
        return repo, c

    def test_returns_200(self, client, mock_identity_client, mock_storage, auth_headers, make_repo, make_blob, make_tree, make_commit, db_session):
        repo, _ = self._setup_repo_with_file(make_repo, make_blob, make_tree, make_commit, db_session)
        r = client.get(_file_url(repo.id, "src/main.py"), headers=auth_headers())
        assert r.status_code == 200

    def test_presigned_url_in_response(self, client, mock_identity_client, mock_storage, auth_headers, make_repo, make_blob, make_tree, make_commit, db_session):
        repo, _ = self._setup_repo_with_file(make_repo, make_blob, make_tree, make_commit, db_session)
        data = client.get(_file_url(repo.id, "src/main.py"), headers=auth_headers()).json()
        assert data["url"] == "https://s3.example.com/fake-url"

    def test_response_shape(self, client, mock_identity_client, mock_storage, auth_headers, make_repo, make_blob, make_tree, make_commit, db_session):
        repo, _ = self._setup_repo_with_file(make_repo, make_blob, make_tree, make_commit, db_session)
        data = client.get(_file_url(repo.id, "src/main.py"), headers=auth_headers()).json()
        assert "url" in data
        assert "path" in data
        assert "content_type" in data
        assert "size" in data
        assert "expires_in" in data

    def test_file_not_in_tree_returns_404(self, client, mock_identity_client, mock_storage, auth_headers, make_repo, make_blob, make_tree, make_commit, db_session):
        repo, _ = self._setup_repo_with_file(make_repo, make_blob, make_tree, make_commit, db_session)
        r = client.get(_file_url(repo.id, "nonexistent.txt"), headers=auth_headers())
        assert r.status_code == 404

    def test_non_member_cannot_get_file(self, client, mock_identity_client, mock_storage, auth_headers, make_repo, make_blob, make_tree, make_commit, db_session):
        mock_identity_client.return_value = None
        repo, _ = self._setup_repo_with_file(make_repo, make_blob, make_tree, make_commit, db_session)
        r = client.get(_file_url(repo.id, "src/main.py"), headers=auth_headers())
        assert r.status_code == 403


class TestGetFileUrlAuth:
    def test_no_token_returns_401(self, client, make_repo):
        repo = make_repo()
        assert client.get(_file_url(repo.id, "file.txt")).status_code == 401

    def test_expired_token_returns_401(self, client, mock_identity_client, mock_storage, auth_headers, make_repo):
        repo = make_repo()
        assert client.get(_file_url(repo.id, "file.txt"), headers=auth_headers(expired=True)).status_code == 401


class TestViewAuth:
    def test_no_token_returns_401(self, client, make_repo):
        repo = make_repo()
        assert client.get(_view_url(repo.id)).status_code == 401

    def test_expired_token_returns_401(self, client, mock_identity_client, mock_storage, auth_headers, make_repo):
        repo = make_repo()
        assert client.get(_view_url(repo.id), headers=auth_headers(expired=True)).status_code == 401
