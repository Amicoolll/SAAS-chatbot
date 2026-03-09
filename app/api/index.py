import os
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import delete

from app.db.session import get_db
from app.db.models import Document, Chunk
from app.services.storage import list_files_recursive, read_text
from app.services.ingest.chunker import chunk_text
from app.services.openai_client import embed_texts

router = APIRouter(tags=["Indexing (pgvector)"])


@router.post("/index/run")
def index_run(
    user_id: str = "demo_user",
    tenant_id: str = "demo_tenant",
    max_files: int = 10,
    db: Session = Depends(get_db),
):
    base_dir = os.path.join("data", f"user_{user_id}")
    raw_dir = os.path.join(base_dir, "raw")

    if not os.path.exists(raw_dir):
        raise HTTPException(status_code=400, detail="No raw files found. Run /drive/sync first.")

    raw_files = list_files_recursive(raw_dir)
    raw_files = [p for p in raw_files if p.endswith(".txt") or p.endswith(".csv")][:max_files]

    docs_indexed = 0
    chunks_indexed = 0

    for path in raw_files:
        name = os.path.basename(path)
        mime_type = "text/plain" if path.endswith(".txt") else "text/csv"

        text = read_text(path)
        chunks = chunk_text(text, chunk_size=1200, overlap=200)
        if not chunks:
            continue

        # embeddings
        embeddings = []
        BATCH = 64
        for i in range(0, len(chunks), BATCH):
            embeddings.extend(embed_texts(chunks[i:i + BATCH]))

        # MVP stable id
        drive_file_id = f"local::{user_id}::{name}"

        doc = db.query(Document).filter(
            Document.tenant_id == tenant_id,
            Document.user_id == user_id,
            Document.drive_file_id == drive_file_id
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

    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "docs_indexed": docs_indexed,
        "chunks_indexed": chunks_indexed,
        "note": "Indexed raw/ files into Postgres + pgvector."
    }