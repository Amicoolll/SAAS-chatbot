"""Unit tests for the Drive sync skip-unchanged manifest logic in
``app.services.drive.routes``.

These tests avoid real network and real DB: Drive service, listing, token
store, download bytes, and pipeline_state updates are all patched.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.drive import routes


# --------------------------- fixtures ---------------------------


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Run each test with cwd set to an isolated temp dir so ``data/`` is scoped."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def patch_pipeline_state(monkeypatch):
    monkeypatch.setattr(
        routes.pipeline_state,
        "update_drive_sync_progress",
        lambda *a, **kw: None,
    )


@pytest.fixture
def patch_drive_auth(monkeypatch):
    """Pretend tokens are present, refresh succeeds, and the Drive service is a mock."""
    monkeypatch.setattr(routes, "ensure_tokens_loaded", lambda *a, **kw: True)
    routes.TOKEN_STORE["u1"] = {"access_token": "a", "refresh_token": "b"}
    monkeypatch.setattr(routes, "refresh_and_persist_tokens", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(
        routes, "build_drive_service_from_credentials", lambda *a, **kw: MagicMock()
    )
    yield
    routes.TOKEN_STORE.pop("u1", None)


def _make_file(fid, name, mime, modified_time="2025-01-01T00:00:00Z"):
    return {
        "id": fid,
        "name": name,
        "mimeType": mime,
        "modifiedTime": modified_time,
        "webViewLink": "",
    }


# --------------------------- manifest helpers ---------------------------


def test_load_manifest_missing_returns_empty(workdir):
    assert routes._load_manifest("u1") == {}


def test_manifest_roundtrip(workdir):
    payload = {
        "abc": {
            "name": "x",
            "modifiedTime": "t",
            "mimeType": "application/pdf",
            "local_filename": "x.pdf",
        }
    }
    routes._save_manifest("u1", payload)
    assert routes._load_manifest("u1") == payload


def test_load_manifest_corrupt_returns_empty(workdir):
    os.makedirs(os.path.join("data", "user_u1"), exist_ok=True)
    Path(routes._manifest_path("u1")).write_text("not json{")
    assert routes._load_manifest("u1") == {}


# --------------------------- sync loop behaviour ---------------------------


def test_first_sync_downloads_all_and_records_manifest(
    workdir, patch_pipeline_state, patch_drive_auth, monkeypatch
):
    files = [
        _make_file("id_doc", "Doc1", routes.GOOGLE_DOC),
        _make_file("id_pdf", "Doc2", "application/pdf"),
    ]
    monkeypatch.setattr(
        routes, "list_all_files", lambda service, on_list_progress=None: files
    )
    monkeypatch.setattr(
        routes, "_download_bytes", lambda req, log_context=None: b"hello world"
    )

    result = routes._run_drive_sync_core("t1", "u1", max_files=10)

    assert result["processed"] == 2
    assert result["skipped"] == 0
    assert result["failed"] == 0

    manifest = routes._load_manifest("u1")
    assert set(manifest.keys()) == {"id_doc", "id_pdf"}
    assert manifest["id_doc"]["local_filename"] == "Doc1.txt"
    assert manifest["id_pdf"]["local_filename"] == "Doc2.pdf"

    raw_dir = os.path.join("data", "user_u1", "raw")
    assert os.path.isfile(os.path.join(raw_dir, "Doc1.txt"))
    assert os.path.isfile(os.path.join(raw_dir, "Doc2.pdf"))


def test_second_sync_skips_unchanged_files(
    workdir, patch_pipeline_state, patch_drive_auth, monkeypatch
):
    files = [_make_file("id_doc", "Doc1", routes.GOOGLE_DOC, modified_time="t1")]
    monkeypatch.setattr(
        routes, "list_all_files", lambda service, on_list_progress=None: files
    )

    calls = {"n": 0}

    def _dl(req, log_context=None):
        calls["n"] += 1
        return b"content"

    monkeypatch.setattr(routes, "_download_bytes", _dl)

    routes._run_drive_sync_core("t1", "u1", max_files=10)
    assert calls["n"] == 1  # downloaded once on first run

    result = routes._run_drive_sync_core("t1", "u1", max_files=10)
    assert result["processed"] == 0
    assert result["skipped"] == 1
    assert calls["n"] == 1  # no new download


def test_modified_file_is_redownloaded(
    workdir, patch_pipeline_state, patch_drive_auth, monkeypatch
):
    state = {"mt": "t1"}

    def _list(service, on_list_progress=None):
        return [
            _make_file("id_doc", "Doc1", routes.GOOGLE_DOC, modified_time=state["mt"])
        ]

    monkeypatch.setattr(routes, "list_all_files", _list)

    calls = {"n": 0}

    def _dl(req, log_context=None):
        calls["n"] += 1
        return b"content"

    monkeypatch.setattr(routes, "_download_bytes", _dl)

    routes._run_drive_sync_core("t1", "u1", max_files=10)
    state["mt"] = "t2"  # Drive reports the file has changed
    result = routes._run_drive_sync_core("t1", "u1", max_files=10)

    assert result["processed"] == 1
    assert result["skipped"] == 0
    assert calls["n"] == 2


def test_missing_local_file_forces_redownload(
    workdir, patch_pipeline_state, patch_drive_auth, monkeypatch
):
    files = [_make_file("id_pdf", "Doc1", "application/pdf", modified_time="t1")]
    monkeypatch.setattr(
        routes, "list_all_files", lambda service, on_list_progress=None: files
    )

    calls = {"n": 0}

    def _dl(req, log_context=None):
        calls["n"] += 1
        return b"pdf-bytes"

    monkeypatch.setattr(routes, "_download_bytes", _dl)

    routes._run_drive_sync_core("t1", "u1", max_files=10)
    # User deleted the local file (or disk was wiped); manifest still has entry.
    os.remove(os.path.join("data", "user_u1", "raw", "Doc1.pdf"))

    result = routes._run_drive_sync_core("t1", "u1", max_files=10)
    assert result["processed"] == 1
    assert result["skipped"] == 0
    assert calls["n"] == 2


def test_mixed_run_skips_unchanged_and_downloads_new(
    workdir, patch_pipeline_state, patch_drive_auth, monkeypatch
):
    round_one = [_make_file("id_a", "A", routes.GOOGLE_DOC, modified_time="t1")]
    round_two = [
        _make_file("id_a", "A", routes.GOOGLE_DOC, modified_time="t1"),  # unchanged
        _make_file("id_b", "B", "application/pdf", modified_time="t1"),  # new
    ]
    state = {"files": round_one}
    monkeypatch.setattr(
        routes,
        "list_all_files",
        lambda service, on_list_progress=None: state["files"],
    )
    monkeypatch.setattr(routes, "_download_bytes", lambda req, log_context=None: b"x")

    routes._run_drive_sync_core("t1", "u1", max_files=10)
    state["files"] = round_two
    result = routes._run_drive_sync_core("t1", "u1", max_files=10)

    assert result["skipped"] == 1
    assert result["processed"] == 1

    manifest = routes._load_manifest("u1")
    assert set(manifest.keys()) == {"id_a", "id_b"}


def test_download_failure_does_not_record_manifest_entry(
    workdir, patch_pipeline_state, patch_drive_auth, monkeypatch
):
    files = [_make_file("id_pdf", "Doc1", "application/pdf")]
    monkeypatch.setattr(
        routes, "list_all_files", lambda service, on_list_progress=None: files
    )

    def _dl(req, log_context=None):
        raise RuntimeError("drive boom")

    monkeypatch.setattr(routes, "_download_bytes", _dl)

    result = routes._run_drive_sync_core("t1", "u1", max_files=10)
    assert result["failed"] == 1
    assert result["processed"] == 0
    # Failed files stay out of the manifest so the next sync retries them.
    manifest = routes._load_manifest("u1")
    assert "id_pdf" not in manifest
