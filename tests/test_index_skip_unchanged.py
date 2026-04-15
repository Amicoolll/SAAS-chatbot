"""Unit tests for skip-unchanged behaviour in ``app.api.index._run_index``
plus the pure ``_should_skip_indexed_file`` decision helper.

Strategy: tests avoid a real DB/pgvector. The pure helper tests don't touch
any external system. The integration tests replace ``db`` with a fake that
supports the narrow subset used by ``_run_index``, and patch the chunker and
embedder so no network calls happen.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.api import index as index_module


# --------------------------- pure helper ---------------------------


def _doc(modified_time: str) -> SimpleNamespace:
    return SimpleNamespace(id="doc-1", modified_time=modified_time)


def test_skip_returns_false_when_no_expected_modified_time():
    assert index_module._should_skip_indexed_file(_doc("t1"), True, None) is False
    assert index_module._should_skip_indexed_file(_doc("t1"), True, "") is False


def test_skip_returns_false_when_no_existing_doc():
    assert index_module._should_skip_indexed_file(None, False, "t1") is False


def test_skip_returns_false_when_modified_time_differs():
    assert index_module._should_skip_indexed_file(_doc("t1"), True, "t2") is False


def test_skip_returns_false_when_no_chunks_exist():
    assert index_module._should_skip_indexed_file(_doc("t1"), False, "t1") is False


def test_skip_returns_true_when_everything_matches():
    assert index_module._should_skip_indexed_file(_doc("t1"), True, "t1") is True


# --------------------------- fake DB + fixtures ---------------------------


class _FakeQuery:
    """Stands in for ``db.query(...).filter(...).first()`` chains.

    The ``_run_index`` loop calls ``.first()`` a few times per file, and we
    want deterministic return values per call site. The integration tests
    below seed a call-ordered sequence.
    """

    def __init__(self, results: list):
        self._results = results
        self._idx = 0

    def filter(self, *_a, **_kw):
        return self

    def first(self):
        result = self._results[self._idx]
        self._idx += 1
        return result


class _FakeDB:
    def __init__(self, first_results: list):
        self.query_log: list = []
        self.add_calls: list = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.execute_calls: list = []
        self._query = _FakeQuery(first_results)

    def query(self, *args):
        self.query_log.append(args)
        return self._query

    def add(self, obj):
        self.add_calls.append(obj)

    def commit(self):
        self.commit_calls += 1

    def refresh(self, obj):
        if getattr(obj, "id", None) in (None, ""):
            obj.id = "new-doc-id"

    def execute(self, stmt):
        self.execute_calls.append(stmt)

    def rollback(self):
        self.rollback_calls += 1


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def patch_pipeline_state(monkeypatch):
    monkeypatch.setattr(
        index_module.pipeline_state,
        "update_index_progress",
        lambda *a, **kw: None,
    )


def _write_manifest(user_id: str, mapping: dict) -> None:
    path = os.path.join("data", f"user_{user_id}", ".drive_manifest.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(mapping, fp)


def _write_raw_text(user_id: str, name: str, body: str = "hello world") -> str:
    raw_dir = os.path.join("data", f"user_{user_id}", "raw")
    os.makedirs(raw_dir, exist_ok=True)
    path = os.path.join(raw_dir, name)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(body)
    return path


# --------------------------- integration tests ---------------------------


def test_index_skips_file_when_manifest_matches_existing_doc(
    workdir, patch_pipeline_state, monkeypatch
):
    user_id, tenant_id = "u1", "t1"
    _write_raw_text(user_id, "Doc1.txt")
    _write_manifest(
        user_id,
        {
            "drive-id-1": {
                "name": "Doc1",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "mimeType": "application/vnd.google-apps.document",
                "local_filename": "Doc1.txt",
            }
        },
    )

    existing_doc = SimpleNamespace(
        id="doc-1", modified_time="2025-01-01T00:00:00Z"
    )
    sample_chunk = SimpleNamespace(id="chunk-1")
    # Only two .first() calls expected on skip path: Document lookup, Chunk lookup.
    db = _FakeDB([existing_doc, sample_chunk])

    embed_mock = MagicMock()
    monkeypatch.setattr(index_module, "embed_texts", embed_mock)
    monkeypatch.setattr(
        index_module, "chunk_text", lambda *a, **kw: ["c1", "c2"]
    )

    result = index_module._run_index(db, tenant_id, user_id, max_files=10)

    assert result["docs_skipped_unchanged"] == 1
    assert result["docs_indexed"] == 0
    assert result["chunks_indexed"] == 0
    embed_mock.assert_not_called()  # The whole point: no OpenAI spend.
    assert db.add_calls == []  # No Chunk/Document inserts.


def test_index_does_not_skip_when_manifest_modifiedtime_differs(
    workdir, patch_pipeline_state, monkeypatch
):
    user_id, tenant_id = "u1", "t1"
    _write_raw_text(user_id, "Doc1.txt")
    _write_manifest(
        user_id,
        {
            "drive-id-1": {
                "name": "Doc1",
                "modifiedTime": "2025-02-01T00:00:00Z",  # newer than DB
                "mimeType": "application/vnd.google-apps.document",
                "local_filename": "Doc1.txt",
            }
        },
    )

    existing_doc = SimpleNamespace(
        id="doc-1", modified_time="2025-01-01T00:00:00Z"
    )
    sample_chunk = SimpleNamespace(id="chunk-old")
    # Calls: Document lookup (skip-check), Chunk lookup (skip-check),
    # Document lookup again (find-or-create).
    db = _FakeDB([existing_doc, sample_chunk, existing_doc])

    monkeypatch.setattr(
        index_module, "chunk_text", lambda *a, **kw: ["c1", "c2"]
    )
    # Keep embed dimension in sync with settings so the fail-fast dim check
    # doesn't trip.
    dim = index_module.settings.EMBED_DIM
    monkeypatch.setattr(
        index_module,
        "embed_texts",
        lambda chunks: [[0.0] * dim for _ in chunks],
    )

    result = index_module._run_index(db, tenant_id, user_id, max_files=10)

    assert result["docs_skipped_unchanged"] == 0
    assert result["docs_indexed"] == 1
    assert result["chunks_indexed"] == 2
    # Existing doc had its modified_time refreshed to the new manifest value.
    assert existing_doc.modified_time == "2025-02-01T00:00:00Z"
    # Old chunks were deleted before new chunks inserted.
    assert len(db.execute_calls) == 1


def test_index_does_not_skip_when_no_chunks_exist_yet(
    workdir, patch_pipeline_state, monkeypatch
):
    user_id, tenant_id = "u1", "t1"
    _write_raw_text(user_id, "Doc1.txt")
    _write_manifest(
        user_id,
        {
            "drive-id-1": {
                "name": "Doc1",
                "modifiedTime": "2025-01-01T00:00:00Z",
                "mimeType": "application/vnd.google-apps.document",
                "local_filename": "Doc1.txt",
            }
        },
    )

    existing_doc = SimpleNamespace(
        id="doc-1", modified_time="2025-01-01T00:00:00Z"
    )
    # Chunk lookup returns None → skip check fails, proceed to re-index.
    db = _FakeDB([existing_doc, None, existing_doc])

    monkeypatch.setattr(index_module, "chunk_text", lambda *a, **kw: ["c1"])
    dim = index_module.settings.EMBED_DIM
    monkeypatch.setattr(
        index_module, "embed_texts", lambda chunks: [[0.0] * dim for _ in chunks]
    )

    result = index_module._run_index(db, tenant_id, user_id, max_files=10)

    assert result["docs_skipped_unchanged"] == 0
    assert result["docs_indexed"] == 1


def test_index_without_manifest_always_indexes(
    workdir, patch_pipeline_state, monkeypatch
):
    user_id, tenant_id = "u1", "t1"
    _write_raw_text(user_id, "Manual.txt")
    # No manifest file written — simulating legacy files dropped into raw/.

    # One .first() call: the find-or-create Document lookup inside try.
    db = _FakeDB([None])
    monkeypatch.setattr(index_module, "chunk_text", lambda *a, **kw: ["c1"])
    dim = index_module.settings.EMBED_DIM
    monkeypatch.setattr(
        index_module, "embed_texts", lambda chunks: [[0.0] * dim for _ in chunks]
    )

    result = index_module._run_index(db, tenant_id, user_id, max_files=10)

    assert result["docs_skipped_unchanged"] == 0
    assert result["docs_indexed"] == 1
    # Newly-created Document inherits empty modified_time when the file isn't
    # in the manifest — we can't prove freshness later, which is the safe default.
    added_docs = [
        obj
        for obj in db.add_calls
        if hasattr(obj, "drive_file_id") and hasattr(obj, "modified_time")
    ]
    assert len(added_docs) == 1
    assert added_docs[0].modified_time == ""
