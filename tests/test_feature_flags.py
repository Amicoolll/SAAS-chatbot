"""Unit tests for ``app.services.feature_flags`` — TTL cache and set/get."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import feature_flags as ff_mod


# ---- Fake DB layer ----


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *_a, **_kw):
        return self

    def first(self):
        return self._result

    def all(self):
        return [self._result] if self._result else []


class _FakeDB:
    def __init__(self, result=None):
        self.result = result
        self.added: list = []
        self.committed = 0

    def query(self, *_a):
        return _FakeQuery(self.result)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed += 1

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _clean_cache():
    ff_mod.invalidate_cache()
    yield
    ff_mod.invalidate_cache()


# ---- Tests ----


def test_is_enabled_returns_false_when_no_row(monkeypatch):
    monkeypatch.setattr(ff_mod, "SessionLocal", lambda: _FakeDB(None))
    assert ff_mod.is_enabled("t1", "web_search_fallback") is False


def test_is_enabled_returns_true_when_row_enabled(monkeypatch):
    row = SimpleNamespace(enabled=True)
    monkeypatch.setattr(ff_mod, "SessionLocal", lambda: _FakeDB(row))
    assert ff_mod.is_enabled("t1", "web_search_fallback") is True


def test_is_enabled_returns_false_when_row_disabled(monkeypatch):
    row = SimpleNamespace(enabled=False)
    monkeypatch.setattr(ff_mod, "SessionLocal", lambda: _FakeDB(row))
    assert ff_mod.is_enabled("t1", "web_search_fallback") is False


def test_cache_avoids_second_db_call(monkeypatch):
    call_count = {"n": 0}
    real_db = _FakeDB(SimpleNamespace(enabled=True))

    def _counting_session():
        call_count["n"] += 1
        return real_db

    monkeypatch.setattr(ff_mod, "SessionLocal", _counting_session)

    assert ff_mod.is_enabled("t1", "flag_a") is True
    assert ff_mod.is_enabled("t1", "flag_a") is True
    assert call_count["n"] == 1  # second call served from cache


def test_cache_expires_after_ttl(monkeypatch):
    monkeypatch.setattr(ff_mod.settings, "FEATURE_FLAG_CACHE_TTL_SECONDS", 0)
    row = SimpleNamespace(enabled=True)
    monkeypatch.setattr(ff_mod, "SessionLocal", lambda: _FakeDB(row))

    assert ff_mod.is_enabled("t1", "flag_a") is True
    # TTL=0 means every call hits DB — cache always stale.
    time.sleep(0.01)
    # Force a second call; with TTL=0 it should re-query.
    ff_mod._cache[("t1", "flag_a")] = (False, 0)  # stale entry
    assert ff_mod.is_enabled("t1", "flag_a") is True  # re-fetched from DB


def test_set_enabled_invalidates_cache(monkeypatch):
    ff_mod._cache[("t1", "flag_a")] = (False, time.monotonic())
    db = _FakeDB(None)
    monkeypatch.setattr(ff_mod, "SessionLocal", lambda: db)

    ff_mod.set_enabled("t1", "flag_a", True)
    assert ("t1", "flag_a") not in ff_mod._cache
    assert db.committed == 1


def test_set_enabled_updates_existing_row(monkeypatch):
    existing = SimpleNamespace(enabled=False)
    db = _FakeDB(existing)
    monkeypatch.setattr(ff_mod, "SessionLocal", lambda: db)

    ff_mod.set_enabled("t1", "flag_a", True)
    assert existing.enabled is True
    assert db.added == []


def test_set_enabled_creates_new_row_when_missing(monkeypatch):
    db = _FakeDB(None)
    monkeypatch.setattr(ff_mod, "SessionLocal", lambda: db)

    ff_mod.set_enabled("t1", "flag_a", True)
    assert len(db.added) == 1
    assert db.added[0].flag_name == "flag_a"
    assert db.added[0].enabled is True


def test_different_tenants_cached_independently(monkeypatch):
    row_on = SimpleNamespace(enabled=True)
    row_off = SimpleNamespace(enabled=False)
    call_n = {"n": 0}

    def _session():
        call_n["n"] += 1
        return _FakeDB(row_on if call_n["n"] == 1 else row_off)

    monkeypatch.setattr(ff_mod, "SessionLocal", _session)

    assert ff_mod.is_enabled("acme", "f") is True
    assert ff_mod.is_enabled("contoso", "f") is False
