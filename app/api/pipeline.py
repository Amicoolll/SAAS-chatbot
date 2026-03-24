"""Pollable status for Drive sync + indexing (background jobs)."""
from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_tenant_user
from app.db.models import Chunk, Document
from app.db.models_pipeline import PipelineState
from app.db.session import get_db
from app.services.drive.token_store import TOKEN_STORE
from app.services.storage import list_files_recursive

router = APIRouter(tags=["Pipeline status"])


def _safe_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _progress_payload(text: str | None) -> dict[str, Any] | None:
    """Parse stored progress JSON and add `percent` for UI bars."""
    raw = _safe_json(text)
    if not raw or not isinstance(raw, dict):
        return None
    cur = raw.get("current")
    tot = raw.get("total")
    pct: float | None = None
    if isinstance(cur, int) and isinstance(tot, int) and tot > 0:
        pct = round(100.0 * min(cur, tot) / tot, 1)
    elif isinstance(cur, int) and isinstance(tot, int) and tot == 0 and cur == 0:
        pct = 0.0
    out = dict(raw)
    out["percent"] = pct
    return out


@router.get("/pipeline/status")
def get_pipeline_status(
    tenant_user: tuple[str, str] = Depends(get_tenant_user),
    db: Session = Depends(get_db),
):
    """
    Use this after POST /drive/sync?background=true or POST /index/run?background=true.

    Poll every 1–3s while jobs run. When `drive_sync.running` or `index.running` is true, check
    `drive_sync.progress` / `index.progress` for live counts:

    - `current` / `total` — e.g. 120 of 385 files (sync uses Drive batch from `max_files`; index uses raw files up to `max_files`)
    - `percent` — 0–100 when `total` is known
    - `current_file` — file being processed (when set)
    - `chunks_so_far` — during indexing only

    Until complete:
    - wait for `drive_sync.running` false before starting index (recommended)
    - wait for `index.running` false before expecting chat to see new documents

    `ready_for_chat` is true when there is at least one indexed chunk and indexing is not running.
    """
    tenant_id, user_id = tenant_user

    drive_connected = user_id in TOKEN_STORE and bool(TOKEN_STORE.get(user_id))

    raw_dir = os.path.join("data", f"user_{user_id}", "raw")
    raw_text_csv_files = 0
    if os.path.isdir(raw_dir):
        for p in list_files_recursive(raw_dir):
            if p.endswith(".txt") or p.endswith(".csv"):
                raw_text_csv_files += 1

    indexed_documents = (
        db.query(func.count(Document.id))
        .filter(Document.tenant_id == tenant_id, Document.user_id == user_id)
        .scalar()
        or 0
    )
    indexed_chunks = (
        db.query(func.count(Chunk.id))
        .filter(Chunk.tenant_id == tenant_id, Chunk.user_id == user_id)
        .scalar()
        or 0
    )

    row = (
        db.query(PipelineState)
        .filter(PipelineState.tenant_id == tenant_id, PipelineState.user_id == user_id)
        .first()
    )

    drive_status = row.drive_sync_status if row else "idle"
    index_status = row.index_status if row else "idle"

    sync_running = drive_status == "running"
    index_running = index_status == "running"

    hints: list[str] = []
    if not drive_connected:
        hints.append("Connect Google Drive (OAuth) before sync.")
    if sync_running:
        hints.append("Drive sync is running; wait before indexing.")
    elif raw_text_csv_files == 0 and drive_connected:
        hints.append("No .txt/.csv files in raw folder yet; run /drive/sync or check failures in drive_sync.")
    if index_running:
        hints.append("Indexing is running; answers may not include new documents until it finishes.")
    if indexed_chunks == 0 and not index_running and raw_text_csv_files > 0:
        hints.append("Run POST /index/run to embed documents for chat.")
    if indexed_chunks == 0 and not index_running and raw_text_csv_files == 0:
        hints.append("Sync and index before chat for knowledge-grounded answers.")

    ready_for_index = (not sync_running) and raw_text_csv_files > 0 and (not index_running)
    ready_for_chat = indexed_chunks > 0 and (not index_running)

    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "drive_connected": drive_connected,
        "raw_text_csv_files": raw_text_csv_files,
        "indexed_documents": int(indexed_documents),
        "indexed_chunks": int(indexed_chunks),
        "drive_sync": {
            "status": drive_status,
            "running": sync_running,
            "started_at": row.drive_sync_started_at.isoformat() if row and row.drive_sync_started_at else None,
            "finished_at": row.drive_sync_finished_at.isoformat() if row and row.drive_sync_finished_at else None,
            "progress": _progress_payload(row.drive_sync_progress_json) if row else None,
            "result": _safe_json(row.drive_sync_result_json) if row else None,
            "error": row.drive_sync_error if row else None,
        },
        "index": {
            "status": index_status,
            "running": index_running,
            "started_at": row.index_started_at.isoformat() if row and row.index_started_at else None,
            "finished_at": row.index_finished_at.isoformat() if row and row.index_finished_at else None,
            "progress": _progress_payload(row.index_progress_json) if row else None,
            "result": _safe_json(row.index_result_json) if row else None,
            "error": row.index_error if row else None,
        },
        "ready_for_index": ready_for_index,
        "ready_for_chat": ready_for_chat,
        "hints": hints,
    }
