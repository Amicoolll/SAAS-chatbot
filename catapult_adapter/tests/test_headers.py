"""x-catapult-* header parsing tests."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from catapult_adapter.service.headers import CatapultContext, resolve_context


def _make_probe_app() -> FastAPI:
    """Tiny app that echoes the resolved context, so we can assert on it."""
    probe = FastAPI()

    @probe.get("/whoami")
    def whoami(ctx: CatapultContext = Depends(resolve_context)) -> dict:
        return {
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
            "request_id": ctx.request_id,
            "trace_id": ctx.trace_id,
            "app_id": ctx.app_id,
        }

    return probe


def test_defaults_when_headers_missing():
    client = TestClient(_make_probe_app())
    r = client.get("/whoami")
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "demo_tenant"
    assert body["user_id"] == "demo_user"
    assert body["request_id"] == ""
    assert body["trace_id"] is None
    assert body["app_id"] is None


def test_headers_resolved_and_stripped():
    client = TestClient(_make_probe_app())
    r = client.get(
        "/whoami",
        headers={
            "x-catapult-tenant-id": "  acme  ",
            "x-catapult-user-id": "user-42",
            "x-catapult-app-id": "support-bot",
            "x-catapult-request-id": "req-xyz",
            "x-catapult-trace-id": "trace-abc",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "acme"
    assert body["user_id"] == "user-42"
    assert body["app_id"] == "support-bot"
    assert body["request_id"] == "req-xyz"
    assert body["trace_id"] == "trace-abc"
