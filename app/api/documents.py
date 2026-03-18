import logging
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_tenant_user
from app.db.models import Document, Chunk
from app.db.session import get_db

router = APIRouter(tags=["Documents"])
logger = logging.getLogger(__name__)


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


