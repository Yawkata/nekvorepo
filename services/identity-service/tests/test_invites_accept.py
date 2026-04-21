"""
Tests for POST /v1/repos/{repo_id}/invites/{token_id}/accept.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlmodel import select

from shared.constants import RepoRole
from shared.models.identity import User, UserRepoLink
from shared.models.invite import InviteToken


_INVITEE_EMAIL = "invitee@example.com"
_INVITEE_ID = "invitee-sub"


def _url(repo_id, token_id):
    return f"/v1/repos/{repo_id}/invites/{token_id}/accept"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_accept_201_creates_membership(client, auth_headers, make_repo, make_invite_token, db_session):
    repo = make_repo()
    token = make_invite_token(repo.id, invited_email=_INVITEE_EMAIL, role=RepoRole.author)

    resp = client.post(
        _url(repo.id, token.id),
        headers=auth_headers(user_id=_INVITEE_ID, email=_INVITEE_EMAIL),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "author"
    assert body["repo_id"] == str(repo.id)

    link = db_session.exec(
        select(UserRepoLink).where(
            UserRepoLink.repo_id == repo.id,
            UserRepoLink.user_id == _INVITEE_ID,
        )
    ).first()
    assert link is not None
    assert link.role == RepoRole.author


def test_consumed_at_set_immediately(client, auth_headers, make_repo, make_invite_token, db_session):
    repo = make_repo()
    token = make_invite_token(repo.id, invited_email=_INVITEE_EMAIL)

    client.post(
        _url(repo.id, token.id),
        headers=auth_headers(user_id=_INVITEE_ID, email=_INVITEE_EMAIL),
    )

    db_session.expire_all()
    refreshed = db_session.get(InviteToken, token.id)
    assert refreshed.consumed_at is not None


def test_upserts_users_table(client, auth_headers, make_repo, make_invite_token, db_session):
    repo = make_repo()
    token = make_invite_token(repo.id, invited_email=_INVITEE_EMAIL)

    client.post(
        _url(repo.id, token.id),
        headers=auth_headers(user_id=_INVITEE_ID, email=_INVITEE_EMAIL),
    )

    user = db_session.exec(select(User).where(User.id == _INVITEE_ID)).first()
    assert user is not None
    assert user.email == _INVITEE_EMAIL


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_wrong_email_403(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    token = make_invite_token(repo.id, invited_email=_INVITEE_EMAIL)

    resp = client.post(
        _url(repo.id, token.id),
        headers=auth_headers(user_id="other-sub", email="other@example.com"),
    )
    assert resp.status_code == 403
    assert "not sent to your email" in resp.json()["detail"]


def test_expired_token_410(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    token = make_invite_token(repo.id, invited_email=_INVITEE_EMAIL, hours=-1)

    resp = client.post(
        _url(repo.id, token.id),
        headers=auth_headers(user_id=_INVITEE_ID, email=_INVITEE_EMAIL),
    )
    assert resp.status_code == 410
    assert "expired" in resp.json()["detail"]


def test_already_consumed_410(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    token = make_invite_token(repo.id, invited_email=_INVITEE_EMAIL, consumed=True)

    resp = client.post(
        _url(repo.id, token.id),
        headers=auth_headers(user_id=_INVITEE_ID, email=_INVITEE_EMAIL),
    )
    assert resp.status_code == 410
    assert "accepted" in resp.json()["detail"]


def test_concurrent_accept_race(client, auth_headers, make_repo, make_invite_token, make_membership):
    """First accept wins; second call after link is created returns 410."""
    repo = make_repo()
    token = make_invite_token(repo.id, invited_email=_INVITEE_EMAIL)

    first = client.post(
        _url(repo.id, token.id),
        headers=auth_headers(user_id=_INVITEE_ID, email=_INVITEE_EMAIL),
    )
    assert first.status_code == 201

    # Second call — token consumed_at is now set → rowcount=0 → 410
    second = client.post(
        _url(repo.id, token.id),
        headers=auth_headers(user_id="other-sub", email=_INVITEE_EMAIL),
    )
    assert second.status_code == 410


def test_already_member_409(client, auth_headers, make_repo, make_invite_token, make_membership):
    """Edge case: member added by other means after token issued; accept sets consumed_at then hits 409."""
    repo = make_repo()
    token = make_invite_token(repo.id, invited_email=_INVITEE_EMAIL)
    # Add membership manually (simulates race where user was added another way)
    make_membership(repo.id, _INVITEE_ID, RepoRole.reader)

    resp = client.post(
        _url(repo.id, token.id),
        headers=auth_headers(user_id=_INVITEE_ID, email=_INVITEE_EMAIL),
    )
    assert resp.status_code == 409
    assert "already a member" in resp.json()["detail"]


def test_not_found_404(client, auth_headers, make_repo):
    repo = make_repo()
    resp = client.post(
        _url(repo.id, uuid.uuid4()),
        headers=auth_headers(user_id=_INVITEE_ID, email=_INVITEE_EMAIL),
    )
    assert resp.status_code == 404


def test_no_passport_401(client, make_repo, make_invite_token):
    repo = make_repo()
    token = make_invite_token(repo.id, invited_email=_INVITEE_EMAIL)
    resp = client.post(_url(repo.id, token.id))
    assert resp.status_code == 401


def test_expiry_checked_before_consumed(client, auth_headers, make_repo, make_invite_token):
    """Token that is both expired AND consumed → should return 'expired' message, not 'accepted'."""
    repo = make_repo()
    token = make_invite_token(repo.id, invited_email=_INVITEE_EMAIL, hours=-1, consumed=True)

    resp = client.post(
        _url(repo.id, token.id),
        headers=auth_headers(user_id=_INVITEE_ID, email=_INVITEE_EMAIL),
    )
    assert resp.status_code == 410
    assert "expired" in resp.json()["detail"]
