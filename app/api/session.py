"""Single endpoint the frontend calls on login to decide what screen to show.

Returns one ``action`` value — the frontend never needs to interpret multiple
flags or call multiple status endpoints to figure out the user's state.
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_tenant_user
from app.db.models import Chunk, Document
from app.db.models_pipeline import PipelineState
from app.db.session import get_db
from app.services.drive.token_store import (
    TOKEN_STORE,
    drive_has_credentials_in_db,
    ensure_tokens_loaded,
)
from app.services.storage import list_files_recursive

router = APIRouter(tags=["Session"])

_RAW_SUFFIXES = (".txt", ".csv", ".pdf", ".xlsx")


def _count_raw_files(user_id: str) -> int:
    raw_dir = os.path.join("data", f"user_{user_id}", "raw")
    if not os.path.isdir(raw_dir):
        return 0
    return sum(
        1 for p in list_files_recursive(raw_dir) if p.lower().endswith(_RAW_SUFFIXES)
    )


def _parse_progress(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    cur = raw.get("current")
    tot = raw.get("total")
    pct = None
    if isinstance(cur, int) and isinstance(tot, int) and tot > 0:
        pct = round(100.0 * min(cur, tot) / tot, 1)
    return {
        "current": cur,
        "total": tot,
        "percent": pct,
        "current_file": raw.get("current_file"),
    }


@router.get("/session/state")
def get_session_state(
    response: Response,
    tenant_user: tuple[str, str] = Depends(get_tenant_user),
    db: Session = Depends(get_db),
):
    """
    Call this on login / page load. The ``action`` field tells the frontend
    exactly what to do next:

    - **ready** — user has indexed docs, go straight to chat.
    - **needs_oauth** — Drive is not connected, show the connect button.
    - **needs_sync** — Drive connected but no files synced yet, trigger sync.
    - **needs_index** — raw files exist but nothing indexed, trigger index.
    - **in_progress** — sync or index is running, show progress bar.
    """
    response.headers["Cache-Control"] = "no-store"

    tenant_id, user_id = tenant_user

    # --- Drive connection ---
    mem = user_id in TOKEN_STORE and bool(TOKEN_STORE.get(user_id))
    has_db_creds = drive_has_credentials_in_db(tenant_id, user_id)
    drive_connected = mem or has_db_creds
    if has_db_creds and not mem:
        ensure_tokens_loaded(tenant_id, user_id)

    # --- Indexed data ---
    indexed_docs = (
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

    # --- Pipeline state ---
    row = (
        db.query(PipelineState)
        .filter(
            PipelineState.tenant_id == tenant_id,
            PipelineState.user_id == user_id,
        )
        .first()
    )
    sync_status = row.drive_sync_status if row else "idle"
    index_status = row.index_status if row else "idle"

    # --- Decision ---

    if sync_status == "running":
        return {
            "action": "in_progress",
            "phase": "syncing",
            "progress": _parse_progress(
                row.drive_sync_progress_json if row else None
            ),
        }

    if index_status == "running":
        return {
            "action": "in_progress",
            "phase": "indexing",
            "progress": _parse_progress(
                row.index_progress_json if row else None
            ),
        }

    if indexed_chunks > 0:
        return {
            "action": "ready",
            "indexed_documents": indexed_docs,
            "indexed_chunks": indexed_chunks,
        }

    if not drive_connected:
        return {"action": "needs_oauth"}

    raw_files = _count_raw_files(user_id)

    if raw_files > 0:
        return {
            "action": "needs_index",
            "raw_files": raw_files,
        }

    return {"action": "needs_sync"}
