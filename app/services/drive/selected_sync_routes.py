import os
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.deps import get_tenant_user
from app.core.logging import log_operation
from app.services import pipeline_state
from app.services.drive.client import build_drive_service
from app.services.drive.token_store import TOKEN_STORE, ensure_tokens_loaded

# Reuse download helpers + supported mime types from the existing sync router.
from app.services.drive.routes import (  # noqa: E402
    GOOGLE_DOC,
    GOOGLE_SHEET,
    GOOGLE_SLIDES,
    SUPPORTED_TYPES,
    _download_bytes,
    _ext_from_mime,
    _save_google_sheet,
    _safe_file_name,
)

router = APIRouter(tags=["Drive (selected)"])
logger = logging.getLogger(__name__)


class SelectedSyncRequest(BaseModel):
    file_ids: list[str] = Field(
        ...,
        min_items=1,
        description="Google Drive file IDs selected by the frontend.",
    )


def _run_drive_selected_sync_core(
    tenant_id: str,
    user_id: str,
    file_ids: list[str],
) -> dict[str, Any]:
    """
    Download only the selected Drive file IDs.
    Used for frontend-driven selection; keeps the same raw folder layout as /drive/sync.
    """
    if not ensure_tokens_loaded(tenant_id, user_id):
        raise ValueError("Drive not connected")

    tokens = TOKEN_STORE.get(user_id)
    if not tokens:
        raise ValueError("Drive not connected")

    service = build_drive_service(tokens["access_token"], tokens["refresh_token"])

    base_dir = os.path.join("data", f"user_{user_id}")
    raw_dir = os.path.join(base_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    # Metadata pass first so pipeline totals reflect actual downloads.
    selected_files: list[dict[str, str]] = []
    skipped_unsupported = 0
    failed = 0
    errors: list[str] = []

    for file_id in file_ids:
        try:
            meta = (
                service.files()
                .get(fileId=file_id, fields="id,name,mimeType")
                .execute()
            )
            mime = meta.get("mimeType")
            if mime not in SUPPORTED_TYPES:
                skipped_unsupported += 1
                continue
            selected_files.append(
                {"id": meta["id"], "name": str(meta.get("name") or meta["id"]), "mimeType": mime}
            )
        except Exception as e:
            failed += 1
            errors.append(f"{file_id}: {type(e).__name__}: {e!s}")

    total = len(selected_files)
    pipeline_state.update_drive_sync_progress(
        tenant_id,
        user_id,
        phase="download",
        current=0,
        total=total,
        current_file=None,
    )

    processed = 0
    for i, f in enumerate(selected_files, start=1):
        safe_name = _safe_file_name(f["name"])
        pipeline_state.update_drive_sync_progress(
            tenant_id,
            user_id,
            phase="download",
            current=i - 1,
            total=total,
            current_file=safe_name,
        )

        try:
            file_id = f["id"]
            mime = f["mimeType"]
            dl_ctx = f"selected/{i}/{total}:{file_id}:{safe_name[:80]}"

            if mime == GOOGLE_DOC:
                request = service.files().export_media(
                    fileId=file_id, mimeType="text/plain"
                )
                content = _download_bytes(request, log_context=f"{dl_ctx}:gdoc").decode(
                    "utf-8", errors="ignore"
                )
                file_path = os.path.join(raw_dir, f"{safe_name}.txt")
                with open(file_path, "w", encoding="utf-8") as f_out:
                    f_out.write(content)
            elif mime == GOOGLE_SHEET:
                _save_google_sheet(
                    service,
                    file_id,
                    safe_name,
                    raw_dir,
                    log_context=dl_ctx,
                )
            elif mime == GOOGLE_SLIDES:
                request = service.files().export_media(
                    fileId=file_id, mimeType="text/plain"
                )
                content = _download_bytes(request, log_context=f"{dl_ctx}:gslides").decode(
                    "utf-8", errors="ignore"
                )
                file_path = os.path.join(raw_dir, f"{safe_name}.txt")
                with open(file_path, "w", encoding="utf-8") as f_out:
                    f_out.write(content)
            else:
                request = service.files().get_media(fileId=file_id)
                content = _download_bytes(request, log_context=f"{dl_ctx}:binary")
                ext = ".pdf" if mime == "application/pdf" else _ext_from_mime(mime)
                file_path = os.path.join(raw_dir, f"{safe_name}{ext}")
                with open(file_path, "wb") as f_out:
                    f_out.write(content)

            processed += 1
            pipeline_state.update_drive_sync_progress(
                tenant_id,
                user_id,
                phase="download",
                current=i,
                total=total,
                current_file=safe_name,
            )
        except Exception as e:
            failed += 1
            err_msg = f"{f.get('name', file_id)}: {type(e).__name__}: {e!s}"
            errors.append(err_msg)
            logger.warning("drive_selected_sync_file_failed user=%s file=%s error=%s", user_id, file_id, e)
            pipeline_state.update_drive_sync_progress(
                tenant_id,
                user_id,
                phase="download",
                current=i,
                total=total,
                current_file=safe_name,
            )

    log_operation(
        logger,
        "drive_selected_sync",
        user_id=user_id,
        requested=len(file_ids),
        processed=processed,
        failed=failed,
        skipped_unsupported=skipped_unsupported,
    )
    return {
        "requested": len(file_ids),
        "processed": processed,
        "failed": failed,
        "skipped_unsupported": skipped_unsupported,
        "total_downloaded": total,
        "saved_in": raw_dir,
        "errors_preview": errors[:10] if errors else None,
    }


def _drive_selected_sync_background(
    tenant_id: str,
    user_id: str,
    file_ids: list[str],
) -> None:
    pipeline_state.mark_drive_sync_running(tenant_id, user_id)
    try:
        result = _run_drive_selected_sync_core(tenant_id, user_id, file_ids)
        pipeline_state.mark_drive_sync_success(tenant_id, user_id, result)
    except ValueError as e:
        pipeline_state.mark_drive_sync_error(tenant_id, user_id, str(e))
    except Exception as e:
        logger.exception("drive_selected_sync_background_failed tenant=%s user=%s", tenant_id, user_id)
        pipeline_state.mark_drive_sync_error(tenant_id, user_id, str(e))


@router.post("/drive/sync/selected")
def drive_sync_selected(
    background_tasks: BackgroundTasks,
    tenant_user: tuple[str, str] = Depends(get_tenant_user),
    body: SelectedSyncRequest = Body(...),
    background: bool = True,
):
    """
    Frontend-driven sync for the selected Drive file IDs.
    Downloads only supported types into `data/user_<id>/raw/`.
    """
    tenant_id, user_id = tenant_user
    if not ensure_tokens_loaded(tenant_id, user_id):
        raise HTTPException(status_code=401, detail="Drive not connected")
    if not body.file_ids:
        raise HTTPException(status_code=400, detail="file_ids must not be empty")

    # Safety cap to prevent extremely large payloads / long jobs.
    # Note: pipeline currently expects bounded work.
    file_ids = body.file_ids[:5000]

    if background:
        background_tasks.add_task(
            _drive_selected_sync_background, tenant_id, user_id, file_ids
        )
        return {
            "status": "accepted",
            "message": "Selected sync started in background. Poll GET /pipeline/status until done.",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "requested": len(body.file_ids),
            "capped_to": len(file_ids),
        }

    pipeline_state.mark_drive_sync_running(tenant_id, user_id)
    try:
        result = _run_drive_selected_sync_core(tenant_id, user_id, file_ids)
        pipeline_state.mark_drive_sync_success(tenant_id, user_id, result)
        return result
    except ValueError as e:
        pipeline_state.mark_drive_sync_error(tenant_id, user_id, str(e))
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        pipeline_state.mark_drive_sync_error(tenant_id, user_id, str(e))
        raise HTTPException(status_code=500, detail="Selected drive sync failed") from e

