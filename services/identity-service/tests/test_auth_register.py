"""
Tests for POST /v1/auth/register

Flow: validate payload → CognitoService.register_user() → 201 MessageResponse
No database writes occur here; user creation is Cognito-owned.

Coverage:
  Happy path   — 201, correct Cognito call, message field present
  Validation   — missing/malformed fields → 422
  Cognito errors — duplicate email → 400, weak password → 400, unexpected → 500
"""

from fastapi import HTTPException


_URL = "/v1/auth/register"
_VALID = {"email": "alice@example.com", "password": "SecurePass123!", "full_name": "Alice Smith"}


class TestRegisterSuccess:
    def test_returns_201(self, client, mock_cognito):
        mock_cognito.register_user.return_value = {"message": "Registration successful. Please check your email for the verification code."}
        assert client.post(_URL, json=_VALID).status_code == 201

    def test_response_has_message_field(self, client, mock_cognito):
        mock_cognito.register_user.return_value = {"message": "Registration successful. Please check your email for the verification code."}
        r = client.post(_URL, json=_VALID)
        assert "message" in r.json()

    def test_cognito_called_with_correct_args(self, client, mock_cognito):
        mock_cognito.register_user.return_value = {"message": "ok"}
        client.post(_URL, json=_VALID)
        mock_cognito.register_user.assert_called_once_with(
            "alice@example.com", "SecurePass123!", "Alice Smith"
        )

    def test_email_normalised_to_lowercase(self, client, mock_cognito):
        """Pydantic EmailStr normalises to lowercase before Cognito receives it."""
        mock_cognito.register_user.return_value = {"message": "ok"}
        client.post(_URL, json={**_VALID, "email": "alice@EXAMPLE.COM"})
        args = mock_cognito.register_user.call_args[0]
        assert args[0] == "alice@example.com"

    def test_extra_fields_are_stripped(self, client, mock_cognito):
        mock_cognito.register_user.return_value = {"message": "ok"}
        r = client.post(_URL, json={**_VALID, "unknown": "ignored"})
        assert r.status_code == 201


class TestRegisterValidation:
    def test_missing_email_422(self, client):
        r = client.post(_URL, json={"password": "Pass123!", "full_name": "Bob"})
        assert r.status_code == 422

    def test_invalid_email_format_422(self, client):
        r = client.post(_URL, json={"email": "not-an-email", "password": "Pass123!", "full_name": "Bob"})
        assert r.status_code == 422

    def test_empty_email_422(self, client):
        r = client.post(_URL, json={"email": "", "password": "Pass123!", "full_name": "Bob"})
        assert r.status_code == 422

    def test_missing_password_422(self, client):
        r = client.post(_URL, json={"email": "bob@example.com", "full_name": "Bob"})
        assert r.status_code == 422

    def test_missing_full_name_422(self, client):
        r = client.post(_URL, json={"email": "bob@example.com", "password": "Pass123!"})
        assert r.status_code == 422

    def test_empty_body_422(self, client):
        r = client.post(_URL, json={})
        assert r.status_code == 422

    def test_malformed_json_422(self, client):
        r = client.post(_URL, content="not json", headers={"Content-Type": "application/json"})
        assert r.status_code == 422

    def test_cognito_not_called_on_invalid_payload(self, client, mock_cognito):
        client.post(_URL, json={"email": "bad"})
        mock_cognito.register_user.assert_not_called()


class TestRegisterCognitoErrors:
    def test_duplicate_email_returns_400(self, client, mock_cognito):
        mock_cognito.register_user.side_effect = HTTPException(400, "Email already registered.")
        r = client.post(_URL, json=_VALID)
        assert r.status_code == 400

    def test_duplicate_email_detail(self, client, mock_cognito):
        mock_cognito.register_user.side_effect = HTTPException(400, "Email already registered.")
        r = client.post(_URL, json=_VALID)
        assert "already registered" in r.json()["detail"]

    def test_weak_password_returns_400(self, client, mock_cognito):
        mock_cognito.register_user.side_effect = HTTPException(
            400, "Password does not meet requirements (min 8 chars, uppercase, number, symbol)."
        )
        r = client.post(_URL, json=_VALID)
        assert r.status_code == 400

    def test_unexpected_error_returns_500(self, client, mock_cognito):
        mock_cognito.register_user.side_effect = HTTPException(500, "Registration failed.")
        r = client.post(_URL, json=_VALID)
        assert r.status_code == 500
