"""Verify Catapult §7.3 traceability requirement.

The submission guide §7.3 explicitly requires:
    "Read AND log x-catapult-request-id for traceability."

These tests assert that whichever operation runs, the resolved request_id
shows up in the captured log records — both on the entry/exit anchor lines
and on the failure paths.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from catapult_adapter.service.main import app


client = TestClient(app)


def _has_request_id(records: list[logging.LogRecord], request_id: str) -> bool:
    """Return True if any log record's formatted message contains the id."""
    return any(request_id in rec.getMessage() for rec in records)


def test_search_logs_request_id_on_success(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.embed_texts",
        lambda texts, **_kw: [[0.0] * 1536 for _ in texts],
    )
    fake_session = MagicMock()
    fake_session.execute.return_value.fetchall.return_value = [
        ("chunk", "doc.md", 0.1)
    ]
    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.SessionLocal",
        lambda: fake_session,
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.settings.ENABLE_HYBRID_RAG",
        False,
    )

    rid = "req-abc-123"
    with caplog.at_level(logging.INFO, logger="catapult_adapter.search"):
        r = client.post(
            "/tools/rag-chatbot/search",
            json={"query": "x", "collection_id": "hr-docs"},
            headers={
                "x-catapult-request-id": rid,
                "x-catapult-trace-id": "trace-xyz",
            },
        )
    assert r.status_code == 200
    # Both the started and ok anchor lines must include the request id.
    assert _has_request_id(caplog.records, rid)


def test_search_logs_request_id_on_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.embed_texts",
        lambda _texts, **_kw: (_ for _ in ()).throw(RuntimeError("openai down")),
    )

    rid = "req-fail-456"
    with caplog.at_level(logging.WARNING, logger="catapult_adapter.search"):
        r = client.post(
            "/tools/rag-chatbot/search",
            json={"query": "x", "collection_id": "hr-docs"},
            headers={"x-catapult-request-id": rid},
        )
    assert r.status_code == 503
    assert _has_request_id(caplog.records, rid)


def test_ask_logs_request_id_on_kb_grounded_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.embed_texts",
        lambda texts, **_kw: [[0.0] * 1536 for _ in texts],
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.settings.ENABLE_HYBRID_RAG",
        False,
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.settings.RAG_DISTANCE_THRESHOLD",
        0.45,
    )
    fake_session = MagicMock()
    fake_session.execute.return_value.fetchall.return_value = [
        ("chunk", "doc.md", 0.1)
    ]
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.SessionLocal",
        lambda: fake_session,
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.chat_with_context",
        lambda *a, **kw: "an answer",
    )

    rid = "req-ask-789"
    with caplog.at_level(logging.INFO, logger="catapult_adapter.ask"):
        r = client.post(
            "/tools/rag-chatbot/ask",
            json={"question": "What's the policy?", "collection_id": "hr-docs"},
            headers={"x-catapult-request-id": rid},
        )
    assert r.status_code == 200
    assert _has_request_id(caplog.records, rid)


def test_ingest_logs_request_id(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ingest.embed_texts",
        lambda texts, **_kw: [[0.0] * 1536 for _ in texts],
    )
    fake_session = MagicMock()
    query_result = MagicMock()
    query_result.first.return_value = None
    filter_result = MagicMock()
    filter_result.first.return_value = None
    query_result.filter.return_value = filter_result
    fake_session.query.return_value = query_result
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ingest.SessionLocal",
        lambda: fake_session,
    )

    rid = "req-ingest-321"
    with caplog.at_level(logging.INFO, logger="catapult_adapter.ingest"):
        r = client.post(
            "/tools/rag-chatbot/ingest",
            json={
                "collection_id": "hr-docs",
                "documents": [{"document_id": "d1", "content": "hello"}],
            },
            headers={"x-catapult-request-id": rid},
        )
    assert r.status_code == 200
    assert _has_request_id(caplog.records, rid)
