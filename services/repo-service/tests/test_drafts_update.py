"""
Tests for PATCH /v1/repos/{repo_id}/drafts/{draft_id} — rename a draft label.

Coverage:
  Happy path  — 200, label updated in response and DB
  Error cases — 404 draft not found, 403 wrong owner
  Validation  — label too long → 422, blank label → 422
  Auth        — no token → 401
"""

from sqlmodel import select

from shared.models.repo import Draft

_URL = "/v1/repos/{repo_id}/drafts/{draft_id}"
_USER_ID = "test-user"
_OTHER_ID = "other-user"


def _url(repo_id, draft_id):
    return _URL.format(repo_id=repo_id, draft_id=draft_id)


class TestUpdateDraftSuccess:
    def test_returns_200(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.patch(_url(repo.id, draft.id), json={"label": "New Label"}, headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 200

    def test_label_updated_in_response(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        data = client.patch(
            _url(repo.id, draft.id), json={"label": "Updated"}, headers=auth_headers(user_id=_USER_ID)
        ).json()
        assert data["label"] == "Updated"

    def test_label_updated_in_db(self, client, mock_identity_client, auth_headers, make_repo, make_draft, db_session):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        client.patch(_url(repo.id, draft.id), json={"label": "DB Check"}, headers=auth_headers(user_id=_USER_ID))
        db_session.expire_all()
        updated = db_session.exec(select(Draft).where(Draft.id == draft.id)).first()
        assert updated.label == "DB Check"

    def test_admin_can_update_any_draft(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "admin"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_OTHER_ID)
        r = client.patch(_url(repo.id, draft.id), json={"label": "Admin Edit"}, headers=auth_headers())
        assert r.status_code == 200


class TestUpdateDraftErrors:
    def test_draft_not_found_returns_404(self, client, mock_identity_client, auth_headers, make_repo):
        import uuid
        repo = make_repo()
        r = client.patch(_url(repo.id, uuid.uuid4()), json={"label": "X"}, headers=auth_headers())
        assert r.status_code == 404

    def test_wrong_owner_returns_403(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        mock_identity_client.return_value = "author"
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_OTHER_ID)
        r = client.patch(_url(repo.id, draft.id), json={"label": "Hack"}, headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 403


class TestUpdateDraftValidation:
    def test_label_too_long_returns_422(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.patch(_url(repo.id, draft.id), json={"label": "x" * 101}, headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 422

    def test_empty_label_returns_422(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        r = client.patch(_url(repo.id, draft.id), json={"label": ""}, headers=auth_headers(user_id=_USER_ID))
        assert r.status_code == 422


class TestUpdateDraftAuth:
    def test_no_token_returns_401(self, client, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        assert client.patch(_url(repo.id, draft.id), json={"label": "X"}).status_code == 401

    def test_expired_token_returns_401(self, client, mock_identity_client, auth_headers, make_repo, make_draft):
        repo = make_repo()
        draft = make_draft(repo_id=repo.id, user_id=_USER_ID)
        assert client.patch(
            _url(repo.id, draft.id), json={"label": "X"}, headers=auth_headers(expired=True)
        ).status_code == 401
