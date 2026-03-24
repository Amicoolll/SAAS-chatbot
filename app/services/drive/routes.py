import io
import logging
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends, Query
from fastapi.responses import FileResponse
from googleapiclient.http import MediaIoBaseDownload

from app.core.deps import get_tenant_user, get_user_id
from app.core.logging import log_operation
from app.services.drive.client import build_drive_service
from app.services.drive.token_store import TOKEN_STORE
from app.services import pipeline_state

router = APIRouter()
logger = logging.getLogger(__name__)

GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES = "application/vnd.google-apps.presentation"

SUPPORTED_TYPES = {
    GOOGLE_DOC,
    GOOGLE_SHEET,
    GOOGLE_SLIDES,
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}


def _download_bytes(request):
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()


def _safe_file_name(name: str) -> str:
    # Keep a conservative filename set for local storage and URL paths.
    return name.replace("/", "_").replace("\\", "_")


def _ext_from_mime(mime: str) -> str:
    if mime == "image/png":
        return ".png"
    if mime in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if mime == "image/webp":
        return ".webp"
    if mime == "image/gif":
        return ".gif"
    return ""


def list_all_files(service):
    all_files = []
    token = None
    while True:
        response = service.files().list(
            q="trashed=false",
            pageSize=200,
            pageToken=token,
            fields="nextPageToken, files(id,name,mimeType,modifiedTime,webViewLink)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        all_files.extend(response.get("files", []))
        token = response.get("nextPageToken")
        if not token:
            break
    return all_files


def _run_drive_sync_core(tenant_id: str, user_id: str, max_files: int) -> dict:
    """
    Download supported Drive files to data/user_<id>/raw.
    Raises ValueError if Drive not connected; other exceptions propagate.
    Updates pipeline_state drive sync progress for GET /pipeline/status polling.
    """
    tokens = TOKEN_STORE.get(user_id)
    if not tokens:
        raise ValueError("Drive not connected")

    pipeline_state.update_drive_sync_progress(
        tenant_id, user_id, phase="listing", current=0, total=0, current_file=None
    )

    service = build_drive_service(
        tokens["access_token"],
        tokens["refresh_token"],
    )
    files = list_all_files(service)
    files = [f for f in files if f["mimeType"] in SUPPORTED_TYPES][:max_files]
    total = len(files)

    pipeline_state.update_drive_sync_progress(
        tenant_id, user_id, phase="download", current=0, total=total, current_file=None
    )

    base_dir = os.path.join("data", f"user_{user_id}")
    raw_dir = os.path.join(base_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    processed = 0
    failed = 0
    errors: list[str] = []

    for i, f in enumerate(files, start=1):
        name_for_progress = f.get("name", f.get("id", "")).replace("/", "_")
        pipeline_state.update_drive_sync_progress(
            tenant_id,
            user_id,
            phase="download",
            current=i - 1,
            total=total,
            current_file=name_for_progress,
        )
        try:
            file_id = f["id"]
            name = _safe_file_name(f["name"])
            mime = f["mimeType"]

            if mime == GOOGLE_DOC:
                request = service.files().export_media(fileId=file_id, mimeType="text/plain")
                content = _download_bytes(request).decode("utf-8", errors="ignore")
                file_path = os.path.join(raw_dir, f"{name}.txt")
                with open(file_path, "w", encoding="utf-8") as f_out:
                    f_out.write(content)
            elif mime == GOOGLE_SHEET:
                request = service.files().export_media(fileId=file_id, mimeType="text/csv")
                content = _download_bytes(request).decode("utf-8", errors="ignore")
                file_path = os.path.join(raw_dir, f"{name}.csv")
                with open(file_path, "w", encoding="utf-8") as f_out:
                    f_out.write(content)
            elif mime == GOOGLE_SLIDES:
                request = service.files().export_media(fileId=file_id, mimeType="text/plain")
                content = _download_bytes(request).decode("utf-8", errors="ignore")
                file_path = os.path.join(raw_dir, f"{name}.txt")
                with open(file_path, "w", encoding="utf-8") as f_out:
                    f_out.write(content)
            else:
                request = service.files().get_media(fileId=file_id)
                content = _download_bytes(request)
                ext = ".pdf" if mime == "application/pdf" else _ext_from_mime(mime)
                file_path = os.path.join(raw_dir, f"{name}{ext}")
                with open(file_path, "wb") as f_out:
                    f_out.write(content)

            processed += 1
            pipeline_state.update_drive_sync_progress(
                tenant_id,
                user_id,
                phase="download",
                current=i,
                total=total,
                current_file=name,
            )
        except Exception as e:
            failed += 1
            fid = f.get("id", "?")
            err_msg = f"{f.get('name', fid)}: {type(e).__name__}: {e!s}"
            errors.append(err_msg[:200])
            logger.warning("drive_sync_file_failed user_id=%s file=%s error=%s", user_id, f.get("name"), e)
            pipeline_state.update_drive_sync_progress(
                tenant_id,
                user_id,
                phase="download",
                current=i,
                total=total,
                current_file=f.get("name", file_id),
            )

    log_operation(logger, "drive_sync", user_id=user_id, processed=processed, failed=failed)
    return {
        "processed": processed,
        "failed": failed,
        "total_planned": total,
        "saved_in": raw_dir,
        "errors_preview": errors[:10] if errors else None,
    }


def _drive_sync_background(tenant_id: str, user_id: str, max_files: int) -> None:
    pipeline_state.mark_drive_sync_running(tenant_id, user_id)
    try:
        result = _run_drive_sync_core(tenant_id, user_id, max_files)
        pipeline_state.mark_drive_sync_success(tenant_id, user_id, result)
    except ValueError as e:
        pipeline_state.mark_drive_sync_error(tenant_id, user_id, str(e))
    except Exception as e:
        logger.exception("drive_sync_background_failed tenant=%s user=%s", tenant_id, user_id)
        pipeline_state.mark_drive_sync_error(tenant_id, user_id, str(e))


@router.get("/drive/files")
def drive_files(user_id: str = Depends(get_user_id)):
    tokens = TOKEN_STORE.get(user_id)
    if not tokens:
        raise HTTPException(status_code=401, detail="Drive not connected")

    service = build_drive_service(
        tokens["access_token"],
        tokens["refresh_token"],
    )
    files = list_all_files(service)
    filtered = [f for f in files if f["mimeType"] in SUPPORTED_TYPES]
    return {"total_files": len(filtered), "files_preview": filtered[:20]}


@router.post("/drive/sync")
def drive_sync(
    background_tasks: BackgroundTasks,
    tenant_user: tuple[str, str] = Depends(get_tenant_user),
    max_files: int = Query(
        500,
        ge=1,
        le=5000,
        description="Max Drive files to download this run (supported types only). Progress total matches this batch.",
    ),
    background: bool = True,
):
    """Sync Drive files to local raw folder. If background=True (default), updates pipeline_state when done.

    Poll **GET /pipeline/status** for `drive_sync.progress` (`current` / `total` / `current_file` / `percent`).
    """
    tenant_id, user_id = tenant_user
    tokens = TOKEN_STORE.get(user_id)
    if not tokens:
        raise HTTPException(status_code=401, detail="Drive not connected")

    if background:
        background_tasks.add_task(_drive_sync_background, tenant_id, user_id, max_files)
        return {
            "status": "accepted",
            "message": "Sync started in background. Poll GET /pipeline/status until drive_sync is not running.",
            "tenant_id": tenant_id,
            "user_id": user_id,
        }
    pipeline_state.mark_drive_sync_running(tenant_id, user_id)
    try:
        result = _run_drive_sync_core(tenant_id, user_id, max_files)
        pipeline_state.mark_drive_sync_success(tenant_id, user_id, result)
        return result
    except ValueError as e:
        pipeline_state.mark_drive_sync_error(tenant_id, user_id, str(e))
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        pipeline_state.mark_drive_sync_error(tenant_id, user_id, str(e))
        raise HTTPException(status_code=500, detail="Drive sync failed") from e


@router.get("/drive/images")
def drive_images(user_id: str = Depends(get_user_id)):
    """List synced image assets from data/user_<id>/raw for frontend rendering."""
    raw_dir = os.path.join("data", f"user_{user_id}", "raw")
    if not os.path.isdir(raw_dir):
        return {"total_images": 0, "images": []}

    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    names = []
    for p in Path(raw_dir).iterdir():
        if p.is_file() and p.suffix.lower() in image_exts:
            names.append(p.name)
    names.sort()
    items = [
        {"name": n, "url": f"/drive/images/{quote(n)}"}
        for n in names[:500]
    ]
    return {"total_images": len(names), "images": items}


@router.get("/drive/images/{image_name:path}")
def drive_image_file(image_name: str, user_id: str = Depends(get_user_id)):
    """Serve a synced image file by name for the current user."""
    safe_name = os.path.basename(image_name)
    if safe_name != image_name:
        raise HTTPException(status_code=400, detail="Invalid image name")
    raw_dir = os.path.join("data", f"user_{user_id}", "raw")
    file_path = os.path.join(raw_dir, safe_name)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise HTTPException(status_code=400, detail="Not an image file")
    return FileResponse(file_path)
