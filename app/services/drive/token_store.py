"""
Per-process cache of OAuth tokens + Postgres backing store for all workers.

TOKEN_STORE[user_id] is still used by Drive routes for compatibility.
Multi-worker: polling may hit a worker with an empty cache — call ensure_tokens_loaded()
before using tokens; pipeline status uses DB for drive_connected.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models_drive_oauth import DriveOAuthToken
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

TOKEN_STORE: dict[str, dict] = {}


def _merge_refresh_token(
    db: Session, tenant_id: str, user_id: str, new_refresh: str | None
) -> str | None:
    if new_refresh:
        return new_refresh
    row = (
        db.query(DriveOAuthToken)
        .filter(
            DriveOAuthToken.tenant_id == tenant_id,
            DriveOAuthToken.user_id == user_id,
        )
        .first()
    )
    return row.refresh_token if row else None


def persist_and_cache_tokens(
    tenant_id: str,
    user_id: str,
    access_token: str,
    refresh_token: str | None,
) -> None:
    """Upsert DB row and refresh in-memory cache."""
    db = SessionLocal()
    try:
        refresh_token = _merge_refresh_token(db, tenant_id, user_id, refresh_token)
        row = (
            db.query(DriveOAuthToken)
            .filter(
                DriveOAuthToken.tenant_id == tenant_id,
                DriveOAuthToken.user_id == user_id,
            )
            .first()
        )
        if row:
            row.access_token = access_token
            if refresh_token:
                row.refresh_token = refresh_token
        else:
            db.add(
                DriveOAuthToken(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    access_token=access_token,
                    refresh_token=refresh_token,
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("persist_drive_oauth_failed tenant=%s user=%s", tenant_id, user_id)
        raise
    finally:
        db.close()

    TOKEN_STORE[user_id] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
    }


def ensure_tokens_loaded(tenant_id: str, user_id: str) -> bool:
    """
    If this worker has no cache entry, load tokens from DB into TOKEN_STORE.
    Returns True if tokens are available in memory after the call.
    """
    cached = TOKEN_STORE.get(user_id)
    if cached and (
        cached.get("refresh_token") or cached.get("access_token")
    ):
        return True

    db = SessionLocal()
    try:
        row = (
            db.query(DriveOAuthToken)
            .filter(
                DriveOAuthToken.tenant_id == tenant_id,
                DriveOAuthToken.user_id == user_id,
            )
            .first()
        )
        if not row:
            return False
        TOKEN_STORE[user_id] = {
            "access_token": row.access_token,
            "refresh_token": row.refresh_token,
        }
        return bool(
            (row.refresh_token and str(row.refresh_token).strip())
            or (row.access_token and str(row.access_token).strip())
        )
    finally:
        db.close()


def drive_has_credentials_in_db(tenant_id: str, user_id: str) -> bool:
    """For /pipeline/status — true if OAuth was completed for this tenant/user."""
    db = SessionLocal()
    try:
        row = (
            db.query(DriveOAuthToken)
            .filter(
                DriveOAuthToken.tenant_id == tenant_id,
                DriveOAuthToken.user_id == user_id,
            )
            .first()
        )
        if not row:
            return False
        return bool(
            (row.refresh_token and str(row.refresh_token).strip())
            or (row.access_token and str(row.access_token).strip())
        )
    finally:
        db.close()
