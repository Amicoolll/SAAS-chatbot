"""search operation tests — embeddings + DB are mocked."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from catapult_adapter.service.main import app


client = TestClient(app)


def _stub_session(rows: list[tuple]) -> MagicMock:
    """A SessionLocal() replacement whose .execute().fetchall() returns rows."""
    session = MagicMock()
    session.execute.return_value.fetchall.return_value = rows
    return session


def test_search_vector_path_returns_results(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.embed_texts",
        lambda texts, **_kw: [[0.0] * 1536 for _ in texts],
    )
    fake_session = _stub_session(
        [
            ("Refunds are processed within 5 days.", "refund-policy.md", 0.12),
            ("Returns require a receipt.", "returns.md", 0.34),
        ]
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.SessionLocal",
        lambda: fake_session,
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.settings.ENABLE_HYBRID_RAG",
        False,
    )

    r = client.post(
        "/tools/rag-chatbot/search",
        json={"query": "refund policy", "collection_id": "hr-docs", "top_k": 5},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["retrieve_mode"] == "vector"
    assert body["total_matches"] == 2
    assert body["results"][0]["chunk_text"].startswith("Refunds")
    assert body["results"][0]["document_name"] == "refund-policy.md"
    assert body["results"][0]["distance"] == pytest.approx(0.12)


def test_search_use_hybrid_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.embed_texts",
        lambda texts, **_kw: [[0.0] * 1536 for _ in texts],
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.SessionLocal",
        lambda: MagicMock(),
    )
    captured: dict = {}

    def fake_hybrid(db, **kwargs):
        captured.update(kwargs)
        return ([("hybrid chunk", "hybrid-doc.md", 0.05)], True)

    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.retrieve_hybrid_chunks",
        fake_hybrid,
    )

    r = client.post(
        "/tools/rag-chatbot/search",
        json={"query": "anything", "collection_id": "hr-docs", "use_hybrid": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["retrieve_mode"] == "hybrid"
    assert body["results"][0]["document_name"] == "hybrid-doc.md"
    assert captured["user_id"] == "hr-docs"
    assert captured["question"] == "anything"


def test_search_embed_failure_returns_503(monkeypatch: pytest.MonkeyPatch):
    def boom(_texts, **_kw):
        raise RuntimeError("openai down")

    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.embed_texts", boom
    )
    r = client.post(
        "/tools/rag-chatbot/search",
        json={"query": "x", "collection_id": "hr-docs"},
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_search_invalid_collection_id_rejected():
    r = client.post(
        "/tools/rag-chatbot/search",
        json={"query": "x", "collection_id": "has spaces"},
    )
    assert r.status_code == 422  # FastAPI/Pydantic validation
