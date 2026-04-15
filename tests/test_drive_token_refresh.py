"""Unit tests for ``app.services.drive.client.refresh_and_persist_tokens``
and the ``DriveReconnectRequired`` propagation in the sync flow.

We avoid network and DB by patching:
- ``build_drive_credentials`` to return a Mock with a controllable ``refresh``.
- ``persist_and_cache_tokens`` to record what would have been persisted.
- ``TOKEN_STORE`` directly (it is just a module-level dict).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError

from app.services.drive import client as drive_client
from app.services.drive import routes as drive_routes
from app.services.drive import token_store as ts


# --------------------------- shared fixtures ---------------------------


@pytest.fixture
def clean_token_store():
    ts.TOKEN_STORE.pop("u1", None)
    yield
    ts.TOKEN_STORE.pop("u1", None)


@pytest.fixture
def capture_persist(monkeypatch):
    """Record (tenant, user, access, refresh) tuples persist would write."""
    captured: list[tuple] = []

    def _fake_persist(tenant_id, user_id, access_token, refresh_token):
        captured.append((tenant_id, user_id, access_token, refresh_token))
        # Mimic what the real function does to the in-memory cache so callers
        # observing TOKEN_STORE see the refreshed value.
        ts.TOKEN_STORE[user_id] = {
            "access_token": access_token,
            "refresh_token": refresh_token,
        }

    # Patch BOTH module-locations: client.py imports lazily inside the function,
    # so we patch the real module that the lazy import resolves to.
    monkeypatch.setattr(ts, "persist_and_cache_tokens", _fake_persist)
    return captured


# ------------------- refresh_and_persist_tokens -------------------


def test_refresh_persists_new_access_token(
    clean_token_store, capture_persist, monkeypatch
):
    ts.TOKEN_STORE["u1"] = {"access_token": "old", "refresh_token": "rt"}

    fake_creds = MagicMock()
    fake_creds.token = "new-access"
    fake_creds.refresh_token = "rt"  # Google didn't rotate it
    fake_creds.refresh = MagicMock()

    monkeypatch.setattr(
        drive_client, "build_drive_credentials", lambda *a, **kw: fake_creds
    )

    creds = drive_client.refresh_and_persist_tokens("t1", "u1")

    assert creds is fake_creds
    fake_creds.refresh.assert_called_once()
    assert capture_persist == [("t1", "u1", "new-access", "rt")]
    assert ts.TOKEN_STORE["u1"]["access_token"] == "new-access"


def test_refresh_keeps_existing_refresh_token_when_google_omits_rotation(
    clean_token_store, capture_persist, monkeypatch
):
    ts.TOKEN_STORE["u1"] = {"access_token": "old", "refresh_token": "rt-original"}

    fake_creds = MagicMock()
    fake_creds.token = "new-access"
    fake_creds.refresh_token = None  # Google omitted refresh_token in response
    fake_creds.refresh = MagicMock()

    monkeypatch.setattr(
        drive_client, "build_drive_credentials", lambda *a, **kw: fake_creds
    )

    drive_client.refresh_and_persist_tokens("t1", "u1")

    # We must fall back to the original refresh token, not persist None.
    assert capture_persist[0][3] == "rt-original"


def test_missing_refresh_token_raises_reconnect(clean_token_store):
    ts.TOKEN_STORE["u1"] = {"access_token": "old", "refresh_token": None}
    with pytest.raises(drive_client.DriveReconnectRequired):
        drive_client.refresh_and_persist_tokens("t1", "u1")


def test_missing_token_entry_raises_reconnect(clean_token_store):
    # No entry for u1 at all.
    with pytest.raises(drive_client.DriveReconnectRequired):
        drive_client.refresh_and_persist_tokens("t1", "u1")


def test_refresh_error_raises_reconnect_and_clears_cache(
    clean_token_store, capture_persist, monkeypatch
):
    ts.TOKEN_STORE["u1"] = {"access_token": "old", "refresh_token": "rt"}

    fake_creds = MagicMock()

    def _raise(_request):
        raise RefreshError("invalid_grant")

    fake_creds.refresh.side_effect = _raise
    monkeypatch.setattr(
        drive_client, "build_drive_credentials", lambda *a, **kw: fake_creds
    )

    with pytest.raises(drive_client.DriveReconnectRequired):
        drive_client.refresh_and_persist_tokens("t1", "u1")

    # On unrecoverable refresh failure the in-memory cache is cleared so the
    # next request triggers a reload from DB (or fails fast with reconnect).
    assert "u1" not in ts.TOKEN_STORE
    assert capture_persist == []  # Nothing was persisted.


# ------------------- routes integration -------------------


def test_sync_surfaces_reconnect_as_value_error(monkeypatch, tmp_path):
    """When refresh fails, _run_drive_sync_core should raise a clean
    ValueError with a 'Drive needs reconnect' message — the existing
    background handler already maps ValueError to a user-visible state.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(drive_routes, "ensure_tokens_loaded", lambda *a, **kw: True)
    drive_routes.TOKEN_STORE["u1"] = {"access_token": "a", "refresh_token": "b"}
    monkeypatch.setattr(
        drive_routes.pipeline_state,
        "update_drive_sync_progress",
        lambda *a, **kw: None,
    )

    def _boom(*a, **kw):
        raise drive_routes.DriveReconnectRequired("revoked")

    monkeypatch.setattr(drive_routes, "refresh_and_persist_tokens", _boom)

    with pytest.raises(ValueError) as exc:
        drive_routes._run_drive_sync_core("t1", "u1", max_files=10)
    assert "Drive needs reconnect" in str(exc.value)

    drive_routes.TOKEN_STORE.pop("u1", None)
