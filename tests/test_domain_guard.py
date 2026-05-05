"""Unit tests for ``app.services.domain_guard`` — semantic hard-filter.

The centroid is mocked so tests never call OpenAI.
"""

from __future__ import annotations

import math

import pytest

from app.services import domain_guard as dg


@pytest.fixture(autouse=True)
def _reset_cache():
    dg.reset_cache()
    yield
    dg.reset_cache()


# ---------- pure cosine ----------


def test_cosine_identical_vectors_returns_1():
    assert dg._cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors_returns_0():
    assert dg._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector_returns_0_no_divide_by_zero():
    assert dg._cosine([0.0, 0.0], [1.0, 2.0]) == 0.0


# ---------- centroid caching ----------


def test_centroid_is_computed_once_and_cached(monkeypatch):
    call_count = {"n": 0}

    def _fake_embed(texts):
        call_count["n"] += 1
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(dg, "embed_texts", _fake_embed)

    c1 = dg.get_centroid()
    c2 = dg.get_centroid()

    assert c1 == c2
    assert c1 == [1.0, 0.0, 0.0]
    assert call_count["n"] == 1  # cached after first call


def test_centroid_averages_seed_vectors(monkeypatch):
    def _fake_embed(texts):
        # Three vectors: mean should be [1, 0.5, 0]
        return [[1.0, 1.0, 0.0], [1.0, 0.5, 0.0], [1.0, 0.0, 0.0]]

    monkeypatch.setattr(dg, "embed_texts", _fake_embed)
    # Force small seed list so we know what's being averaged
    monkeypatch.setattr(dg, "_AVIATION_SEEDS", ["a", "b", "c"])

    centroid = dg.get_centroid()
    assert centroid == pytest.approx([1.0, 0.5, 0.0])


def test_centroid_returns_none_when_embed_fails(monkeypatch):
    def _raising_embed(texts):
        raise RuntimeError("openai down")

    monkeypatch.setattr(dg, "embed_texts", _raising_embed)
    assert dg.get_centroid() is None


# ---------- is_on_domain ----------


def test_is_on_domain_accepts_vector_close_to_centroid(monkeypatch):
    monkeypatch.setattr(dg, "get_centroid", lambda: [1.0, 0.0, 0.0])
    assert dg.is_on_domain([1.0, 0.0, 0.0], threshold=0.5) is True


def test_is_on_domain_rejects_orthogonal_vector(monkeypatch):
    monkeypatch.setattr(dg, "get_centroid", lambda: [1.0, 0.0])
    # Orthogonal → similarity 0.0 → below any positive threshold
    assert dg.is_on_domain([0.0, 1.0], threshold=0.3) is False


def test_is_on_domain_respects_threshold_boundary(monkeypatch):
    monkeypatch.setattr(dg, "get_centroid", lambda: [1.0, 0.0])
    # 60-degree angle → similarity = cos(60) = 0.5
    q = [math.cos(math.radians(60)), math.sin(math.radians(60))]
    assert dg.is_on_domain(q, threshold=0.4) is True
    assert dg.is_on_domain(q, threshold=0.6) is False


def test_is_on_domain_fails_open_when_centroid_missing(monkeypatch):
    """If the centroid can't be computed (e.g., OpenAI down), don't block
    users — let the question through and rely on the prompt guardrail.
    """
    monkeypatch.setattr(dg, "get_centroid", lambda: None)
    assert dg.is_on_domain([0.0, 1.0, 0.0], threshold=0.9) is True


def test_is_on_domain_uses_settings_threshold_when_not_passed(monkeypatch):
    monkeypatch.setattr(dg, "get_centroid", lambda: [1.0, 0.0])
    monkeypatch.setattr(dg.settings, "DOMAIN_GUARD_THRESHOLD", 0.1)
    # Low similarity but below the very lenient threshold
    q = [math.cos(math.radians(70)), math.sin(math.radians(70))]  # ~0.34
    assert dg.is_on_domain(q) is True

    monkeypatch.setattr(dg.settings, "DOMAIN_GUARD_THRESHOLD", 0.9)
    assert dg.is_on_domain(q) is False
