"""Update pipeline_state rows for background Drive sync and indexing."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models_pipeline import PipelineState
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create(db: Session, tenant_id: str, user_id: str) -> PipelineState:
    row = (
        db.query(PipelineState)
        .filter(PipelineState.tenant_id == tenant_id, PipelineState.user_id == user_id)
        .first()
    )
    if not row:
        row = PipelineState(tenant_id=tenant_id, user_id=user_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def mark_drive_sync_running(tenant_id: str, user_id: str) -> None:
    db = SessionLocal()
    try:
        row = _get_or_create(db, tenant_id, user_id)
        row.drive_sync_status = "running"
        row.drive_sync_started_at = _now()
        row.drive_sync_finished_at = None
        row.drive_sync_error = None
        row.drive_sync_result_json = None
        row.drive_sync_progress_json = json.dumps(
            {
                "phase": "starting",
                "current": 0,
                "total": None,
                "current_file": None,
            }
        )
        db.commit()
    finally:
        db.close()


def update_drive_sync_progress(
    tenant_id: str,
    user_id: str,
    *,
    phase: str,
    current: int,
    total: int | None,
    current_file: str | None = None,
) -> None:
    """Best-effort progress for polling (separate DB session)."""
    db = SessionLocal()
    try:
        row = (
            db.query(PipelineState)
            .filter(PipelineState.tenant_id == tenant_id, PipelineState.user_id == user_id)
            .first()
        )
        if not row:
            logger.warning(
                "drive_sync_progress_skip no_row tenant=%s user=%s phase=%s",
                tenant_id,
                user_id,
                phase,
            )
            return
        if row.drive_sync_status != "running":
            logger.debug(
                "drive_sync_progress_skip status=%s tenant=%s user=%s",
                row.drive_sync_status,
                tenant_id,
                user_id,
            )
            return
        row.drive_sync_progress_json = json.dumps(
            {
                "phase": phase,
                "current": current,
                "total": total,
                "current_file": current_file,
            },
            default=str,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(
            "drive_sync_progress_write_failed tenant=%s user=%s: %s",
            tenant_id,
            user_id,
            e,
            exc_info=True,
        )
    finally:
        db.close()


def mark_drive_sync_success(tenant_id: str, user_id: str, result: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        row = _get_or_create(db, tenant_id, user_id)
        row.drive_sync_status = "success"
        row.drive_sync_finished_at = _now()
        row.drive_sync_error = None
        row.drive_sync_result_json = json.dumps(result, default=str)
        row.drive_sync_progress_json = None
        db.commit()
    finally:
        db.close()


def mark_drive_sync_error(tenant_id: str, user_id: str, message: str) -> None:
    db = SessionLocal()
    try:
        row = _get_or_create(db, tenant_id, user_id)
        row.drive_sync_status = "error"
        row.drive_sync_finished_at = _now()
        row.drive_sync_error = message[:4000]
        row.drive_sync_progress_json = None
        db.commit()
    finally:
        db.close()


def mark_index_running(tenant_id: str, user_id: str) -> None:
    db = SessionLocal()
    try:
        row = _get_or_create(db, tenant_id, user_id)
        row.index_status = "running"
        row.index_started_at = _now()
        row.index_finished_at = None
        row.index_error = None
        row.index_result_json = None
        row.index_progress_json = json.dumps(
            {"phase": "starting", "current": 0, "total": None, "current_file": None}
        )
        db.commit()
    finally:
        db.close()


def update_index_progress(
    tenant_id: str,
    user_id: str,
    *,
    phase: str,
    current: int,
    total: int | None,
    current_file: str | None = None,
    chunks_so_far: int | None = None,
) -> None:
    """Best-effort progress for polling (separate DB session)."""
    db = SessionLocal()
    try:
        row = (
            db.query(PipelineState)
            .filter(PipelineState.tenant_id == tenant_id, PipelineState.user_id == user_id)
            .first()
        )
        if not row:
            logger.warning(
                "index_progress_skip no_row tenant=%s user=%s", tenant_id, user_id
            )
            return
        if row.index_status != "running":
            logger.debug(
                "index_progress_skip status=%s tenant=%s user=%s",
                row.index_status,
                tenant_id,
                user_id,
            )
            return
        payload: dict[str, Any] = {
            "phase": phase,
            "current": current,
            "total": total,
            "current_file": current_file,
        }
        if chunks_so_far is not None:
            payload["chunks_so_far"] = chunks_so_far
        row.index_progress_json = json.dumps(payload, default=str)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(
            "index_progress_write_failed tenant=%s user=%s: %s",
            tenant_id,
            user_id,
            e,
            exc_info=True,
        )
    finally:
        db.close()


def mark_index_success(tenant_id: str, user_id: str, result: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        row = _get_or_create(db, tenant_id, user_id)
        row.index_status = "success"
        row.index_finished_at = _now()
        row.index_error = None
        row.index_result_json = json.dumps(result, default=str)
        row.index_progress_json = None
        db.commit()
    finally:
        db.close()


def mark_index_error(tenant_id: str, user_id: str, message: str) -> None:
    db = SessionLocal()
    try:
        row = _get_or_create(db, tenant_id, user_id)
        row.index_status = "error"
        row.index_finished_at = _now()
        row.index_error = message[:4000]
        row.index_progress_json = None
        db.commit()
    finally:
        db.close()
