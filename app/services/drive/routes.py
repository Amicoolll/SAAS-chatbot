import os
import io
from fastapi import APIRouter, HTTPException
from googleapiclient.http import MediaIoBaseDownload
from app.services.drive.token_store import TOKEN_STORE
from app.services.drive.client import build_drive_service

router = APIRouter()

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


@router.get("/drive/files")
def drive_files(user_id: str = "demo_user"):
    tokens = TOKEN_STORE.get(user_id)
    if not tokens:
        raise HTTPException(status_code=401, detail="Drive not connected")

    service = build_drive_service(
        tokens["access_token"],
        tokens["refresh_token"],
    )

    files = list_all_files(service)
    filtered = [f for f in files if f["mimeType"] in SUPPORTED_TYPES]

    return {
        "total_files": len(filtered),
        "files_preview": filtered[:20],
    }


@router.post("/drive/sync")
def drive_sync(user_id: str = "demo_user", max_files: int = 20):
    tokens = TOKEN_STORE.get(user_id)
    if not tokens:
        raise HTTPException(status_code=401, detail="Drive not connected")

    service = build_drive_service(
        tokens["access_token"],
        tokens["refresh_token"],
    )

    files = list_all_files(service)
    files = [f for f in files if f["mimeType"] in SUPPORTED_TYPES][:max_files]

    # 🔹 CLEAN FOLDER STRUCTURE
    base_dir = os.path.join("data", f"user_{user_id}")
    raw_dir = os.path.join(base_dir, "raw")

    os.makedirs(raw_dir, exist_ok=True)

    processed = 0
    failed = 0

    for f in files:
        try:
            file_id = f["id"]
            name = f["name"].replace("/", "_")
            mime = f["mimeType"]

            if mime == GOOGLE_DOC:
                request = service.files().export_media(
                    fileId=file_id,
                    mimeType="text/plain",
                )
                content = _download_bytes(request).decode("utf-8", errors="ignore")
                file_path = os.path.join(raw_dir, f"{name}.txt")

                with open(file_path, "w", encoding="utf-8") as f_out:
                    f_out.write(content)

            elif mime == GOOGLE_SHEET:
                request = service.files().export_media(
                    fileId=file_id,
                    mimeType="text/csv",
                )
                content = _download_bytes(request).decode("utf-8", errors="ignore")
                file_path = os.path.join(raw_dir, f"{name}.csv")

                with open(file_path, "w", encoding="utf-8") as f_out:
                    f_out.write(content)

            elif mime == GOOGLE_SLIDES:
                request = service.files().export_media(
                    fileId=file_id,
                    mimeType="text/plain",
                )
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

    return {
        "processed": processed,
        "failed": failed,
        "saved_in": raw_dir,
    }