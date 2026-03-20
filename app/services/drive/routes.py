import logging
import os
import io
from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from googleapiclient.http import MediaIoBaseDownload

from app.core.deps import get_user_id
from app.core.logging import log_operation
from app.services.drive.client import build_drive_service
from app.services.drive.token_store import TOKEN_STORE

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
}


def _download_bytes(request):
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()


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


def _run_drive_sync(user_id: str, max_files: int) -> dict:
    """Perform Drive sync and return result. Used by route and background task."""
    tokens = TOKEN_STORE.get(user_id)
    if not tokens:
        raise HTTPException(status_code=401, detail="Drive not connected")

    service = build_drive_service(
        tokens["access_token"],
        tokens["refresh_token"],
    )
    files = list_all_files(service)
    files = [f for f in files if f["mimeType"] in SUPPORTED_TYPES][:max_files]

    base_dir = os.path.join("data", f"user_{user_id}")
    raw_dir = os.path.join(base_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    processed = 0
    failed = 0
    errors: list[str] = []

    for f in files:
        try:
            file_id = f["id"]
            name = f["name"].replace("/", "_")
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
                file_path = os.path.join(raw_dir, f"{name}.pdf")
                with open(file_path, "wb") as f_out:
                    f_out.write(content)

            processed += 1
        except Exception as e:
            failed += 1
            err_msg = f"{f.get('name', file_id)}: {type(e).__name__}: {e!s}"
            errors.append(err_msg[:200])
            logger.warning("drive_sync_file_failed user_id=%s file=%s error=%s", user_id, f.get("name"), e)

    log_operation(logger, "drive_sync", user_id=user_id, processed=processed, failed=failed)
    return {
        "processed": processed,
        "failed": failed,
        "saved_in": raw_dir,
        "errors_preview": errors[:10] if errors else None,
    }


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
    user_id: str = Depends(get_user_id),
    max_files: int = 20,
    background: bool = True,
):
    """Sync Drive files to local raw folder. If background=True (default), returns 202 and runs in background."""
    tokens = TOKEN_STORE.get(user_id)
    if not tokens:
        raise HTTPException(status_code=401, detail="Drive not connected")

    if background:
        background_tasks.add_task(_run_drive_sync, user_id, max_files)
        return {"status": "accepted", "message": "Sync started in background.", "user_id": user_id}
    return _run_drive_sync(user_id, max_files)
