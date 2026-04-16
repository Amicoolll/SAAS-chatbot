"""Unit tests for ``app.services.web_search`` — Tavily wrapper, no HTTP."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services import web_search as ws_mod
from app.services.web_search import WebResult, search_web


# ---- Helpers ----


def _tavily_response(results: list[dict], answer: str | None = None) -> bytes:
    body: dict = {"results": results}
    if answer is not None:
        body["answer"] = answer
    return json.dumps(body).encode("utf-8")


def _fake_urlopen(body_bytes: bytes):
    """Return a context-manager mock that mimics urllib.request.urlopen."""
    resp = MagicMock()
    resp.read.return_value = body_bytes
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---- Tests ----


def test_returns_empty_when_no_api_key(monkeypatch):
    monkeypatch.setattr(ws_mod.settings, "TAVILY_API_KEY", None)
    assert search_web("test query") == []


def test_parses_tavily_results(monkeypatch):
    monkeypatch.setattr(ws_mod.settings, "TAVILY_API_KEY", "tvly-test")
    body = _tavily_response(
        [
            {"title": "T1", "url": "https://a.com", "content": "Snippet 1"},
            {"title": "T2", "url": "https://b.com", "content": "Snippet 2"},
        ]
    )
    with patch("urllib.request.urlopen", return_value=_fake_urlopen(body)):
        results = search_web("test query")

    assert len(results) == 2
    assert results[0] == WebResult(title="T1", url="https://a.com", snippet="Snippet 1")
    assert results[1].url == "https://b.com"


def test_uses_tavily_answer_when_no_results(monkeypatch):
    monkeypatch.setattr(ws_mod.settings, "TAVILY_API_KEY", "tvly-test")
    body = _tavily_response([], answer="Tavily says this.")
    with patch("urllib.request.urlopen", return_value=_fake_urlopen(body)):
        results = search_web("test query")

    assert len(results) == 1
    assert results[0].snippet == "Tavily says this."
    assert results[0].title == "Tavily Summary"


def test_returns_empty_on_http_error(monkeypatch):
    monkeypatch.setattr(ws_mod.settings, "TAVILY_API_KEY", "tvly-test")
    with patch(
        "urllib.request.urlopen", side_effect=OSError("connection refused")
    ):
        results = search_web("test query")

    assert results == []


def test_skips_results_without_url_or_snippet(monkeypatch):
    monkeypatch.setattr(ws_mod.settings, "TAVILY_API_KEY", "tvly-test")
    body = _tavily_response(
        [
            {"title": "Good", "url": "https://a.com", "content": "Has snippet"},
            {"title": "Bad", "url": "", "content": "No URL"},
            {"title": "Worse", "url": "https://c.com", "content": ""},
        ]
    )
    with patch("urllib.request.urlopen", return_value=_fake_urlopen(body)):
        results = search_web("test query")

    assert len(results) == 1
    assert results[0].url == "https://a.com"


def test_respects_max_results_setting(monkeypatch):
    monkeypatch.setattr(ws_mod.settings, "TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(ws_mod.settings, "WEB_SEARCH_MAX_RESULTS", 3)

    captured_payload = {}

    def _capture_urlopen(req, timeout=None):
        captured_payload.update(json.loads(req.data.decode("utf-8")))
        return _fake_urlopen(_tavily_response([]))

    with patch("urllib.request.urlopen", side_effect=_capture_urlopen):
        search_web("test query")

    assert captured_payload["max_results"] == 3
    assert captured_payload["search_depth"] == "basic"
    assert captured_payload["include_answer"] is True
