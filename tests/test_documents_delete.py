"""Tests for ``DELETE /documents/{document_id}``.

The endpoint is tenant-scoped: a tenant can only delete its own
documents. We mock the DB session via FastAPI's dependency override so
no real Postgres is required.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api import documents as documents_mod
from app.core.deps import get_tenant_user
from app.db.session import get_db
from app.main import app


client = TestClient(app)


# ---- helpers --------------------------------------------------------


def _make_session(*, doc=None, chunks_deleted: int = 0) -> MagicMock:
    """Build a fake SQLAlchemy session that returns ``doc`` from the
    documents lookup and reports ``chunks_deleted`` from the chunks
    delete statement.
    """
    session = MagicMock()

    # db.query(Document).filter(...).first() → doc | None
    query_chain = MagicMock()
    filter_chain = MagicMock()
    filter_chain.first.return_value = doc
    query_chain.filter.return_value = filter_chain
    session.query.return_value = query_chain

    # db.execute(delete(Chunk)...) returns an object with .rowcount
    exec_result = MagicMock()
    exec_result.rowcount = chunks_deleted
    session.execute.return_value = exec_result

    session.delete.return_value = None
    session.commit.return_value = None
    return session


@pytest.fixture
def override_deps():
    """Override the get_db and get_tenant_user dependencies for one test
    block; clean up afterwards. Yields a callable used to install the
    overrides.
    """
    def _install(*, db, tenant_id="acme", user_id="user-1"):
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_tenant_user] = lambda: (tenant_id, user_id)
    yield _install
    app.dependency_overrides.clear()


# ---- happy path ----------------------------------------------------


def test_delete_existing_document_returns_ok_with_counts(
    override_deps, monkeypatch: pytest.MonkeyPatch
):
    """Successful delete: 200, returns deleted id + name + chunk count."""
    doc = SimpleNamespace(id="doc-123", name="policy.pdf")
    db = _make_session(doc=doc, chunks_deleted=42)
    override_deps(db=db, tenant_id="acme", user_id="user-1")

    # Don't actually touch the disk; pretend the file isn't there.
    monkeypatch.setattr(os.path, "isfile", lambda _p: False)

    r = client.delete(
        "/documents/doc-123",
        headers={"X-Tenant-Id": "acme", "X-User-Id": "user-1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["deleted_document_id"] == "doc-123"
    assert body["deleted_document_name"] == "policy.pdf"
    assert body["chunks_deleted"] == 42
    assert body["raw_file_removed"] is False  # file didn't exist on disk

    # DB side effects
    db.query.assert_called_once_with(documents_mod.Document)
    db.delete.assert_called_once_with(doc)
    assert db.commit.called


def test_delete_removes_raw_file_when_present(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    """If the raw file exists on disk, delete it too (default behaviour)."""
    doc = SimpleNamespace(id="d1", name="manual.txt")
    db = _make_session(doc=doc, chunks_deleted=0)
    override_deps(db=db, tenant_id="acme", user_id="user-7")

    # Simulate a real file the endpoint will try to remove.
    raw_dir = tmp_path / "data" / "user_user-7" / "raw"
    raw_dir.mkdir(parents=True)
    raw_path = raw_dir / "manual.txt"
    raw_path.write_text("contents")

    monkeypatch.chdir(tmp_path)

    r = client.delete(
        "/documents/d1",
        headers={"X-Tenant-Id": "acme", "X-User-Id": "user-7"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["raw_file_removed"] is True
    assert not raw_path.exists()


def test_delete_with_delete_raw_file_false_keeps_disk_file(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    """When ``delete_raw_file=false``, the file is left on disk even if
    it exists. Useful when an operator wants to re-index the same source
    later without re-uploading.
    """
    doc = SimpleNamespace(id="d2", name="schedule.csv")
    db = _make_session(doc=doc, chunks_deleted=5)
    override_deps(db=db, tenant_id="acme", user_id="user-9")

    raw_dir = tmp_path / "data" / "user_user-9" / "raw"
    raw_dir.mkdir(parents=True)
    raw_path = raw_dir / "schedule.csv"
    raw_path.write_text("data")

    monkeypatch.chdir(tmp_path)

    r = client.delete(
        "/documents/d2?delete_raw_file=false",
        headers={"X-Tenant-Id": "acme", "X-User-Id": "user-9"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["raw_file_removed"] is False
    assert raw_path.exists()  # disk file untouched


# ---- error paths ---------------------------------------------------


def test_delete_nonexistent_document_returns_404(override_deps):
    """No matching row → 404 Not Found."""
    db = _make_session(doc=None)
    override_deps(db=db)

    r = client.delete(
        "/documents/does-not-exist",
        headers={"X-Tenant-Id": "acme", "X-User-Id": "user-1"},
    )
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()
    # No DB writes attempted on the not-found path
    db.delete.assert_not_called()


def test_delete_other_tenants_document_returns_404(override_deps):
    """Tenant isolation: even if the document_id exists for some other
    tenant, our query (filtered by tenant_id) returns None → 404. The
    response is identical to "not found" so the existence isn't leaked.
    """
    db = _make_session(doc=None)  # filter excludes other-tenant rows
    override_deps(db=db, tenant_id="acme")

    r = client.delete(
        "/documents/owned-by-other-tenant",
        headers={"X-Tenant-Id": "acme", "X-User-Id": "user-1"},
    )
    assert r.status_code == 404


def test_delete_handles_raw_file_unlink_failure_gracefully(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
):
    """If os.remove() raises (locked file, permission denied), the
    endpoint should still return 200 — the DB cleanup is already done
    and the orphan file is harmless until next /index/run."""
    doc = SimpleNamespace(id="d3", name="locked.pdf")
    db = _make_session(doc=doc, chunks_deleted=1)
    override_deps(db=db, tenant_id="acme", user_id="user-3")

    monkeypatch.setattr(os.path, "isfile", lambda _p: True)

    def boom(_p):
        raise PermissionError("locked")

    monkeypatch.setattr(os, "remove", boom)

    r = client.delete(
        "/documents/d3",
        headers={"X-Tenant-Id": "acme", "X-User-Id": "user-3"},
    )
    assert r.status_code == 200
    assert r.json()["raw_file_removed"] is False
