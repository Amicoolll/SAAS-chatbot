import logging
import os
from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import delete

from app.core.config import settings
from app.core.deps import get_tenant_user
from app.core.logging import log_operation
from app.db.session import get_db, SessionLocal
from app.db.models import Document, Chunk
from app.services.storage import list_files_recursive, read_text
from app.services.ingest.chunker import chunk_text
from app.services.openai_client import embed_texts

router = APIRouter(tags=["Indexing (pgvector)"])
logger = logging.getLogger(__name__)


def _run_index(
    db: Session,
    tenant_id: str,
    user_id: str,
    max_files: int,
) -> dict:
    """Index raw files into Postgres + pgvector. Used by route and background task."""
    base_dir = os.path.join("data", f"user_{user_id}")
    raw_dir = os.path.join(base_dir, "raw")

    if not os.path.exists(raw_dir):
        raise HTTPException(status_code=400, detail="No raw files found. Run /drive/sync first.")

    raw_files = list_files_recursive(raw_dir)
    raw_files = [p for p in raw_files if p.endswith(".txt") or p.endswith(".csv")][:max_files]

    batch_size = settings.EMBED_BATCH_SIZE
    chunk_size = settings.CHUNK_SIZE
    chunk_overlap = settings.CHUNK_OVERLAP

    docs_indexed = 0
    chunks_indexed = 0

    for path in raw_files:
        name = os.path.basename(path)
        mime_type = "text/plain" if path.endswith(".txt") else "text/csv"

        text = read_text(path)
        chunks = chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
        if not chunks:
            continue

        embeddings = []
        for i in range(0, len(chunks), batch_size):
            embeddings.extend(embed_texts(chunks[i : i + batch_size]))

        drive_file_id = f"local::{user_id}::{name}"

        doc = db.query(Document).filter(
            Document.tenant_id == tenant_id,
            Document.user_id == user_id,
            Document.drive_file_id == drive_file_id,
        ).first()

        if not doc:
            doc = Document(
                tenant_id=tenant_id,
                user_id=user_id,
                drive_file_id=drive_file_id,
                name=name,
                mime_type=mime_type,
                modified_time="",
                web_view_link="",
            )
            db.add(doc)
            db.commit()
            db.refresh(doc)
        else:
            db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
            db.commit()

        for idx, (c, e) in enumerate(zip(chunks, embeddings)):
            db.add(Chunk(
                tenant_id=tenant_id,
                user_id=user_id,
                document_id=doc.id,
                chunk_index=idx,
                content=c,
                embedding=e,
            ))

        db.commit()
        docs_indexed += 1
        chunks_indexed += len(chunks)

    log_operation(
        logger, "index_run", tenant_id=tenant_id, user_id=user_id,
        docs_indexed=docs_indexed, chunks_indexed=chunks_indexed,
    )
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "docs_indexed": docs_indexed,
        "chunks_indexed": chunks_indexed,
        "note": "Indexed raw/ files into Postgres + pgvector.",
    }


def _index_background_task(tenant_id: str, user_id: str, max_files: int) -> None:
    db = SessionLocal()
    try:
        _run_index(db, tenant_id, user_id, max_files)
    except Exception:
        logger.exception("index_run_background_failed tenant_id=%s user_id=%s", tenant_id, user_id)
        raise
    finally:
        db.close()


@router.post("/index/run")
def index_run(
    background_tasks: BackgroundTasks,
    tenant_user: tuple[str, str] = Depends(get_tenant_user),
    max_files: int = 10,
    background: bool = True,
    db: Session = Depends(get_db),
):
    """Index raw folder into pgvector. If background=True (default), returns 202 and runs in background."""
    tenant_id, user_id = tenant_user

    base_dir = os.path.join("data", f"user_{user_id}")
    raw_dir = os.path.join(base_dir, "raw")
    if not os.path.exists(raw_dir):
        raise HTTPException(status_code=400, detail="No raw files found. Run /drive/sync first.")

    if background:
        background_tasks.add_task(_index_background_task, tenant_id, user_id, max_files)
        return {
            "status": "accepted",
            "message": "Indexing started in background.",
            "tenant_id": tenant_id,
            "user_id": user_id,
        }
    return _run_index(db, tenant_id, user_id, max_files)
