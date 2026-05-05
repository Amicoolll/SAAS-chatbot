"""ingest operation tests — embeddings + DB are mocked.

We don't exercise SQLAlchemy here (the chunk/document insert pattern is
already covered by the main app's tests). Instead we verify that the
adapter:
    - chunks each document
    - calls embed_texts with the chunk strings
    - reports per-document errors without aborting the whole batch
    - maps replaced documents into documents_replaced
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from catapult_adapter.service.main import app


client = TestClient(app)


def _make_session(existing_doc=None) -> MagicMock:
    """Return a SessionLocal()-like mock.

    ``existing_doc`` simulates the prior-Document lookup result. When it is
    None, the adapter takes the create-new branch; when it is an object,
    the adapter takes the replace-existing branch.
    """
    session = MagicMock()

    query_result = MagicMock()
    query_result.first.return_value = existing_doc
    filter_result = MagicMock()
    filter_result.first.return_value = existing_doc
    query_result.filter.return_value = filter_result
    session.query.return_value = query_result

    session.add.return_value = None
    session.execute.return_value = None
    session.commit.return_value = None
    session.refresh.return_value = None
    session.rollback.return_value = None
    session.close.return_value = None
    return session


def test_ingest_new_document(monkeypatch: pytest.MonkeyPatch):
    captured_embed: list[list[str]] = []

    def fake_embed(texts, **_kw):
        captured_embed.append(list(texts))
        return [[0.0] * 1536 for _ in texts]

    monkeypatch.setattr(
        "catapult_adapter.service.operations.ingest.embed_texts", fake_embed
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ingest.SessionLocal",
        lambda: _make_session(existing_doc=None),
    )

    r = client.post(
        "/tools/rag-chatbot/ingest",
        json={
            "collection_id": "hr-docs",
            "documents": [
                {
                    "document_id": "policy-001",
                    "content": "Refund policy text.",
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["collection_id"] == "hr-docs"
    assert body["documents_processed"] == 1
    assert body["chunks_created"] >= 1
    assert body["documents_replaced"] == 0
    assert body["errors"] == []
    # embed_texts was called at least once (once per document)
    assert captured_embed
    assert all(isinstance(t, str) for t in captured_embed[0])


def test_ingest_replaces_existing_document(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ingest.embed_texts",
        lambda texts, **_kw: [[0.0] * 1536 for _ in texts],
    )
    existing = SimpleNamespace(id="doc-uuid", name="old", mime_type="text/plain", modified_time="x")
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ingest.SessionLocal",
        lambda: _make_session(existing_doc=existing),
    )

    r = client.post(
        "/tools/rag-chatbot/ingest",
        json={
            "collection_id": "hr-docs",
            "documents": [
                {
                    "document_id": "policy-001",
                    "name": "Updated policy",
                    "content": "New policy text.",
                }
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["documents_replaced"] == 1
    assert body["documents_processed"] == 1


def test_ingest_per_document_failure_does_not_abort_batch(
    monkeypatch: pytest.MonkeyPatch,
):
    call_count = {"n": 0}

    def flaky_embed(texts, **_kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("embed transient")
        return [[0.0] * 1536 for _ in texts]

    monkeypatch.setattr(
        "catapult_adapter.service.operations.ingest.embed_texts", flaky_embed
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ingest.SessionLocal",
        lambda: _make_session(existing_doc=None),
    )

    r = client.post(
        "/tools/rag-chatbot/ingest",
        json={
            "collection_id": "hr-docs",
            "documents": [
                {"document_id": "doc-a", "content": "first"},
                {"document_id": "doc-b", "content": "second"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["documents_processed"] == 1  # second one succeeded
    assert len(body["errors"]) == 1
    assert body["errors"][0]["document_id"] == "doc-a"


def test_ingest_empty_content_field_rejected_by_validation():
    r = client.post(
        "/tools/rag-chatbot/ingest",
        json={
            "collection_id": "hr-docs",
            "documents": [{"document_id": "x", "content": ""}],
        },
    )
    assert r.status_code == 422


def test_ingest_too_many_documents_rejected():
    docs = [{"document_id": f"d-{i}", "content": "x"} for i in range(101)]
    r = client.post(
        "/tools/rag-chatbot/ingest",
        json={"collection_id": "hr-docs", "documents": docs},
    )
    assert r.status_code == 422
