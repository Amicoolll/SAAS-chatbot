"""Health endpoint tests — no DB, no network.

The readiness probe does touch SessionLocal in production, so we patch it
to return a fake session whose ``execute`` succeeds.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from catapult_adapter.service.main import app


client = TestClient(app)


def test_status():
    r = client.get("/tools/rag-chatbot/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"


def test_liveness():
    r = client.get("/tools/rag-chatbot/health/live")
    assert r.status_code == 200
    assert r.json() == {"alive": True}


def test_readiness_ok(monkeypatch: pytest.MonkeyPatch):
    fake_db = MagicMock()
    fake_db.execute.return_value = None
    fake_db.close.return_value = None
    monkeypatch.setattr(
        "app.db.session.SessionLocal",
        lambda: fake_db,
    )
    r = client.get("/tools/rag-chatbot/health/ready")
    assert r.status_code == 200
    assert r.json() == {"ready": True}


def test_readiness_db_down_returns_503(monkeypatch: pytest.MonkeyPatch):
    fake_db = MagicMock()
    fake_db.execute.side_effect = RuntimeError("conn refused")
    monkeypatch.setattr(
        "app.db.session.SessionLocal",
        lambda: fake_db,
    )
    r = client.get("/tools/rag-chatbot/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "conn refused" in body["error"]["message"]
