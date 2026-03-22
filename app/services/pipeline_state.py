"""Update pipeline_state rows for background Drive sync and indexing."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models_pipeline import PipelineState
from app.db.session import SessionLocal


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
        db.commit()
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
        db.commit()
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
        db.commit()
    finally:
        db.close()
