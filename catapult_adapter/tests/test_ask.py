"""ask operation tests — every external call (embed, chat, retrieval, web)
is replaced with a stub. Verifies orchestration, not OpenAI/Tavily behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from catapult_adapter.service.main import app


client = TestClient(app)


@dataclass(frozen=True)
class _FakeWebResult:
    title: str
    url: str
    snippet: str


def _stub_session_with_rows(rows: list[tuple]) -> MagicMock:
    s = MagicMock()
    s.execute.return_value.fetchall.return_value = rows
    return s


def _patch_common(monkeypatch: pytest.MonkeyPatch):
    """Default: ENABLE_HYBRID_RAG off, threshold 0.45, no Tavily key,
    embed_texts returns a fixed-shape vector. Caller overrides individual
    behaviours per test.
    """
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.embed_texts",
        lambda texts, **_kw: [[0.0] * 1536 for _ in texts],
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.settings.ENABLE_HYBRID_RAG", False
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.settings.RAG_DISTANCE_THRESHOLD",
        0.45,
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.settings.TAVILY_API_KEY", ""
    )


# --- greeting short-circuit ----------------------------------------------


def test_greeting_short_circuits_to_conversational(monkeypatch: pytest.MonkeyPatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.chat_conversational",
        lambda question, history, agent_type, **_kw: "Hi! How can I help?",
    )
    # No DB call should be needed — but make SessionLocal safe just in case.
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.SessionLocal",
        lambda: MagicMock(),
    )

    r = client.post(
        "/tools/rag-chatbot/ask",
        json={"question": "hi", "collection_id": "hr-docs"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_type"] == "none"
    assert body["mode"] == "conversational"
    assert body["sources"] == []
    assert "Hi" in body["answer"]


# --- kb_grounded path ----------------------------------------------------


def test_kb_grounded_when_chunks_within_threshold(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_common(monkeypatch)
    fake_session = _stub_session_with_rows(
        [
            ("Refunds within 5 business days.", "policy.md", 0.10),
            ("Returns require receipt.", "returns.md", 0.18),
        ]
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.SessionLocal", lambda: fake_session
    )

    captured: dict = {}

    def fake_chat(question, chunks, agent_type, history, **_kw):
        captured["question"] = question
        captured["chunks"] = chunks
        captured["agent_type"] = agent_type
        return "Refunds happen in 5 days."

    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.chat_with_context", fake_chat
    )

    r = client.post(
        "/tools/rag-chatbot/ask",
        json={
            "question": "When do I get my refund?",
            "collection_id": "hr-docs",
            "agent_type": "support",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_type"] == "documents"
    assert body["mode"] == "kb_grounded"
    assert {s["name"] for s in body["sources"]} == {"policy.md", "returns.md"}
    assert body["usage"]["chunks_retrieved"] == 2
    assert captured["agent_type"] == "support"


def test_include_sources_false_strips_sources(monkeypatch: pytest.MonkeyPatch):
    _patch_common(monkeypatch)
    fake_session = _stub_session_with_rows(
        [("text", "doc.md", 0.10)]
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.SessionLocal", lambda: fake_session
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.chat_with_context",
        lambda *a, **kw: "answer",
    )

    r = client.post(
        "/tools/rag-chatbot/ask",
        json={
            "question": "What is the policy?",
            "collection_id": "hr-docs",
            "include_sources": False,
        },
    )
    assert r.status_code == 200
    assert r.json()["sources"] == []


# --- web fallback path --------------------------------------------------


def test_web_fallback_when_kb_distance_above_threshold(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.settings.TAVILY_API_KEY",
        "fake-key",
    )
    # Distance 0.9 > threshold 0.45 → kb is not usable, web fallback runs.
    fake_session = _stub_session_with_rows(
        [("irrelevant", "old.md", 0.9)]
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.SessionLocal", lambda: fake_session
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.search_web",
        lambda q, **_kw: [
            _FakeWebResult(
                title="Refund Guide",
                url="https://example.com/refunds",
                snippet="Refunds info",
            )
        ],
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.chat_with_web_context",
        lambda question, web_results, agent_type, history, **_kw: "From the web: refunds info",
    )

    r = client.post(
        "/tools/rag-chatbot/ask",
        json={"question": "Refunds?", "collection_id": "hr-docs"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_type"] == "web"
    assert body["mode"] == "web_grounded"
    assert body["sources"][0]["url"] == "https://example.com/refunds"
    assert "web search" in body["message"]


def test_llm_fallback_when_no_chunks_and_no_web(monkeypatch: pytest.MonkeyPatch):
    _patch_common(monkeypatch)
    fake_session = _stub_session_with_rows([])
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.SessionLocal", lambda: fake_session
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.chat_without_context",
        lambda question, agent_type, history, **_kw: "Generic LLM answer",
    )

    r = client.post(
        "/tools/rag-chatbot/ask",
        json={"question": "Random?", "collection_id": "hr-docs"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_type"] == "llm"
    assert body["mode"] == "llm_fallback"
    assert body["sources"] == []


# --- error handling -----------------------------------------------------


def test_embed_failure_returns_503(monkeypatch: pytest.MonkeyPatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.embed_texts",
        lambda _texts, **_kw: (_ for _ in ()).throw(RuntimeError("embed down")),
    )
    monkeypatch.setattr(
        "catapult_adapter.service.operations.ask.SessionLocal", lambda: MagicMock()
    )

    r = client.post(
        "/tools/rag-chatbot/ask",
        json={"question": "What is the policy?", "collection_id": "hr-docs"},
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_invalid_question_rejected_by_pydantic():
    r = client.post(
        "/tools/rag-chatbot/ask",
        json={"question": "", "collection_id": "hr-docs"},
    )
    assert r.status_code == 422
