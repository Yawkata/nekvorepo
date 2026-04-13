"""
Tests for POST /v1/internal/cache/invalidate

This endpoint is cluster-internal (no auth required) and evicts a single entry
from workflow-service's in-process role cache.

Coverage:
  Happy path  — 204 No Content, cache entry evicted
  Validation  — missing fields, invalid UUID
  Auth        — no token required
"""

import uuid
from unittest.mock import patch

_URL = "/v1/internal/cache/invalidate"
_REPO_ID = str(uuid.uuid4())
_USER_ID = "some-user-sub"


class TestCacheInvalidate:
    def test_returns_204(self, client):
        r = client.post(_URL, json={"repo_id": _REPO_ID, "user_id": _USER_ID})
        assert r.status_code == 204

    def test_response_body_is_empty(self, client):
        r = client.post(_URL, json={"repo_id": _REPO_ID, "user_id": _USER_ID})
        assert r.content == b""

    def test_no_auth_required(self, client):
        """Cluster-internal endpoint — should succeed with no Authorization header."""
        r = client.post(_URL, json={"repo_id": _REPO_ID, "user_id": _USER_ID})
        assert r.status_code == 204

    def test_calls_identity_client_invalidate(self, client):
        with patch("app.services.identity_client.invalidate") as mock_invalidate:
            client.post(_URL, json={"repo_id": _REPO_ID, "user_id": _USER_ID})
            mock_invalidate.assert_called_once_with(_REPO_ID, _USER_ID)

    def test_missing_repo_id_returns_422(self, client):
        assert client.post(_URL, json={"user_id": _USER_ID}).status_code == 422

    def test_missing_user_id_returns_422(self, client):
        assert client.post(_URL, json={"repo_id": _REPO_ID}).status_code == 422

    def test_invalid_uuid_repo_id_returns_422(self, client):
        assert client.post(_URL, json={"repo_id": "not-a-uuid", "user_id": _USER_ID}).status_code == 422
