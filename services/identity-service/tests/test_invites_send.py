"""
Tests for POST /v1/repos/{repo_id}/invites — Send invite.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from shared.constants import RepoRole
from shared.models.invite import InviteToken


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INVITEE = "invitee@example.com"
_INVITE_URL = "/v1/repos/{repo_id}/invites"


def _url(repo_id):
    return f"/v1/repos/{repo_id}/invites"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_happy_path_201_creates_token_row(client, auth_headers, make_repo, db_session):
    repo = make_repo()
    resp = client.post(
        _url(repo.id),
        json={"email": _INVITEE, "role": "reader"},
        headers=auth_headers(),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["invited_email"] == _INVITEE
    assert body["role"] == "reader"
    assert "token_id" in body
    assert "expires_at" in body

    # Verify DB row was created
    token = db_session.get(InviteToken, uuid.UUID(body["token_id"]))
    assert token is not None
    assert token.invited_email == _INVITEE
    assert token.role == RepoRole.reader
    assert token.consumed_at is None


def test_ses_called_on_success(client, auth_headers, make_repo):
    repo = make_repo()
    with patch("app.api.v1.endpoints.invites.send_invite_notification") as mock_ses:
        resp = client.post(
            _url(repo.id),
            json={"email": _INVITEE, "role": "author"},
            headers=auth_headers(),
        )
    assert resp.status_code == 201
    mock_ses.assert_called_once()
    args = mock_ses.call_args
    assert args.kwargs["recipient_email"] == _INVITEE
    assert args.kwargs["role"] == "author"


def test_ses_failure_does_not_block_201(client, auth_headers, make_repo):
    repo = make_repo()
    with patch("app.api.v1.endpoints.invites.send_invite_notification", side_effect=Exception("SES down")):
        resp = client.post(
            _url(repo.id),
            json={"email": _INVITEE, "role": "reader"},
            headers=auth_headers(),
        )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["author", "reviewer", "reader"])
def test_requires_admin_403(client, auth_headers, make_repo, make_membership, role):
    repo = make_repo()
    other_id = "other-user"
    make_membership(repo.id, other_id, RepoRole[role])
    resp = client.post(
        _url(repo.id),
        json={"email": _INVITEE, "role": "reader"},
        headers=auth_headers(user_id=other_id),
    )
    assert resp.status_code == 403


def test_expired_passport_401(client, auth_headers, make_repo):
    repo = make_repo()
    resp = client.post(
        _url(repo.id),
        json={"email": _INVITEE, "role": "reader"},
        headers=auth_headers(expired=True),
    )
    assert resp.status_code == 401


def test_repo_not_found_404(client, auth_headers):
    resp = client.post(
        _url(uuid.uuid4()),
        json={"email": _INVITEE, "role": "reader"},
        headers=auth_headers(),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Conflict guards
# ---------------------------------------------------------------------------

def test_already_member_409(client, auth_headers, make_repo, make_membership, make_user):
    repo = make_repo()
    other_id = "member-user"
    make_user(user_id=other_id, email=_INVITEE)
    make_membership(repo.id, other_id, RepoRole.reader)
    resp = client.post(
        _url(repo.id),
        json={"email": _INVITEE, "role": "reader"},
        headers=auth_headers(),
    )
    assert resp.status_code == 409
    assert "already a member" in resp.json()["detail"]


def test_pending_invite_exists_409(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    make_invite_token(repo.id, invited_email=_INVITEE, hours=72)
    resp = client.post(
        _url(repo.id),
        json={"email": _INVITEE, "role": "reader"},
        headers=auth_headers(),
    )
    assert resp.status_code == 409
    assert "pending invite" in resp.json()["detail"]


def test_expired_token_allows_new_201(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    # Seed an expired token (hours=-1 → already expired)
    make_invite_token(repo.id, invited_email=_INVITEE, hours=-1)
    resp = client.post(
        _url(repo.id),
        json={"email": _INVITEE, "role": "reader"},
        headers=auth_headers(),
    )
    assert resp.status_code == 201


def test_consumed_token_allows_new_201(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    make_invite_token(repo.id, invited_email=_INVITEE, consumed=True)
    resp = client.post(
        _url(repo.id),
        json={"email": _INVITEE, "role": "reader"},
        headers=auth_headers(),
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_invalid_role_422(client, auth_headers, make_repo):
    repo = make_repo()
    resp = client.post(
        _url(repo.id),
        json={"email": _INVITEE, "role": "superadmin"},
        headers=auth_headers(),
    )
    assert resp.status_code == 422
