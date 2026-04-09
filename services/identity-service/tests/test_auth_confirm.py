"""
Tests for POST /v1/auth/confirm

Flow: validate payload → CognitoService.confirm_user() → 200 MessageResponse
No database access. confirm_user() raises HTTPException for all error cases.

Coverage:
  Happy path   — 200, message field, cognito called correctly
  Validation   — missing/malformed fields → 422
  Cognito errors — code mismatch 400, expired code 400, already confirmed 400,
                   user not found 404, rate limited 429
"""

from fastapi import HTTPException


_URL = "/v1/auth/confirm"
_VALID = {"email": "alice@example.com", "code": "123456"}


class TestConfirmSuccess:
    def test_returns_200(self, client, mock_cognito):
        mock_cognito.confirm_user.return_value = None  # returns None on success
        r = client.post(_URL, json=_VALID)
        assert r.status_code == 200

    def test_response_message(self, client, mock_cognito):
        mock_cognito.confirm_user.return_value = None
        r = client.post(_URL, json=_VALID)
        assert "message" in r.json()
        assert "confirmed" in r.json()["message"].lower()

    def test_cognito_called_with_correct_args(self, client, mock_cognito):
        mock_cognito.confirm_user.return_value = None
        client.post(_URL, json=_VALID)
        mock_cognito.confirm_user.assert_called_once_with("alice@example.com", "123456")

    def test_email_normalised_to_lowercase(self, client, mock_cognito):
        mock_cognito.confirm_user.return_value = None
        client.post(_URL, json={"email": "alice@EXAMPLE.COM", "code": "999999"})
        args = mock_cognito.confirm_user.call_args[0]
        assert args[0] == "alice@example.com"


class TestConfirmValidation:
    def test_missing_email_422(self, client):
        assert client.post(_URL, json={"code": "123456"}).status_code == 422

    def test_invalid_email_422(self, client):
        assert client.post(_URL, json={"email": "bad", "code": "123456"}).status_code == 422

    def test_missing_code_422(self, client):
        assert client.post(_URL, json={"email": "alice@example.com"}).status_code == 422

    def test_empty_body_422(self, client):
        assert client.post(_URL, json={}).status_code == 422


class TestConfirmCognitoErrors:
    def test_invalid_code_returns_400(self, client, mock_cognito):
        mock_cognito.confirm_user.side_effect = HTTPException(400, "Invalid confirmation code.")
        assert client.post(_URL, json=_VALID).status_code == 400

    def test_expired_code_returns_400(self, client, mock_cognito):
        mock_cognito.confirm_user.side_effect = HTTPException(400, "Confirmation code has expired.")
        r = client.post(_URL, json=_VALID)
        assert r.status_code == 400
        assert "expired" in r.json()["detail"].lower()

    def test_already_confirmed_returns_400(self, client, mock_cognito):
        mock_cognito.confirm_user.side_effect = HTTPException(400, "Account is already confirmed.")
        r = client.post(_URL, json=_VALID)
        assert r.status_code == 400

    def test_user_not_found_returns_404(self, client, mock_cognito):
        mock_cognito.confirm_user.side_effect = HTTPException(404, "User not found.")
        assert client.post(_URL, json=_VALID).status_code == 404

    def test_rate_limited_returns_429(self, client, mock_cognito):
        mock_cognito.confirm_user.side_effect = HTTPException(429, "Too many attempts.")
        assert client.post(_URL, json=_VALID).status_code == 429
