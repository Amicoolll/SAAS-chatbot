"""Catapult request-context resolution.

Reads ``x-catapult-*`` headers and returns the identity tuple the underlying
``app.db`` tables expect: ``(tenant_id, user_id)``.

Defaults match ``app/core/deps.py`` so a request with no Catapult headers
(local curl, smoke tests) still works.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header


_DEFAULT_TENANT_ID = "demo_tenant"
_DEFAULT_USER_ID = "demo_user"


@dataclass(frozen=True)
class CatapultContext:
    tenant_id: str
    user_id: str
    request_id: str
    trace_id: str | None
    app_id: str | None


def resolve_context(
    x_catapult_tenant_id: str | None = Header(default=None),
    x_catapult_user_id: str | None = Header(default=None),
    x_catapult_app_id: str | None = Header(default=None),
    x_catapult_request_id: str | None = Header(default=None),
    x_catapult_trace_id: str | None = Header(default=None),
) -> CatapultContext:
    return CatapultContext(
        tenant_id=(x_catapult_tenant_id or _DEFAULT_TENANT_ID).strip(),
        user_id=(x_catapult_user_id or _DEFAULT_USER_ID).strip(),
        request_id=(x_catapult_request_id or "").strip(),
        trace_id=(x_catapult_trace_id or None),
        app_id=(x_catapult_app_id or None),
    )


def trace_headers(ctx: CatapultContext) -> dict[str, str] | None:
    """Headers to propagate to downstream calls (OpenAI, Tavily) so platform
    observability can stitch the entire trace together.

    Per Catapult submission guide §7.3: "Propagate x-catapult-trace-id to any
    downstream calls."

    Returns ``None`` (not an empty dict) when there's no trace context — the
    underlying clients treat ``None`` as "no extra headers", which keeps the
    behavior identical to in-app callers that don't pass anything.
    """
    headers: dict[str, str] = {}
    if ctx.trace_id:
        headers["X-Trace-Id"] = ctx.trace_id
    if ctx.request_id:
        headers["X-Request-Id"] = ctx.request_id
    return headers or None
