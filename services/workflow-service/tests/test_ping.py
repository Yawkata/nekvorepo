"""Tests for GET /ping — Kubernetes liveness probe."""


class TestPing:
    def test_returns_200(self, client):
        assert client.get("/ping").status_code == 200

    def test_body_status_ok(self, client):
        assert client.get("/ping").json() == {"status": "ok"}
