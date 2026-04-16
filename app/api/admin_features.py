"""Admin endpoints for per-tenant feature flags.

Protected by ``X-Admin-Token`` header matched against ``settings.ADMIN_TOKEN``.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.services.feature_flags import is_enabled, list_flags, set_enabled

router = APIRouter(prefix="/admin", tags=["Admin — Feature Flags"])


def _require_admin_token(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
) -> None:
    if not settings.ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_TOKEN not configured on the server.",
        )
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token.")


class _SetFlagBody(BaseModel):
    enabled: bool


@router.get("/tenants/{tenant_id}/features")
def get_tenant_features(
    tenant_id: str,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
):
    _require_admin_token(x_admin_token)
    return {"tenant_id": tenant_id, "features": list_flags(tenant_id)}


@router.put("/tenants/{tenant_id}/features/{flag_name}")
def put_tenant_feature(
    tenant_id: str,
    flag_name: str,
    body: _SetFlagBody,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
):
    _require_admin_token(x_admin_token)
    set_enabled(tenant_id, flag_name, body.enabled)
    return {
        "tenant_id": tenant_id,
        "flag_name": flag_name,
        "enabled": body.enabled,
        "check": is_enabled(tenant_id, flag_name),
    }
