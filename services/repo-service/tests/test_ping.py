"""Tests for GET /ping — liveness probe."""


class TestPing:
    def test_returns_200(self, client):
        assert client.get("/ping").status_code == 200

    def test_returns_status_ok(self, client):
        assert client.get("/ping").json() == {"status": "ok"}

    def test_no_auth_required(self, client):
        """Liveness probe must be reachable without a JWT."""
        assert client.get("/ping").status_code == 200

    def test_content_type_json(self, client):
        r = client.get("/ping")
        assert "application/json" in r.headers["content-type"]
