"""
Tests for GET /health — Kubernetes readiness probe.

The endpoint performs two live checks:
  - RDS: SELECT 1 via the module-level engine
  - Cognito JWKS: HTTP GET the JWKS URL

Both are patched in tests to avoid network I/O.
app.main.engine is replaced with the real testcontainers engine
so the DB check actually exercises a live PostgreSQL connection.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch


_ENGINE_PATCH = "app.main.engine"
_URLOPEN_PATCH = "app.main.urllib.request.urlopen"


@contextmanager
def _urlopen_ok():
    with patch(_URLOPEN_PATCH, return_value=MagicMock()):
        yield


@contextmanager
def _urlopen_fail():
    with patch(_URLOPEN_PATCH, side_effect=Exception("Network error")):
        yield


class TestHealthAllOk:
    def test_returns_200(self, client, db_engine):
        with patch(_ENGINE_PATCH, db_engine), _urlopen_ok():
            r = client.get("/health")
        assert r.status_code == 200

    def test_status_ok(self, client, db_engine):
        with patch(_ENGINE_PATCH, db_engine), _urlopen_ok():
            r = client.get("/health")
        assert r.json()["status"] == "ok"

    def test_rds_check_passes(self, client, db_engine):
        with patch(_ENGINE_PATCH, db_engine), _urlopen_ok():
            r = client.get("/health")
        assert r.json()["checks"]["rds"] == "ok"

    def test_cognito_jwks_check_passes(self, client, db_engine):
        with patch(_ENGINE_PATCH, db_engine), _urlopen_ok():
            r = client.get("/health")
        assert r.json()["checks"]["cognito_jwks"] == "ok"

    def test_no_auth_required(self, client, db_engine):
        with patch(_ENGINE_PATCH, db_engine), _urlopen_ok():
            r = client.get("/health")
        assert r.status_code not in (401, 403)


class TestHealthDegraded:
    def test_503_when_cognito_fails(self, client, db_engine):
        with patch(_ENGINE_PATCH, db_engine), _urlopen_fail():
            r = client.get("/health")
        assert r.status_code == 503

    def test_status_degraded_when_cognito_fails(self, client, db_engine):
        with patch(_ENGINE_PATCH, db_engine), _urlopen_fail():
            r = client.get("/health")
        assert r.json()["status"] == "degraded"

    def test_rds_ok_cognito_error(self, client, db_engine):
        with patch(_ENGINE_PATCH, db_engine), _urlopen_fail():
            r = client.get("/health")
        assert r.json()["checks"]["rds"] == "ok"
        assert r.json()["checks"]["cognito_jwks"] == "error"

    def test_503_when_rds_fails(self, client):
        broken = MagicMock()
        broken.connect.side_effect = Exception("DB down")
        with patch(_ENGINE_PATCH, broken), _urlopen_ok():
            r = client.get("/health")
        assert r.status_code == 503

    def test_rds_error_cognito_ok(self, client):
        broken = MagicMock()
        broken.connect.side_effect = Exception("DB down")
        with patch(_ENGINE_PATCH, broken), _urlopen_ok():
            r = client.get("/health")
        assert r.json()["checks"]["rds"] == "error"
        assert r.json()["checks"]["cognito_jwks"] == "ok"

    def test_both_checks_fail(self, client):
        broken = MagicMock()
        broken.connect.side_effect = Exception("DB down")
        with patch(_ENGINE_PATCH, broken), _urlopen_fail():
            r = client.get("/health")
        assert r.status_code == 503
        assert r.json()["checks"]["rds"] == "error"
        assert r.json()["checks"]["cognito_jwks"] == "error"
