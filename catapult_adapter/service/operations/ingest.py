"""ingest operation — accept raw text documents and store as embedded chunks.

Mirrors the per-file commit shape used by ``app/api/index.py`` so a partial
failure leaves the DB consistent: each document's Document + Chunk rows are
written in one transaction, errors are collected per-document.

Field mapping into the existing schema (no DB migration in v1):

    request.collection_id      → chunks.user_id
    ctx.tenant_id              → chunks.tenant_id
    request.documents[*].document_id → documents.drive_file_id
    request.documents[*].name  → documents.name (defaults to document_id)
    request.documents[*].mime_type → documents.mime_type
    <ingest timestamp>         → documents.modified_time
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete

from app.db.models import Chunk, Document
from app.db.session import SessionLocal
from app.services.ingest.chunker import chunk_text
from app.services.openai_client import embed_texts

from catapult_adapter.service.headers import CatapultContext, trace_headers
from catapult_adapter.service.models import (
    IngestError,
    IngestRequest,
    IngestResponse,
)

logger = logging.getLogger("catapult_adapter.ingest")


def _trace_kv(ctx: CatapultContext) -> str:
    """Per Catapult submission guide §7.3: include x-catapult-request-id (and
    trace_id when present) in every log line so the platform team can correlate
    adapter logs to the originating SDK call.
    """
    return (
        f"request_id={ctx.request_id or '-'} "
        f"trace_id={ctx.trace_id or '-'} "
        f"tenant={ctx.tenant_id} app={ctx.app_id or '-'}"
    )


def _ingest_one(
    db,
    *,
    tenant_id: str,
    user_id: str,
    document_id: str,
    name: str,
    mime_type: str,
    content: str,
    chunk_size: int,
    chunk_overlap: int,
    th: dict[str, str] | None,
) -> tuple[int, bool]:
    """Ingest a single document. Returns (chunks_created, replaced_existing).

    Either commits all of (Document + its Chunks) or rolls back. Caller decides
    how to surface the error.

    ``th`` is the optional trace-header dict returned by
    ``catapult_adapter.service.headers.trace_headers``; passed straight
    through to ``embed_texts``.
    """
    chunks = chunk_text(content, chunk_size=chunk_size, overlap=chunk_overlap)
    if not chunks:
        return 0, False

    embeddings = embed_texts(chunks, trace_headers=th)
    if len(embeddings) != len(chunks):
        raise RuntimeError(
            f"embed_texts returned {len(embeddings)} vectors for {len(chunks)} chunks"
        )

    now_iso = datetime.now(timezone.utc).isoformat()

    doc = (
        db.query(Document)
        .filter(
            Document.tenant_id == tenant_id,
            Document.user_id == user_id,
            Document.drive_file_id == document_id,
        )
        .first()
    )

    replaced = False
    if doc is None:
        doc = Document(
            tenant_id=tenant_id,
            user_id=user_id,
            drive_file_id=document_id,
            name=name,
            mime_type=mime_type,
            modified_time=now_iso,
            web_view_link="",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
    else:
        replaced = True
        doc.name = name
        doc.mime_type = mime_type
        doc.modified_time = now_iso
        db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        db.commit()

    for idx, (c, e) in enumerate(zip(chunks, embeddings)):
        db.add(
            Chunk(
                tenant_id=tenant_id,
                user_id=user_id,
                document_id=doc.id,
                chunk_index=idx,
                content=c,
                embedding=e,
            )
        )
    db.commit()

    return len(chunks), replaced


def run(req: IngestRequest, ctx: CatapultContext) -> IngestResponse:
    logger.info(
        "ingest_started collection=%s docs=%d %s",
        req.collection_id, len(req.documents), _trace_kv(ctx),
    )
    db = SessionLocal()
    chunks_total = 0
    docs_processed = 0
    docs_replaced = 0
    errors: list[IngestError] = []
    th = trace_headers(ctx)

    try:
        for d in req.documents:
            display_name = d.name or d.document_id
            try:
                chunk_count, replaced = _ingest_one(
                    db,
                    tenant_id=ctx.tenant_id,
                    user_id=req.collection_id,
                    document_id=d.document_id,
                    name=display_name,
                    mime_type=d.mime_type,
                    content=d.content,
                    chunk_size=req.chunk_size,
                    chunk_overlap=req.chunk_overlap,
                    th=th,
                )
            except Exception as exc:
                db.rollback()
                logger.exception(
                    "ingest_document_failed document_id=%s %s",
                    d.document_id, _trace_kv(ctx),
                )
                errors.append(
                    IngestError(document_id=d.document_id, error=str(exc) or type(exc).__name__)
                )
                continue

            chunks_total += chunk_count
            docs_processed += 1
            if replaced:
                docs_replaced += 1
    finally:
        db.close()

    logger.info(
        "ingest_ok docs_processed=%d docs_replaced=%d chunks=%d errors=%d %s",
        docs_processed, docs_replaced, chunks_total, len(errors), _trace_kv(ctx),
    )
    return IngestResponse(
        collection_id=req.collection_id,
        documents_processed=docs_processed,
        chunks_created=chunks_total,
        documents_replaced=docs_replaced,
        errors=errors,
    )
