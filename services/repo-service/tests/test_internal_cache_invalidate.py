"""
Tests for POST /v1/internal/cache/invalidate — role cache invalidation.

Coverage:
  Happy path  — 204 No Content
  Behaviour   — identity_client.invalidate called with correct args
  Validation  — missing repo_id/user_id → 422, invalid UUID → 422
  No auth required
"""

import uuid
from unittest.mock import patch

_URL = "/v1/internal/cache/invalidate"


class TestCacheInvalidateSuccess:
    def test_returns_204(self, client):
        r = client.post(_URL, json={"repo_id": str(uuid.uuid4()), "user_id": "test-user"})
        assert r.status_code == 204

    def test_invalidate_called_with_correct_args(self, client):
        repo_id = uuid.uuid4()
        user_id = "user-sub-123"
        with patch("app.services.identity_client.invalidate") as mock_inv:
            client.post(_URL, json={"repo_id": str(repo_id), "user_id": user_id})
            mock_inv.assert_called_once_with(str(repo_id), user_id)

    def test_no_auth_required(self, client):
        r = client.post(_URL, json={"repo_id": str(uuid.uuid4()), "user_id": "u"})
        assert r.status_code != 401

    def test_returns_no_body(self, client):
        r = client.post(_URL, json={"repo_id": str(uuid.uuid4()), "user_id": "u"})
        assert r.content == b""


class TestCacheInvalidateValidation:
    def test_missing_repo_id_returns_422(self, client):
        r = client.post(_URL, json={"user_id": "test-user"})
        assert r.status_code == 422

    def test_missing_user_id_returns_422(self, client):
        r = client.post(_URL, json={"repo_id": str(uuid.uuid4())})
        assert r.status_code == 422

    def test_invalid_repo_id_uuid_returns_422(self, client):
        r = client.post(_URL, json={"repo_id": "not-a-uuid", "user_id": "user"})
        assert r.status_code == 422
