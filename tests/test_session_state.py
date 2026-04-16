"""Unit tests for GET /session/state decision logic."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api import session as session_mod


# ---- Fake DB ----


class _FakeQuery:
    def __init__(self, results: list):
        self._results = list(results)
        self._idx = 0

    def filter(self, *_a, **_kw):
        return self

    def first(self):
        r = self._results[self._idx]
        self._idx += 1
        return r

    def scalar(self):
        r = self._results[self._idx]
        self._idx += 1
        return r


class _FakeDB:
    def __init__(self, scalars: list, row=None):
        self._sequence = scalars + [row]
        self._idx = 0

    def query(self, *_a):
        q = _FakeQuery([self._sequence[self._idx]])
        self._idx += 1
        return q


class _FakeResponse:
    def __init__(self):
        self.headers = {}


# ---- Fixtures ----


@pytest.fixture
def patch_drive(monkeypatch):
    """Default: drive connected."""
    monkeypatch.setattr(session_mod, "drive_has_credentials_in_db", lambda *a: True)
    monkeypatch.setattr(session_mod, "ensure_tokens_loaded", lambda *a: True)
    session_mod.TOKEN_STORE["u1"] = {"access_token": "a", "refresh_token": "b"}
    yield
    session_mod.TOKEN_STORE.pop("u1", None)


# ---- Tests ----


def test_ready_when_chunks_exist(monkeypatch, patch_drive):
    pipeline_row = SimpleNamespace(
        drive_sync_status="success",
        index_status="success",
        drive_sync_progress_json=None,
        index_progress_json=None,
    )
    db = _FakeDB(scalars=[10, 50], row=pipeline_row)
    monkeypatch.setattr(session_mod, "get_tenant_user", lambda: ("t1", "u1"))

    result = session_mod.get_session_state(
        response=_FakeResponse(),
        tenant_user=("t1", "u1"),
        db=db,
    )
    assert result["action"] == "ready"
    assert result["indexed_documents"] == 10
    assert result["indexed_chunks"] == 50


def test_needs_oauth_when_no_drive(monkeypatch):
    monkeypatch.setattr(session_mod, "drive_has_credentials_in_db", lambda *a: False)
    session_mod.TOKEN_STORE.pop("u1", None)

    pipeline_row = SimpleNamespace(
        drive_sync_status="idle",
        index_status="idle",
        drive_sync_progress_json=None,
        index_progress_json=None,
    )
    db = _FakeDB(scalars=[0, 0], row=pipeline_row)

    result = session_mod.get_session_state(
        response=_FakeResponse(),
        tenant_user=("t1", "u1"),
        db=db,
    )
    assert result["action"] == "needs_oauth"


def test_needs_sync_when_connected_but_no_files(monkeypatch, patch_drive, tmp_path):
    monkeypatch.chdir(tmp_path)
    pipeline_row = SimpleNamespace(
        drive_sync_status="idle",
        index_status="idle",
        drive_sync_progress_json=None,
        index_progress_json=None,
    )
    db = _FakeDB(scalars=[0, 0], row=pipeline_row)

    result = session_mod.get_session_state(
        response=_FakeResponse(),
        tenant_user=("t1", "u1"),
        db=db,
    )
    assert result["action"] == "needs_sync"


def test_needs_index_when_raw_files_exist(monkeypatch, patch_drive, tmp_path):
    monkeypatch.chdir(tmp_path)
    import os

    raw_dir = os.path.join("data", "user_u1", "raw")
    os.makedirs(raw_dir, exist_ok=True)
    with open(os.path.join(raw_dir, "doc.txt"), "w") as f:
        f.write("hello")

    pipeline_row = SimpleNamespace(
        drive_sync_status="success",
        index_status="idle",
        drive_sync_progress_json=None,
        index_progress_json=None,
    )
    db = _FakeDB(scalars=[0, 0], row=pipeline_row)

    result = session_mod.get_session_state(
        response=_FakeResponse(),
        tenant_user=("t1", "u1"),
        db=db,
    )
    assert result["action"] == "needs_index"
    assert result["raw_files"] == 1


def test_in_progress_syncing(monkeypatch, patch_drive):
    pipeline_row = SimpleNamespace(
        drive_sync_status="running",
        index_status="idle",
        drive_sync_progress_json='{"current": 5, "total": 20, "current_file": "doc.pdf"}',
        index_progress_json=None,
    )
    db = _FakeDB(scalars=[0, 0], row=pipeline_row)

    result = session_mod.get_session_state(
        response=_FakeResponse(),
        tenant_user=("t1", "u1"),
        db=db,
    )
    assert result["action"] == "in_progress"
    assert result["phase"] == "syncing"
    assert result["progress"]["current"] == 5
    assert result["progress"]["total"] == 20
    assert result["progress"]["percent"] == 25.0


def test_in_progress_indexing(monkeypatch, patch_drive):
    pipeline_row = SimpleNamespace(
        drive_sync_status="success",
        index_status="running",
        drive_sync_progress_json=None,
        index_progress_json='{"current": 3, "total": 10}',
    )
    db = _FakeDB(scalars=[0, 0], row=pipeline_row)

    result = session_mod.get_session_state(
        response=_FakeResponse(),
        tenant_user=("t1", "u1"),
        db=db,
    )
    assert result["action"] == "in_progress"
    assert result["phase"] == "indexing"
    assert result["progress"]["percent"] == 30.0


def test_ready_takes_priority_over_needs_sync(monkeypatch, patch_drive):
    """Even if last sync was long ago, if chunks exist → ready."""
    pipeline_row = SimpleNamespace(
        drive_sync_status="idle",
        index_status="idle",
        drive_sync_progress_json=None,
        index_progress_json=None,
    )
    db = _FakeDB(scalars=[5, 100], row=pipeline_row)

    result = session_mod.get_session_state(
        response=_FakeResponse(),
        tenant_user=("t1", "u1"),
        db=db,
    )
    assert result["action"] == "ready"
