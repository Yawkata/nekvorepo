"""
Tests for POST /v1/auth/refresh

Flow:
  1. CognitoService.refresh_session(refresh_token, email) → {"IdToken": "..."}
  2. verify_cognito_token(IdToken) → {"sub": "..."}
  3. Mint and return a new Passport JWT

No database reads. verify_cognito_token patched at the auth.py usage site.

Coverage:
  Happy path   — 200, new access_token, correct passport claims
  Cognito errors — 401 expired/invalid refresh token, 401 user not found
  Validation   — 422 missing fields
"""

from unittest.mock import patch

import jwt

_URL = "/v1/auth/refresh"
_PATCH = "app.api.v1.endpoints.auth.verify_cognito_token"
_TEST_SECRET = "a" * 32
_FAKE_ID_TOKEN = "header.payload.sig"


def _decode(token: str) -> dict:
    return jwt.decode(
        token,
        _TEST_SECRET,
        algorithms=["HS256"],
        audience="internal-microservices",
        options={"verify_iss": False},
    )


class TestRefreshSuccess:
    _PAYLOAD = {"refresh_token": "opaque-cognito-refresh-token", "email": "alice@example.com"}

    def _do_refresh(self, client, mock_cognito, sub="alice-sub"):
        mock_cognito.refresh_session.return_value = {"IdToken": _FAKE_ID_TOKEN}
        with patch(_PATCH) as mv:
            mv.return_value = {"sub": sub}
            return client.post(_URL, json=self._PAYLOAD)

    def test_returns_200(self, client, mock_cognito):
        assert self._do_refresh(client, mock_cognito).status_code == 200

    def test_returns_new_access_token(self, client, mock_cognito):
        r = self._do_refresh(client, mock_cognito)
        assert "access_token" in r.json()

    def test_token_type_bearer(self, client, mock_cognito):
        r = self._do_refresh(client, mock_cognito)
        assert r.json()["token_type"] == "bearer"

    def test_no_refresh_token_in_response(self, client, mock_cognito):
        """Cognito does not issue a new refresh token on refresh."""
        r = self._do_refresh(client, mock_cognito)
        assert r.json().get("refresh_token") is None

    def test_passport_sub_from_cognito(self, client, mock_cognito):
        r = self._do_refresh(client, mock_cognito, sub="alice-sub-123")
        payload = _decode(r.json()["access_token"])
        assert payload["sub"] == "alice-sub-123"

    def test_passport_email_from_request(self, client, mock_cognito):
        r = self._do_refresh(client, mock_cognito)
        payload = _decode(r.json()["access_token"])
        assert payload["email"] == "alice@example.com"

    def test_passport_audience(self, client, mock_cognito):
        r = self._do_refresh(client, mock_cognito)
        payload = _decode(r.json()["access_token"])
        assert payload["aud"] == "internal-microservices"

    def test_cognito_called_with_correct_args(self, client, mock_cognito):
        self._do_refresh(client, mock_cognito)
        mock_cognito.refresh_session.assert_called_once_with(
            "opaque-cognito-refresh-token", "alice@example.com"
        )


class TestRefreshCognitoErrors:
    _PAYLOAD = {"refresh_token": "expired-token", "email": "alice@example.com"}

    def test_expired_refresh_token_returns_401(self, client, mock_cognito):
        from fastapi import HTTPException
        mock_cognito.refresh_session.side_effect = HTTPException(401, "Refresh token is invalid or expired")
        assert client.post(_URL, json=self._PAYLOAD).status_code == 401

    def test_user_not_found_returns_401(self, client, mock_cognito):
        from fastapi import HTTPException
        mock_cognito.refresh_session.side_effect = HTTPException(401, "User not found")
        assert client.post(_URL, json=self._PAYLOAD).status_code == 401

    def test_invalid_id_token_from_cognito_returns_401(self, client, mock_cognito):
        from fastapi import HTTPException
        mock_cognito.refresh_session.return_value = {"IdToken": "bad"}
        with patch(_PATCH) as mv:
            mv.side_effect = HTTPException(401, "Invalid or expired Cognito token.")
            r = client.post(_URL, json=self._PAYLOAD)
        assert r.status_code == 401


class TestRefreshValidation:
    def test_missing_refresh_token_422(self, client):
        assert client.post(_URL, json={"email": "alice@example.com"}).status_code == 422

    def test_missing_email_422(self, client):
        assert client.post(_URL, json={"refresh_token": "tok"}).status_code == 422

    def test_invalid_email_422(self, client):
        assert client.post(_URL, json={"refresh_token": "tok", "email": "bad"}).status_code == 422

    def test_empty_body_422(self, client):
        assert client.post(_URL, json={}).status_code == 422
