"""Dependencies for tenant/user identity. Wire to JWT/SSO later."""
from fastapi import Header, Request

from app.core.config import settings


def get_tenant_id(
    x_tenant_id: str | None = Header(None, alias="X-Tenant-Id"),
) -> str:
    return (x_tenant_id or "").strip() or settings.DEFAULT_TENANT_ID


def get_user_id(
    x_user_id: str | None = Header(None, alias="X-User-Id"),
) -> str:
    return (x_user_id or "").strip() or settings.DEFAULT_USER_ID


def get_tenant_user(
    x_tenant_id: str | None = Header(None, alias="X-Tenant-Id"),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
) -> tuple[str, str]:
    return (
        (x_tenant_id or "").strip() or settings.DEFAULT_TENANT_ID,
        (x_user_id or "").strip() or settings.DEFAULT_USER_ID,
    )
