"""
Tests for GET /health — readiness probe.

Coverage:
  - 200 + all checks ok when DB and EFS are healthy
  - 503 + rds: error when DB is down (patched engine)
  - 503 + efs: error when EFS is unwritable (read-only tmp dir)
  - No auth required
"""
from unittest.mock import patch

from sqlalchemy.exc import OperationalError


class TestHealthSuccess:
    def test_returns_200(self, client):
        assert client.get("/health").status_code == 200

    def test_both_checks_ok(self, client):
        data = client.get("/health").json()
        assert data["status"] == "ok"
        assert data["checks"]["rds"] == "ok"
        assert data["checks"]["efs"] == "ok"

    def test_no_auth_required(self, client):
        # /health should be accessible without a token
        assert client.get("/health").status_code == 200


class TestHealthDegraded:
    def test_rds_error_returns_503(self, client):
        def _bad_connect():
            raise OperationalError("connection refused", None, None)

        with patch("app.main.engine") as mock_engine:
            mock_engine.connect.side_effect = _bad_connect
            r = client.get("/health")
        assert r.status_code == 503
        assert r.json()["checks"]["rds"] == "error"

    def test_efs_error_returns_503(self, client):
        # Patch Path.touch to raise OSError — avoids relying on chmod (broken on Windows)
        with patch("pathlib.Path.touch", side_effect=OSError("EFS unavailable")):
            r = client.get("/health")
        assert r.status_code == 503
        assert r.json()["checks"]["efs"] == "error"

    def test_degraded_status_string(self, client):
        with patch("app.main.engine") as mock_engine:
            mock_engine.connect.side_effect = OperationalError("fail", None, None)
            r = client.get("/health")
        assert r.json()["status"] == "degraded"
