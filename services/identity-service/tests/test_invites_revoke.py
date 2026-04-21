"""
Tests for POST /v1/repos/{repo_id}/invites/{token_id}/revoke.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from shared.constants import RepoRole
from shared.models.invite import InviteToken


def _url(repo_id, token_id):
    return f"/v1/repos/{repo_id}/invites/{token_id}/revoke"


def test_revoke_204_sets_expires_at(client, auth_headers, make_repo, make_invite_token, db_session):
    repo = make_repo()
    token = make_invite_token(repo.id)
    resp = client.post(_url(repo.id, token.id), headers=auth_headers())
    assert resp.status_code == 204

    db_session.expire_all()
    refreshed = db_session.get(InviteToken, token.id)
    assert refreshed.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)


def test_token_excluded_from_pending_after_revoke(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    token = make_invite_token(repo.id)

    client.post(_url(repo.id, token.id), headers=auth_headers())

    resp = client.get(f"/v1/repos/{repo.id}/invites", headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json() == []


def test_ses_not_called(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    token = make_invite_token(repo.id)
    with patch("app.api.v1.endpoints.invites.send_invite_notification") as mock_ses:
        resp = client.post(_url(repo.id, token.id), headers=auth_headers())
    assert resp.status_code == 204
    mock_ses.assert_not_called()


def test_consumed_token_410(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    token = make_invite_token(repo.id, consumed=True)
    resp = client.post(_url(repo.id, token.id), headers=auth_headers())
    assert resp.status_code == 410
    assert "consumed" in resp.json()["detail"]


def test_not_found_404(client, auth_headers, make_repo):
    repo = make_repo()
    resp = client.post(_url(repo.id, uuid.uuid4()), headers=auth_headers())
    assert resp.status_code == 404


def test_requires_admin_403(client, auth_headers, make_repo, make_membership, make_invite_token):
    repo = make_repo()
    reader_id = "reader-user"
    make_membership(repo.id, reader_id, RepoRole.reader)
    token = make_invite_token(repo.id)
    resp = client.post(_url(repo.id, token.id), headers=auth_headers(user_id=reader_id))
    assert resp.status_code == 403


def test_no_passport_401(client, make_repo, make_invite_token):
    repo = make_repo()
    token = make_invite_token(repo.id)
    resp = client.post(_url(repo.id, token.id))
    assert resp.status_code == 401


def test_already_expired_410(client, auth_headers, make_repo, make_invite_token):
    """Revoking an already-expired token should return 410, not silently succeed."""
    repo = make_repo()
    token = make_invite_token(repo.id, hours=-1)
    resp = client.post(_url(repo.id, token.id), headers=auth_headers())
    assert resp.status_code == 410
    assert "expired" in resp.json()["detail"]
