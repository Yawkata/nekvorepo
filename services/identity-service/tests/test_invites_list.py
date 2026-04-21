"""
Tests for GET /v1/repos/{repo_id}/invites — List pending invites.
"""
import uuid

import pytest

from shared.constants import RepoRole


def _url(repo_id):
    return f"/v1/repos/{repo_id}/invites"


def test_returns_only_unconsumed_unexpired(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    valid = make_invite_token(repo.id, invited_email="valid@example.com", hours=72)
    make_invite_token(repo.id, invited_email="expired@example.com", hours=-1)       # expired
    make_invite_token(repo.id, invited_email="consumed@example.com", consumed=True) # consumed

    resp = client.get(_url(repo.id), headers=auth_headers())
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["invited_email"] == "valid@example.com"


def test_requires_admin_403(client, auth_headers, make_repo, make_membership):
    repo = make_repo()
    reader_id = "reader-user"
    make_membership(repo.id, reader_id, RepoRole.reader)
    resp = client.get(_url(repo.id), headers=auth_headers(user_id=reader_id))
    assert resp.status_code == 403


def test_empty_list_when_no_pending(client, auth_headers, make_repo):
    repo = make_repo()
    resp = client.get(_url(repo.id), headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json() == []


def test_response_fields_shape(client, auth_headers, make_repo, make_invite_token):
    repo = make_repo()
    make_invite_token(repo.id, invited_email="check@example.com")
    resp = client.get(_url(repo.id), headers=auth_headers())
    assert resp.status_code == 200
    item = resp.json()[0]
    assert "token_id" in item
    assert "invited_email" in item
    assert "role" in item
    assert "expires_at" in item
    assert "created_at" in item
