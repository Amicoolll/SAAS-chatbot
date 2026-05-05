"""search operation — semantic retrieval over the ingested KB, no LLM call.

Reuses ``app.services.openai_client.embed_texts`` and
``app.services.rag.hybrid_retrieval.retrieve_hybrid_chunks``. No code under
``app/`` is modified.
"""

from __future__ import annotations

import logging
import math
import time

from fastapi import HTTPException
from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.openai_client import embed_texts
from app.services.rag.hybrid_retrieval import retrieve_hybrid_chunks

from catapult_adapter.service.headers import CatapultContext, trace_headers
from catapult_adapter.service.models import (
    SearchRequest,
    SearchResponse,
    SearchResult,
)

logger = logging.getLogger("catapult_adapter.search")


def _trace_kv(ctx: CatapultContext) -> str:
    """Return a 'request_id=... trace_id=... tenant=... app=...' suffix for log lines.

    Per Catapult submission guide §7.3: read AND log x-catapult-request-id for
    traceability. Including the IDs in every log line lets the platform team
    correlate failures back to the originating SDK call.
    """
    return (
        f"request_id={ctx.request_id or '-'} "
        f"trace_id={ctx.trace_id or '-'} "
        f"tenant={ctx.tenant_id} app={ctx.app_id or '-'}"
    )


def _embed_one(query: str, ctx: CatapultContext) -> list[float]:
    try:
        vectors = embed_texts([query], trace_headers=trace_headers(ctx))
    except Exception:
        logger.exception("search_embed_failed %s", _trace_kv(ctx))
        raise HTTPException(
            status_code=503, detail="Embedding service temporarily unavailable"
        )
    if not vectors:
        logger.warning("search_embed_empty %s", _trace_kv(ctx))
        raise HTTPException(
            status_code=503, detail="Embedding service returned no vectors"
        )
    return vectors[0]


def run(req: SearchRequest, ctx: CatapultContext) -> SearchResponse:
    started = time.perf_counter()
    logger.info(
        "search_started collection=%s top_k=%d %s",
        req.collection_id, req.top_k, _trace_kv(ctx),
    )

    q_vec = _embed_one(req.query, ctx)
    q_emb = "[" + ",".join(str(x) for x in q_vec) + "]"

    use_hybrid = (
        req.use_hybrid
        if req.use_hybrid is not None
        else bool(settings.ENABLE_HYBRID_RAG)
    )
    retrieve_mode = "hybrid" if use_hybrid else "vector"

    db = SessionLocal()
    try:
        if use_hybrid:
            k_vec = max(
                req.top_k * settings.HYBRID_VECTOR_CANDIDATE_MULTIPLIER,
                settings.HYBRID_VECTOR_CANDIDATE_MIN,
            )
            rows, _ = retrieve_hybrid_chunks(
                db,
                tenant_id=ctx.tenant_id,
                user_id=req.collection_id,
                q_emb=q_emb,
                question=req.query,
                k_final=req.top_k,
                fts_language=settings.FTS_LANGUAGE,
                vector_candidate_k=k_vec,
                fts_candidate_k=settings.HYBRID_FTS_CANDIDATE_K,
                rrf_k=settings.HYBRID_RRF_K,
            )
        else:
            sql = text(
                """
                SELECT c.content, d.name, (c.embedding <=> (:q_emb)::vector) AS distance
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.tenant_id = :tenant_id AND c.user_id = :user_id
                ORDER BY distance
                LIMIT :k
                """
            )
            rows = db.execute(
                sql,
                {
                    "tenant_id": ctx.tenant_id,
                    "user_id": req.collection_id,
                    "q_emb": q_emb,
                    "k": req.top_k,
                },
            ).fetchall()
            rows = [(r[0], r[1], float(r[2])) for r in rows]
    except HTTPException:
        raise
    except Exception:
        logger.exception("search_retrieval_failed %s", _trace_kv(ctx))
        raise HTTPException(
            status_code=503, detail="Retrieval service temporarily unavailable"
        )
    finally:
        db.close()

    results: list[SearchResult] = []
    for content, name, distance in rows:
        d = float(distance)
        results.append(
            SearchResult(
                chunk_text=content,
                document_name=name,
                distance=d if math.isfinite(d) else None,
            )
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "search_ok results=%d mode=%s latency_ms=%d %s",
        len(results), retrieve_mode, elapsed_ms, _trace_kv(ctx),
    )
    return SearchResponse(
        results=results,
        retrieve_mode=retrieve_mode,
        total_matches=len(results),
        latency_ms=elapsed_ms,
    )
