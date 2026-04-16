"""Integration tests for the web-search fallback in ``_fallback_answer``.

Verifies mode transitions:
- flag OFF → llm_fallback (today's behaviour)
- flag ON + web results → web_grounded
- flag ON + no web results → llm_fallback
- global kill switch OFF → llm_fallback regardless of flag
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api import chat_pg as chat_mod
from app.services.web_search import WebResult


@pytest.fixture(autouse=True)
def _patch_llm(monkeypatch):
    """Patch LLM calls so tests never hit OpenAI."""
    monkeypatch.setattr(
        chat_mod, "chat_without_context", lambda *a, **kw: "plain fallback"
    )
    monkeypatch.setattr(
        chat_mod, "chat_with_web_context", lambda *a, **kw: "web answer"
    )


# ---- _fallback_answer tests ----


def test_flag_off_returns_llm_fallback(monkeypatch):
    monkeypatch.setattr(chat_mod, "feature_is_enabled", lambda *a: False)
    monkeypatch.setattr(chat_mod.settings, "WEB_SEARCH_GLOBAL_ENABLED", True)

    answer, sources, mode = chat_mod._fallback_answer(
        "t1", "what is X?", "general", ""
    )
    assert mode == "llm_fallback"
    assert answer == "plain fallback"
    assert sources == []


def test_flag_on_with_results_returns_web_grounded(monkeypatch):
    monkeypatch.setattr(chat_mod, "feature_is_enabled", lambda *a: True)
    monkeypatch.setattr(chat_mod.settings, "WEB_SEARCH_GLOBAL_ENABLED", True)
    monkeypatch.setattr(
        chat_mod,
        "search_web",
        lambda q: [
            WebResult(title="R1", url="https://a.com", snippet="info"),
            WebResult(title="R2", url="https://b.com", snippet="more"),
        ],
    )

    answer, sources, mode = chat_mod._fallback_answer(
        "t1", "what is X?", "general", ""
    )
    assert mode == "web_grounded"
    assert answer == "web answer"
    assert sources == ["https://a.com", "https://b.com"]


def test_flag_on_no_results_returns_llm_fallback(monkeypatch):
    monkeypatch.setattr(chat_mod, "feature_is_enabled", lambda *a: True)
    monkeypatch.setattr(chat_mod.settings, "WEB_SEARCH_GLOBAL_ENABLED", True)
    monkeypatch.setattr(chat_mod, "search_web", lambda q: [])

    answer, sources, mode = chat_mod._fallback_answer(
        "t1", "what is X?", "general", ""
    )
    assert mode == "llm_fallback"
    assert answer == "plain fallback"


def test_global_kill_switch_off_skips_web(monkeypatch):
    monkeypatch.setattr(chat_mod, "feature_is_enabled", lambda *a: True)
    monkeypatch.setattr(chat_mod.settings, "WEB_SEARCH_GLOBAL_ENABLED", False)

    search_called = {"n": 0}

    def _no_call(q):
        search_called["n"] += 1
        return [WebResult(title="X", url="https://x.com", snippet="x")]

    monkeypatch.setattr(chat_mod, "search_web", _no_call)

    answer, sources, mode = chat_mod._fallback_answer(
        "t1", "what is X?", "general", ""
    )
    assert mode == "llm_fallback"
    assert search_called["n"] == 0


def test_web_results_with_empty_urls_excluded_from_sources(monkeypatch):
    monkeypatch.setattr(chat_mod, "feature_is_enabled", lambda *a: True)
    monkeypatch.setattr(chat_mod.settings, "WEB_SEARCH_GLOBAL_ENABLED", True)
    monkeypatch.setattr(
        chat_mod,
        "search_web",
        lambda q: [
            WebResult(title="Tavily Summary", url="", snippet="summary"),
            WebResult(title="R1", url="https://a.com", snippet="info"),
        ],
    )

    answer, sources, mode = chat_mod._fallback_answer(
        "t1", "what is X?", "general", ""
    )
    assert mode == "web_grounded"
    assert sources == ["https://a.com"]


def test_different_tenants_different_flags(monkeypatch):
    monkeypatch.setattr(chat_mod.settings, "WEB_SEARCH_GLOBAL_ENABLED", True)
    monkeypatch.setattr(
        chat_mod,
        "feature_is_enabled",
        lambda tid, flag: tid == "acme",
    )
    monkeypatch.setattr(
        chat_mod,
        "search_web",
        lambda q: [WebResult(title="R", url="https://r.com", snippet="s")],
    )

    _, _, mode_acme = chat_mod._fallback_answer("acme", "q", "general", "")
    _, _, mode_contoso = chat_mod._fallback_answer("contoso", "q", "general", "")

    assert mode_acme == "web_grounded"
    assert mode_contoso == "llm_fallback"
