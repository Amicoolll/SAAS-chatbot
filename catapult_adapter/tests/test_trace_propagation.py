"""Verify Catapult §7.3 downstream-trace requirement.

The submission guide §7.3 requires:
    "Propagate x-catapult-trace-id to any downstream calls."

These tests assert that when the request carries ``x-catapult-trace-id``,
the trace header reaches the underlying ``embed_texts``, chat, and
``search_web`` calls; and that when no trace id is present, no trace
header is added (so in-app callers' behavior remains unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from catapult_adapter.service.headers import CatapultContext, trace_headers
from catapult_adapter.service.main import app


client = TestClient(app)


# ------- pure helper ----------------------------------------------------


def test_trace_headers_helper_returns_none_without_ids():
    ctx = CatapultContext(
        tenant_id="t", user_id="u", request_id="", trace_id=None, app_id=None
    )
    assert trace_headers(ctx) is None


def test_trace_headers_helper_includes_present_ids_only():
    ctx = CatapultContext(
        tenant_id="t",
        user_id="u",
        request_id="req-1",
        trace_id="trace-1",
        app_id=None,
    )
    h = trace_headers(ctx)
    assert h == {"X-Trace-Id": "trace-1", "X-Request-Id": "req-1"}


def test_trace_headers_helper_with_only_request_id():
    ctx = CatapultContext(
        tenant_id="t", user_id="u", request_id="req-only", trace_id=None, app_id=None
    )
    assert trace_headers(ctx) == {"X-Request-Id": "req-only"}


# ------- search: embed_texts receives trace_headers --------------------


def test_search_propagates_trace_to_embed(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_embed(texts, **kwargs):
        captured.update(kwargs)
        return [[0.0] * 1536 for _ in texts]

    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.embed_texts", fake_embed
    )
    fake_session = MagicMock()
    fake_session.execute.return_value.fetchall.return_value = []
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
        json={"query": "x", "collection_id": "hr-docs"},
        headers={
            "x-catapult-request-id": "req-1",
            "x-catapult-trace-id": "trace-1",
        },
    )
    assert r.status_code == 200
    assert captured.get("trace_headers") == {
        "X-Trace-Id": "trace-1",
        "X-Request-Id": "req-1",
    }


def test_search_no_trace_headers_when_no_catapult_ids(
    monkeypatch: pytest.MonkeyPatch,
):
    """Without Catapult headers, trace_headers must be None — preserves
    backward-compat behavior of the underlying embed_texts call."""
    captured: dict = {}

    def fake_embed(texts, **kwargs):
        captured.update(kwargs)
        return [[0.0] * 1536 for _ in texts]

    monkeypatch.setattr(
        "catapult_adapter.service.operations.search.embed_texts", fake_embed
    )
    fake_session = MagicMock()
    fake_session.execute.return_value.fetchall.return_value = []
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
        json={"query": "x", "collection_id": "hr-docs"},
    )
    assert r.status_code == 200
    assert captured.get("trace_headers") is None


# ------- ask: trace flows into embed + chat ----------------------------


def test_ask_propagates_trace_to_embed_and_chat(
    monkeypatch: pytest.MonkeyPatch,
):
    embed_kwargs: dict = {}
    chat_kwargs: dict = {}

    def fake_embed(texts, **kwargs):
        embed_kwargs.update(kwargs)
        return [[0.0] * 1536 for _ in texts]

    def fake_chat(question, chunks, agent_type, history, **kwargs):
        chat_kwargs.update(kwargs)
        return "answer"

    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.embed_texts", fake_embed
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.chat_with_context", fake_chat
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.settings.ENABLE_HYBRID_RAG", False
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.settings.RAG_DISTANCE_THRESHOLD",
        0.45,
    )
    fake_session = MagicMock()
    fake_session.execute.return_value.fetchall.return_value = [
        ("chunk", "doc.md", 0.10)
    ]
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.SessionLocal", lambda: fake_session
    )

    r = client.post(
        "/tools/rag-chatbot/ask",
        json={"question": "policy?", "collection_id": "hr-docs"},
        headers={
            "x-catapult-request-id": "req-2",
            "x-catapult-trace-id": "trace-2",
        },
    )
    assert r.status_code == 200, r.text
    expected = {"X-Trace-Id": "trace-2", "X-Request-Id": "req-2"}
    assert embed_kwargs.get("trace_headers") == expected
    assert chat_kwargs.get("trace_headers") == expected


# ------- ask: web fallback path propagates to search_web AND chat ------


@dataclass(frozen=True)
class _FakeWebResult:
    title: str
    url: str
    snippet: str


def test_ask_web_fallback_propagates_trace_to_search_web_and_chat(
    monkeypatch: pytest.MonkeyPatch,
):
    search_web_kwargs: dict = {}
    web_chat_kwargs: dict = {}

    def fake_search_web(query, **kwargs):
        search_web_kwargs.update(kwargs)
        return [_FakeWebResult("title", "https://example.com", "snippet")]

    def fake_web_chat(question, web_results, agent_type, history, **kwargs):
        web_chat_kwargs.update(kwargs)
        return "from web"

    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.embed_texts",
        lambda texts, **_kw: [[0.0] * 1536 for _ in texts],
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.search_web", fake_search_web
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.chat_with_web_context",
        fake_web_chat,
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.settings.ENABLE_HYBRID_RAG",
        False,
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.settings.RAG_DISTANCE_THRESHOLD",
        0.45,
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.settings.TAVILY_API_KEY",
        "fake-key",
    )
    # KB rows above threshold → fallback path is taken.
    fake_session = MagicMock()
    fake_session.execute.return_value.fetchall.return_value = [
        ("irrelevant", "old.md", 0.9)
    ]
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.SessionLocal", lambda: fake_session
    )

    r = client.post(
        "/tools/rag-chatbot/ask",
        json={"question": "anything", "collection_id": "hr-docs"},
        headers={
            "x-catapult-request-id": "req-w",
            "x-catapult-trace-id": "trace-w",
        },
    )
    assert r.status_code == 200, r.text
    expected = {"X-Trace-Id": "trace-w", "X-Request-Id": "req-w"}
    assert search_web_kwargs.get("trace_headers") == expected
    assert web_chat_kwargs.get("trace_headers") == expected


# ------- ingest: trace flows into embed --------------------------------


def test_ingest_propagates_trace_to_embed(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_embed(texts, **kwargs):
        captured.update(kwargs)
        return [[0.0] * 1536 for _ in texts]

    monkeypatch.setattr(
        "catapult_adapter.service.operations.ingest.embed_texts", fake_embed
    )
    fake_session = MagicMock()
    qr = MagicMock()
    qr.first.return_value = None
    fr = MagicMock()
    fr.first.return_value = None
    qr.filter.return_value = fr
    fake_session.query.return_value = qr
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ingest.SessionLocal",
        lambda: fake_session,
    )

    r = client.post(
        "/tools/rag-chatbot/ingest",
        json={
            "collection_id": "hr-docs",
            "documents": [{"document_id": "d1", "content": "hello"}],
        },
        headers={
            "x-catapult-request-id": "req-i",
            "x-catapult-trace-id": "trace-i",
        },
    )
    assert r.status_code == 200, r.text
    assert captured.get("trace_headers") == {
        "X-Trace-Id": "trace-i",
        "X-Request-Id": "req-i",
    }
