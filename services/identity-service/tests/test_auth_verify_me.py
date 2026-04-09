"""
Tests for GET /v1/auth/verify-me

Protected by Security(verify_passport).
Returns {"status": "verified", "data": TokenData} — TokenData has user_id and email only.
No database access, no Cognito calls.

Coverage:
  Happy path   — 200, body shape, user_id, email
  Auth failures — no header 403, malformed 401, expired 401, wrong issuer 401,
                  wrong secret 401, wrong audience 401, wrong scheme 403
"""

_URL = "/v1/auth/verify-me"


class TestVerifyMeSuccess:
    def test_returns_200(self, client, make_passport):
        r = client.get(_URL, headers={"Authorization": f"Bearer {make_passport()}"})
        assert r.status_code == 200

    def test_status_is_verified(self, client, make_passport):
        r = client.get(_URL, headers={"Authorization": f"Bearer {make_passport()}"})
        assert r.json()["status"] == "verified"

    def test_data_field_present(self, client, make_passport):
        r = client.get(_URL, headers={"Authorization": f"Bearer {make_passport()}"})
        assert "data" in r.json()

    def test_data_contains_user_id(self, client, make_passport):
        token = make_passport(user_id="alice-sub")
        r = client.get(_URL, headers={"Authorization": f"Bearer {token}"})
        assert r.json()["data"]["user_id"] == "alice-sub"

    def test_data_contains_email(self, client, make_passport):
        token = make_passport(email="alice@example.com")
        r = client.get(_URL, headers={"Authorization": f"Bearer {token}"})
        assert r.json()["data"]["email"] == "alice@example.com"

    def test_no_permissions_in_data(self, client, make_passport):
        """Permissions are not in the JWT — confirm the field is absent."""
        r = client.get(_URL, headers={"Authorization": f"Bearer {make_passport()}"})
        assert "permissions" not in r.json()["data"]

    def test_no_db_access_required(self, client, make_passport):
        """Endpoint must succeed purely from the JWT — no DB round-trip."""
        r = client.get(_URL, headers={"Authorization": f"Bearer {make_passport()}"})
        assert r.status_code == 200


class TestVerifyMeAuthFailures:
    def test_no_header_returns_401(self, client):
        assert client.get(_URL).status_code == 401

    def test_malformed_token_returns_401(self, client):
        r = client.get(_URL, headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401

    def test_random_string_returns_401(self, client):
        r = client.get(_URL, headers={"Authorization": "Bearer randomgarbage"})
        assert r.status_code == 401

    def test_expired_token_returns_401(self, client, make_passport):
        token = make_passport(expired=True)
        r = client.get(_URL, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_wrong_issuer_returns_401(self, client, make_passport):
        token = make_passport(wrong_issuer=True)
        r = client.get(_URL, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_wrong_secret_returns_401(self, client, make_passport):
        token = make_passport(wrong_secret=True)
        r = client.get(_URL, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_wrong_audience_returns_401(self, client, make_passport):
        token = make_passport(wrong_audience=True)
        r = client.get(_URL, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_basic_scheme_returns_401(self, client, make_passport):
        """HTTPBearer rejects non-Bearer auth schemes with 401."""
        token = make_passport()
        r = client.get(_URL, headers={"Authorization": f"Basic {token}"})
        assert r.status_code == 401
