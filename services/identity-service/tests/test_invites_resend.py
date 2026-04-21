"""
Tests for POST /v1/repos/{repo_id}/invites/{token_id}/resend.
"""
import uuid
from unittest.mock import patch

import pytest

from shared.constants import RepoRole
from shared.models.invite import InviteToken


def _url(repo_id, token_id):
    return f"/v1/repos/{repo_id}/invites/{token_id}/resend"


def test_resend_200_creates_new_token(client, auth_headers, make_repo, make_invite_token, db_session):
    repo = make_repo()
    old_token = make_invite_token(repo.id, invited_email="user@example.com")
    old_id = old_token.id

    resp = client.post(_url(repo.id, old_id), headers=auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    new_id = uuid.UUID(body["token_id"])
    assert new_id != old_id

    # Old token must be expired now
    db_session.expire_all()
    refreshed_old = db_session.get(InviteToken, old_id)
    from datetime import datetime, timezone
    assert refreshed_old.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)


def test_old_token_excluded_from_pending_list(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    old_token = make_invite_token(repo.id, invited_email="user@example.com")

    client.post(_url(repo.id, old_token.id), headers=auth_headers())

    resp = client.get(f"/v1/repos/{repo.id}/invites", headers=auth_headers())
    assert resp.status_code == 200
    items = resp.json()
    old_ids = [i["token_id"] for i in items]
    assert str(old_token.id) not in old_ids


def test_ses_called(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    token = make_invite_token(repo.id, invited_email="user@example.com")
    with patch("app.api.v1.endpoints.invites.send_invite_notification") as mock_ses:
        resp = client.post(_url(repo.id, token.id), headers=auth_headers())
    assert resp.status_code == 200
    mock_ses.assert_called_once()


def test_consumed_token_410(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    token = make_invite_token(repo.id, consumed=True)
    resp = client.post(_url(repo.id, token.id), headers=auth_headers())
    assert resp.status_code == 410
    assert "accepted" in resp.json()["detail"]


def test_already_expired_410(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    token = make_invite_token(repo.id, hours=-1)
    resp = client.post(_url(repo.id, token.id), headers=auth_headers())
    assert resp.status_code == 410
    assert "expired" in resp.json()["detail"]


def test_wrong_repo_404(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    token = make_invite_token(repo.id)
    other_repo_id = uuid.uuid4()
    resp = client.post(_url(other_repo_id, token.id), headers=auth_headers())
    # other_repo_id doesn't exist → 404 from _require_admin
    assert resp.status_code == 404


def test_requires_admin_403(client, auth_headers, make_repo, make_membership, make_invite_token):
    repo = make_repo()
    reader_id = "reader-user"
    make_membership(repo.id, reader_id, RepoRole.reader)
    token = make_invite_token(repo.id)
    resp = client.post(_url(repo.id, token.id), headers=auth_headers(user_id=reader_id))
    assert resp.status_code == 403
