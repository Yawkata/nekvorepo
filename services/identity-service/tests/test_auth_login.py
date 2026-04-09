"""
Tests for POST /v1/auth/login

Flow:
  1. CognitoService.login(email, password) → {"IdToken": "...", "RefreshToken": "..."}
  2. verify_cognito_token(IdToken) → {"sub": "<cognito-sub>"}
  3. create_passport_token(user_id, email) → signed HS256 JWT
  4. Return Token(access_token, token_type="bearer", refresh_token)

verify_cognito_token is patched at the usage site in auth.py.
No database reads — the new passport design omits permissions from the JWT.

Coverage:
  Happy path   — 200, token shape, passport claims, refresh_token forwarded
  Passport JWT — sub, email, iss, aud, exp claims present
  Cognito errors — 401 bad credentials, 403 unconfirmed, 401 invalid IdToken
  Validation   — 422 on missing / malformed fields
"""

from unittest.mock import patch

import jwt

_URL = "/v1/auth/login"
_CREDS = {"email": "alice@example.com", "password": "SecurePass123!"}
_FAKE_ID_TOKEN = "header.payload.signature"
_PATCH = "app.api.v1.endpoints.auth.verify_cognito_token"
_TEST_SECRET = "a" * 32


def _decode(token: str) -> dict:
    return jwt.decode(
        token,
        _TEST_SECRET,
        algorithms=["HS256"],
        audience="internal-microservices",
        options={"verify_iss": False},
    )


class TestLoginSuccess:
    def _login(self, client, mock_cognito, sub="user-sub-123", refresh="rt-opaque"):
        mock_cognito.login.return_value = {"IdToken": _FAKE_ID_TOKEN, "RefreshToken": refresh}
        with patch(_PATCH) as mv:
            mv.return_value = {"sub": sub}
            return client.post(_URL, json=_CREDS)

    def test_returns_200(self, client, mock_cognito):
        assert self._login(client, mock_cognito).status_code == 200

    def test_has_access_token(self, client, mock_cognito):
        assert "access_token" in self._login(client, mock_cognito).json()

    def test_token_type_bearer(self, client, mock_cognito):
        assert self._login(client, mock_cognito).json()["token_type"] == "bearer"

    def test_refresh_token_forwarded(self, client, mock_cognito):
        r = self._login(client, mock_cognito, refresh="my-refresh-token")
        assert r.json()["refresh_token"] == "my-refresh-token"

    def test_refresh_token_absent_when_cognito_omits_it(self, client, mock_cognito):
        """Cognito may omit RefreshToken on silent refresh flows."""
        mock_cognito.login.return_value = {"IdToken": _FAKE_ID_TOKEN}
        with patch(_PATCH) as mv:
            mv.return_value = {"sub": "sub"}
            r = client.post(_URL, json=_CREDS)
        assert r.json().get("refresh_token") is None

    def test_cognito_called_with_correct_args(self, client, mock_cognito):
        self._login(client, mock_cognito)
        mock_cognito.login.assert_called_once_with("alice@example.com", "SecurePass123!")


class TestLoginPassportClaims:
    def _get_passport(self, client, mock_cognito, sub="test-sub") -> dict:
        mock_cognito.login.return_value = {"IdToken": _FAKE_ID_TOKEN}
        with patch(_PATCH) as mv:
            mv.return_value = {"sub": sub}
            r = client.post(_URL, json=_CREDS)
        return _decode(r.json()["access_token"])

    def test_sub_matches_cognito_sub(self, client, mock_cognito):
        payload = self._get_passport(client, mock_cognito, sub="alice-cognito-sub")
        assert payload["sub"] == "alice-cognito-sub"

    def test_email_matches_login_email(self, client, mock_cognito):
        payload = self._get_passport(client, mock_cognito)
        assert payload["email"] == "alice@example.com"

    def test_issuer_is_identity_service(self, client, mock_cognito):
        payload = self._get_passport(client, mock_cognito)
        assert payload["iss"] == "identity-service"

    def test_audience_is_internal_microservices(self, client, mock_cognito):
        payload = self._get_passport(client, mock_cognito)
        assert payload["aud"] == "internal-microservices"

    def test_exp_claim_present(self, client, mock_cognito):
        payload = self._get_passport(client, mock_cognito)
        assert "exp" in payload

    def test_no_permissions_in_passport(self, client, mock_cognito):
        """Permissions are not embedded in the JWT — callers query the role endpoint."""
        payload = self._get_passport(client, mock_cognito)
        assert "permissions" not in payload


class TestLoginCognitoErrors:
    def test_bad_credentials_returns_401(self, client, mock_cognito):
        from fastapi import HTTPException
        mock_cognito.login.side_effect = HTTPException(401, "Invalid email or password")
        assert client.post(_URL, json=_CREDS).status_code == 401

    def test_unconfirmed_account_returns_403(self, client, mock_cognito):
        from fastapi import HTTPException
        mock_cognito.login.side_effect = HTTPException(403, "Account not confirmed.")
        assert client.post(_URL, json=_CREDS).status_code == 403

    def test_invalid_id_token_returns_401(self, client, mock_cognito):
        from fastapi import HTTPException
        mock_cognito.login.return_value = {"IdToken": "tampered"}
        with patch(_PATCH) as mv:
            mv.side_effect = HTTPException(401, "Invalid or expired Cognito token.")
            r = client.post(_URL, json=_CREDS)
        assert r.status_code == 401


class TestLoginValidation:
    def test_missing_email_422(self, client):
        assert client.post(_URL, json={"password": "Pass123!"}).status_code == 422

    def test_invalid_email_422(self, client):
        assert client.post(_URL, json={"email": "bad", "password": "Pass123!"}).status_code == 422

    def test_missing_password_422(self, client):
        assert client.post(_URL, json={"email": "alice@example.com"}).status_code == 422

    def test_empty_body_422(self, client):
        assert client.post(_URL, json={}).status_code == 422

    def test_cognito_not_called_on_validation_failure(self, client, mock_cognito):
        client.post(_URL, json={"email": "bad"})
        mock_cognito.login.assert_not_called()
