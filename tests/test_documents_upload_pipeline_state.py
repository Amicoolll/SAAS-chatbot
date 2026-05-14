"""Regression tests for the sync-path pipeline_state bug.

Before the fix, ``POST /documents/upload-and-index?background=false``
called ``_run_index`` directly without wrapping it in
``pipeline_state.mark_index_running()`` /
``mark_index_success()`` / ``mark_index_error()``. Result: the
``pipeline_state`` row was never created in sync mode, so:

  - ``update_index_progress()`` calls during the run hit "no_row" and
    skipped silently (log spam)
  - Frontend polling ``/pipeline/status`` saw stale data
  - Status field stayed at whatever the previous run left it (or
    "idle" / "error" forever)

These tests verify the sync path now marks the lifecycle correctly,
and that errors propagate the failure into pipeline_state too.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import documents as documents_mod
from app.core.deps import get_tenant_user
from app.db.session import get_db
from app.main import app


client = TestClient(app)


@pytest.fixture
def patch_dependencies(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Replace the heavy bits with stubs:
       - get_db: MagicMock session
       - get_tenant_user: fixed identity
       - _save_uploaded_files: skip disk writes, return canned result
       - _run_index: skip OpenAI / DB writes, return canned index result
       - pipeline_state.{mark_index_running, mark_index_success, mark_index_error}:
         spies so we can assert what was called
    """
    fake_db = MagicMock()
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_tenant_user] = lambda: ("acme", "user-x")

    monkeypatch.setattr(
        documents_mod,
        "_save_uploaded_files",
        lambda user_id, files: {"saved": 1, "saved_files": ["x.txt"]},
    )

    spy_running = MagicMock()
    spy_success = MagicMock()
    spy_error = MagicMock()
    monkeypatch.setattr(documents_mod.pipeline_state, "mark_index_running", spy_running)
    monkeypatch.setattr(documents_mod.pipeline_state, "mark_index_success", spy_success)
    monkeypatch.setattr(documents_mod.pipeline_state, "mark_index_error", spy_error)

    yield {
        "spy_running": spy_running,
        "spy_success": spy_success,
        "spy_error": spy_error,
    }

    app.dependency_overrides.clear()


def _post_sync_upload(payload_files: list = None):
    """POST /documents/upload-and-index?background=false with a tiny dummy file."""
    files = payload_files or [("files", ("x.txt", io.BytesIO(b"hello"), "text/plain"))]
    return client.post(
        "/documents/upload-and-index?background=false",
        headers={"X-Tenant-Id": "acme", "X-User-Id": "user-x"},
        files=files,
    )


def test_sync_upload_marks_running_then_success(
    patch_dependencies, monkeypatch: pytest.MonkeyPatch
):
    """Successful sync upload+index calls mark_index_running BEFORE _run_index
    and mark_index_success AFTER it returns. mark_index_error is NOT called."""
    spies = patch_dependencies

    # _run_index returns a canned result without touching disk/OpenAI/DB.
    canned_result = {"docs_indexed": 1, "chunks_indexed": 7}
    monkeypatch.setattr(
        documents_mod, "_run_index", lambda db, t, u, m: canned_result
    )

    r = _post_sync_upload()
    assert r.status_code == 200, r.text

    spies["spy_running"].assert_called_once_with("acme", "user-x")
    spies["spy_success"].assert_called_once_with("acme", "user-x", canned_result)
    spies["spy_error"].assert_not_called()


def test_sync_upload_marks_error_when_run_index_raises_httpexception(
    patch_dependencies, monkeypatch: pytest.MonkeyPatch
):
    """If _run_index raises HTTPException (e.g. embedding service down),
    mark_index_error is called with the detail string and the exception
    is re-raised so the client gets the right status code."""
    spies = patch_dependencies

    def boom(db, t, u, m):
        raise HTTPException(status_code=503, detail="Embedding service down")

    monkeypatch.setattr(documents_mod, "_run_index", boom)

    r = _post_sync_upload()
    assert r.status_code == 503

    spies["spy_running"].assert_called_once()
    spies["spy_success"].assert_not_called()
    spies["spy_error"].assert_called_once_with(
        "acme", "user-x", "Embedding service down"
    )


def test_sync_upload_marks_error_when_run_index_raises_unexpected(
    patch_dependencies, monkeypatch: pytest.MonkeyPatch
):
    """Generic exceptions (e.g. DB transaction failure) flip status to
    error and re-raise — the important guarantee is that mark_index_error
    runs BEFORE the exception escapes, so /pipeline/status reflects the
    failure even though the response itself is a 500."""
    spies = patch_dependencies

    def boom(db, t, u, m):
        raise RuntimeError("disk full")

    monkeypatch.setattr(documents_mod, "_run_index", boom)

    # TestClient re-raises uncaught server exceptions by default — use a
    # client that surfaces them as 500 so we can also assert the response.
    nonraising_client = TestClient(app, raise_server_exceptions=False)
    r = nonraising_client.post(
        "/documents/upload-and-index?background=false",
        headers={"X-Tenant-Id": "acme", "X-User-Id": "user-x"},
        files=[("files", ("x.txt", io.BytesIO(b"hello"), "text/plain"))],
    )
    assert r.status_code == 500

    spies["spy_running"].assert_called_once()
    spies["spy_success"].assert_not_called()
    spies["spy_error"].assert_called_once_with("acme", "user-x", "disk full")


def test_background_upload_does_NOT_call_pipeline_state_synchronously(
    patch_dependencies, monkeypatch: pytest.MonkeyPatch
):
    """The background path is unchanged — _index_background_task wraps the
    pipeline_state calls itself. The endpoint returns immediately without
    invoking the marks here. Regression guard so the fix doesn't double-call."""
    spies = patch_dependencies

    monkeypatch.setattr(
        documents_mod, "_index_background_task", lambda t, u, m: None
    )

    r = client.post(
        "/documents/upload-and-index?background=true",
        headers={"X-Tenant-Id": "acme", "X-User-Id": "user-x"},
        files=[("files", ("x.txt", io.BytesIO(b"hello"), "text/plain"))],
    )
    assert r.status_code == 200, r.text

    # Synchronous endpoint code MUST NOT have called these — the bg task
    # owns them.
    spies["spy_running"].assert_not_called()
    spies["spy_success"].assert_not_called()
    spies["spy_error"].assert_not_called()
