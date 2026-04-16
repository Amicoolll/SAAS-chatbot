"""Per-tenant feature flags with in-process TTL cache.

Usage:
    from app.services.feature_flags import is_enabled, set_enabled

    if is_enabled("acme", "web_search_fallback"):
        ...

The cache avoids a DB query on every chat turn. ``set_enabled`` writes to DB
and invalidates the cache entry immediately so the change takes effect.
"""

from __future__ import annotations

import logging
import time

from app.core.config import settings
from app.db.models_features import TenantFeatureFlag
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

_cache: dict[tuple[str, str], tuple[bool, float]] = {}


def _cache_key(tenant_id: str, flag_name: str) -> tuple[str, str]:
    return (tenant_id, flag_name)


def is_enabled(tenant_id: str, flag_name: str) -> bool:
    """Return True if *flag_name* is explicitly enabled for *tenant_id*.

    Results are cached in-process for ``FEATURE_FLAG_CACHE_TTL_SECONDS``.
    Unknown / missing flags default to **False** (safe default).
    """
    key = _cache_key(tenant_id, flag_name)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None:
        value, ts = cached
        if now - ts < settings.FEATURE_FLAG_CACHE_TTL_SECONDS:
            return value

    db = SessionLocal()
    try:
        row = (
            db.query(TenantFeatureFlag)
            .filter(
                TenantFeatureFlag.tenant_id == tenant_id,
                TenantFeatureFlag.flag_name == flag_name,
            )
            .first()
        )
        value = bool(row and row.enabled)
    except Exception:
        logger.exception(
            "feature_flag_read_failed tenant=%s flag=%s", tenant_id, flag_name
        )
        value = False
    finally:
        db.close()

    _cache[key] = (value, now)
    return value


def set_enabled(tenant_id: str, flag_name: str, enabled: bool) -> None:
    """Write *enabled* to DB and invalidate the cache entry."""
    db = SessionLocal()
    try:
        row = (
            db.query(TenantFeatureFlag)
            .filter(
                TenantFeatureFlag.tenant_id == tenant_id,
                TenantFeatureFlag.flag_name == flag_name,
            )
            .first()
        )
        if row:
            row.enabled = enabled
        else:
            db.add(
                TenantFeatureFlag(
                    tenant_id=tenant_id,
                    flag_name=flag_name,
                    enabled=enabled,
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "feature_flag_write_failed tenant=%s flag=%s", tenant_id, flag_name
        )
        raise
    finally:
        db.close()

    _cache.pop(_cache_key(tenant_id, flag_name), None)


def list_flags(tenant_id: str) -> list[dict]:
    """Return all flags for *tenant_id* as a list of dicts."""
    db = SessionLocal()
    try:
        rows = (
            db.query(TenantFeatureFlag)
            .filter(TenantFeatureFlag.tenant_id == tenant_id)
            .all()
        )
        return [
            {
                "flag_name": r.flag_name,
                "enabled": r.enabled,
                "updated_at": str(r.updated_at) if r.updated_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


def invalidate_cache() -> None:
    """Clear the entire in-process cache (useful in tests)."""
    _cache.clear()
