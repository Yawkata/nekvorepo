"""
Tests for DELETE /v1/internal/repos/{repo_id}/members/{user_id}/drafts
"""
import os
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from sqlmodel import select

from shared.constants import DraftStatus
from shared.models.repo import Draft


_OWNER = "target-user-sub"
_OTHER = "other-user-sub"


def _url(repo_id, user_id=_OWNER):
    return f"/v1/internal/repos/{repo_id}/members/{user_id}/drafts"


def _make_efs_dir(tmp_efs: str, user_id: str, repo_id, draft_id) -> Path:
    """Create a real EFS directory tree for a draft."""
    d = Path(tmp_efs) / user_id / str(repo_id) / str(draft_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "file.txt").write_text("hello")
    return d


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_deletes_all_drafts_204(client, make_repo, make_draft):
    repo = make_repo()
    make_draft(repo.id, user_id=_OWNER, status=DraftStatus.editing)
    make_draft(repo.id, user_id=_OWNER, status=DraftStatus.needs_rebase)

    resp = client.delete(_url(repo.id))
    assert resp.status_code == 204


def test_draft_rows_removed_from_db(client, make_repo, make_draft, db_session):
    repo = make_repo()
    d1 = make_draft(repo.id, user_id=_OWNER, status=DraftStatus.editing)
    d2 = make_draft(repo.id, user_id=_OWNER, status=DraftStatus.needs_rebase)

    client.delete(_url(repo.id))

    db_session.expire_all()
    remaining = db_session.exec(
        select(Draft).where(Draft.repo_id == repo.id, Draft.user_id == _OWNER)
    ).all()
    assert remaining == []


def test_efs_dirs_wiped(client, make_repo, make_draft, tmp_efs):
    """Real EFS directory is removed when drafts are deleted."""
    repo = make_repo()
    d = make_draft(repo.id, user_id=_OWNER)
    efs_dir = _make_efs_dir(tmp_efs, _OWNER, repo.id, d.id)
    assert efs_dir.exists()

    resp = client.delete(_url(repo.id))
    assert resp.status_code == 204
    assert not efs_dir.exists()


def test_efs_failure_does_not_block(client, make_repo, make_draft, db_session):
    """Even if EFS delete_dir raises, DB rows must be deleted and 204 returned."""
    repo = make_repo()
    d = make_draft(repo.id, user_id=_OWNER)

    from app.services.efs import EFSService
    mock_efs = MagicMock(spec=EFSService)
    mock_efs.delete_dir.side_effect = OSError("EFS unavailable")

    from app.api import deps
    original = client.app.dependency_overrides.get(deps.get_efs)
    client.app.dependency_overrides[deps.get_efs] = lambda: mock_efs
    try:
        resp = client.delete(_url(repo.id))
    finally:
        if original is None:
            client.app.dependency_overrides.pop(deps.get_efs, None)
        else:
            client.app.dependency_overrides[deps.get_efs] = original

    assert resp.status_code == 204

    db_session.expire_all()
    remaining = db_session.exec(
        select(Draft).where(Draft.repo_id == repo.id, Draft.user_id == _OWNER)
    ).all()
    assert remaining == []


def test_no_drafts_is_204_no_op(client, make_repo):
    repo = make_repo()
    resp = client.delete(_url(repo.id))
    assert resp.status_code == 204


def test_different_repo_unaffected(client, make_repo, make_draft, db_session):
    repo1 = make_repo(repo_name="repo-1")
    repo2 = make_repo(repo_name="repo-2")
    d2 = make_draft(repo2.id, user_id=_OWNER, status=DraftStatus.editing)

    client.delete(_url(repo1.id))

    db_session.expire_all()
    still_there = db_session.get(Draft, d2.id)
    assert still_there is not None


def test_different_user_unaffected(client, make_repo, make_draft, db_session):
    repo = make_repo()
    other_draft = make_draft(repo.id, user_id=_OTHER, status=DraftStatus.editing)

    client.delete(_url(repo.id, user_id=_OWNER))

    db_session.expire_all()
    still_there = db_session.get(Draft, other_draft.id)
    assert still_there is not None


# ---------------------------------------------------------------------------
# Auth / validation
# ---------------------------------------------------------------------------

def test_no_auth_required(client, make_repo):
    """Internal endpoint — should return 204 with no Authorization header."""
    repo = make_repo()
    resp = client.delete(_url(repo.id))
    assert resp.status_code == 204


def test_invalid_uuid_422(client):
    resp = client.delete("/v1/internal/repos/not-a-uuid/members/some-user/drafts")
    assert resp.status_code == 422
