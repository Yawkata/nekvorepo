"""
Tests for GET /v1/repos/{repo_id}/members.
"""
import pytest

from shared.constants import RepoRole


def _url(repo_id, q=None):
    base = f"/v1/repos/{repo_id}/members"
    return f"{base}?q={q}" if q else base


def test_returns_all_members(client, auth_headers, make_repo, make_membership, make_user):
    repo = make_repo()  # creates admin member with _TEST_USER_ID
    u2, u3 = "user-b", "user-c"
    make_membership(repo.id, u2, RepoRole.author)
    make_membership(repo.id, u3, RepoRole.reviewer)

    resp = client.get(_url(repo.id), headers=auth_headers())
    assert resp.status_code == 200
    ids = {m["user_id"] for m in resp.json()}
    assert ids == {"test-user-sub", u2, u3}


def test_email_from_users_table(client, auth_headers, make_repo, make_user):
    repo = make_repo()
    make_user(user_id="test-user-sub", email="admin@example.com")

    resp = client.get(_url(repo.id), headers=auth_headers())
    assert resp.status_code == 200
    admin_row = next(m for m in resp.json() if m["user_id"] == "test-user-sub")
    assert admin_row["email"] == "admin@example.com"


def test_null_email_when_no_user_row(client, auth_headers, make_repo):
    repo = make_repo()  # _TEST_USER_ID has no User row seeded
    resp = client.get(_url(repo.id), headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json()[0]["email"] is None


def test_search_by_email_partial(client, auth_headers, make_repo, make_membership, make_user):
    repo = make_repo()
    alice_id, bob_id = "alice-sub", "bob-sub"
    make_user(user_id=alice_id, email="alice@example.com")
    make_user(user_id=bob_id, email="bob@example.com")
    make_membership(repo.id, alice_id, RepoRole.reader)
    make_membership(repo.id, bob_id, RepoRole.reader)

    resp = client.get(_url(repo.id, q="alice"), headers=auth_headers())
    assert resp.status_code == 200
    ids = [m["user_id"] for m in resp.json()]
    assert alice_id in ids
    assert bob_id not in ids


def test_search_case_insensitive(client, auth_headers, make_repo, make_membership, make_user):
    repo = make_repo()
    alice_id = "alice-sub"
    make_user(user_id=alice_id, email="Alice@Example.COM")
    make_membership(repo.id, alice_id, RepoRole.reader)

    resp = client.get(_url(repo.id, q="alice"), headers=auth_headers())
    assert resp.status_code == 200
    assert any(m["user_id"] == alice_id for m in resp.json())


def test_search_no_matches_empty_list(client, auth_headers, make_repo):
    repo = make_repo()
    resp = client.get(_url(repo.id, q="zzznomatch"), headers=auth_headers())
    assert resp.status_code == 200
    assert resp.json() == []


def test_non_member_403(client, auth_headers, make_repo):
    repo = make_repo()
    resp = client.get(_url(repo.id), headers=auth_headers(user_id="stranger"))
    assert resp.status_code == 403


def test_reader_can_call(client, auth_headers, make_repo, make_membership):
    repo = make_repo()
    reader_id = "reader-user"
    make_membership(repo.id, reader_id, RepoRole.reader)
    resp = client.get(_url(repo.id), headers=auth_headers(user_id=reader_id))
    assert resp.status_code == 200


def test_reviewer_can_call(client, auth_headers, make_repo, make_membership):
    repo = make_repo()
    reviewer_id = "reviewer-user"
    make_membership(repo.id, reviewer_id, RepoRole.reviewer)
    resp = client.get(_url(repo.id), headers=auth_headers(user_id=reviewer_id))
    assert resp.status_code == 200
