"""
Tests for GET /health — Kubernetes readiness probe.

Coverage:
  Healthy   — 200, status ok, rds check ok
  Degraded  — 503, status degraded, rds check error (patched engine)
  Auth      — no token required on either case
"""

from unittest.mock import patch, MagicMock
from sqlalchemy.exc import OperationalError


class TestHealth:
    def test_no_auth_required(self, client):
        """Health probe must be reachable without a JWT (used by Kubernetes)."""
        assert client.get("/health").status_code == 200

    def test_returns_200_when_db_up(self, client):
        assert client.get("/health").status_code == 200

    def test_body_when_healthy(self, client):
        r = client.get("/health").json()
        assert r["status"] == "ok"
        assert r["checks"]["rds"] == "ok"

    def test_returns_503_when_db_down(self, client):
        with patch("app.main.engine") as mock_engine:
            mock_engine.connect.side_effect = OperationalError("conn", {}, Exception())
            assert client.get("/health").status_code == 503

    def test_body_when_degraded(self, client):
        with patch("app.main.engine") as mock_engine:
            mock_engine.connect.side_effect = OperationalError("conn", {}, Exception())
            r = client.get("/health").json()
            assert r["status"] == "degraded"
            assert r["checks"]["rds"] == "error"
