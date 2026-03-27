import logging
import os
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_tenant_user
from app.db.models import Document, Chunk
from app.db.session import get_db
from app.api.index import _index_background_task, _run_index

router = APIRouter(tags=["Documents"])
logger = logging.getLogger(__name__)

ALLOWED_UPLOAD_EXTENSIONS = {".txt", ".csv", ".pdf", ".xlsx"}
MAX_UPLOAD_FILES_PER_REQUEST = 500
MAX_UPLOAD_BYTES_PER_FILE = 25 * 1024 * 1024


def _sanitize_upload_name(name: str) -> str:
    safe = os.path.basename(name or "").strip()
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid file name")
    return safe.replace("/", "_").replace("\\", "_")


def _resolve_raw_dir(user_id: str) -> str:
    raw_dir = os.path.join("data", f"user_{user_id}", "raw")
    os.makedirs(raw_dir, exist_ok=True)
    return raw_dir


def _save_uploaded_files(user_id: str, files: list[UploadFile]) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > MAX_UPLOAD_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files in one request. Max={MAX_UPLOAD_FILES_PER_REQUEST}.",
        )

    raw_dir = _resolve_raw_dir(user_id)
    saved = 0
    skipped_unsupported = 0
    skipped_invalid = 0
    errors: list[str] = []
    saved_files: list[str] = []

    for f in files:
        original_name = f.filename or ""
        try:
            safe_name = _sanitize_upload_name(original_name)
            ext = os.path.splitext(safe_name)[1].lower()
            if ext not in ALLOWED_UPLOAD_EXTENSIONS:
                skipped_unsupported += 1
                continue

            content = f.file.read(MAX_UPLOAD_BYTES_PER_FILE + 1)
            if len(content) > MAX_UPLOAD_BYTES_PER_FILE:
                skipped_invalid += 1
                errors.append(f"{safe_name}: file too large")
                continue

            output_path = os.path.join(raw_dir, safe_name)
            with open(output_path, "wb") as out:
                out.write(content)
            saved += 1
            saved_files.append(safe_name)
        except Exception as e:
            skipped_invalid += 1
            errors.append(f"{original_name or '?'}: {type(e).__name__}")
        finally:
            f.file.close()

    return {
        "saved": saved,
        "saved_files": saved_files[:50],
        "skipped_unsupported": skipped_unsupported,
        "skipped_invalid": skipped_invalid,
        "saved_in": raw_dir,
        "errors_preview": errors[:10] if errors else None,
    }


@router.get("/documents")
def list_documents(
    tenant_user: tuple[str, str] = Depends(get_tenant_user),
    db: Session = Depends(get_db),
    with_chunk_counts: bool = False,
):
    tenant_id, user_id = tenant_user

    docs: List[Document] = (
        db.query(Document)
        .filter(Document.tenant_id == tenant_id, Document.user_id == user_id)
        .order_by(Document.created_at.desc())
        .all()
    )

    if not docs:
        return {"documents": []}

    chunk_counts = {}
    if with_chunk_counts:
        rows = (
            db.query(Chunk.document_id, func.count(Chunk.id))
            .filter(Chunk.tenant_id == tenant_id, Chunk.user_id == user_id)
            .group_by(Chunk.document_id)
            .all()
        )
        chunk_counts = {doc_id: count for doc_id, count in rows}

    result = []
    for d in docs:
        payload = {
            "id": d.id,
            "drive_file_id": d.drive_file_id,
            "name": d.name,
            "mime_type": d.mime_type,
            "created_at": d.created_at,
            "modified_time": d.modified_time,
            "web_view_link": d.web_view_link,
        }
        if with_chunk_counts:
            payload["chunk_count"] = int(chunk_counts.get(d.id, 0))
        result.append(payload)

    return {"documents": result}


@router.post("/documents/upload")
def upload_documents(
    tenant_user: tuple[str, str] = Depends(get_tenant_user),
    files: list[UploadFile] = File(...),
):
    """
    Upload selected documents directly to the user's raw folder.
    Works regardless of Google Drive connection status.
    """
    _, user_id = tenant_user
    result = _save_uploaded_files(user_id, files)
    return {
        "status": "ok",
        "message": "Files uploaded to raw folder.",
        **result,
    }


@router.post("/documents/upload-and-index")
def upload_and_index_documents(
    background_tasks: BackgroundTasks,
    tenant_user: tuple[str, str] = Depends(get_tenant_user),
    files: list[UploadFile] = File(...),
    max_files: int = Query(
        5000,
        ge=1,
        description="Max raw files to index after upload; clamped to 5000.",
    ),
    background: bool = True,
    db: Session = Depends(get_db),
):
    """
    Upload selected files, then trigger indexing from local raw folder.
    """
    tenant_id, user_id = tenant_user
    upload_result = _save_uploaded_files(user_id, files)
    max_files = min(max_files, 5000)

    if background:
        background_tasks.add_task(_index_background_task, tenant_id, user_id, max_files)
        return {
            "status": "accepted",
            "message": "Upload completed. Indexing started in background. Poll GET /pipeline/status.",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "index_max_files": max_files,
            "upload": upload_result,
        }

    index_result = _run_index(db, tenant_id, user_id, max_files)
    return {
        "status": "ok",
        "message": "Upload and indexing completed.",
        "upload": upload_result,
        "index": index_result,
    }


