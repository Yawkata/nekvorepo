"""
Tests for GET /ping — Kubernetes liveness probe.

Must always return 200 immediately with no dependency checks.
No auth, no DB, no external calls.
"""


class TestPing:
    def test_returns_200(self, client):
        assert client.get("/ping").status_code == 200

    def test_body_status_ok(self, client):
        assert client.get("/ping").json()["status"] == "ok"

    def test_no_auth_required(self, client):
        """Liveness probe must be reachable without credentials."""
        r = client.get("/ping")
        assert r.status_code not in (401, 403)

    def test_content_type_json(self, client):
        assert "application/json" in client.get("/ping").headers["content-type"]
