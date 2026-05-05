"""ask operation — full RAG flow: greeting → retrieve → answer → fallback.

Mirrors the orchestration shape of ``app/api/chat_pg.py`` but is independent:
no DB writes for messages/conversations (Catapult invocations are stateless),
no per-tenant feature-flag lookup (those map to Catapult tool config), and a
narrower response schema. Web fallback runs whenever ``TAVILY_API_KEY`` is
configured — same as the main app's default-on behaviour.
"""

from __future__ import annotations

import logging
import math
import time

from fastapi import HTTPException
from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.greetingHandler import processIncomingMessage
from app.services.openai_client import (
    chat_conversational,
    chat_with_context,
    chat_with_web_context,
    chat_without_context,
    embed_texts,
)
from app.services.rag.hybrid_retrieval import retrieve_hybrid_chunks
from app.services.web_search import search_web

from catapult_adapter.service.headers import CatapultContext, trace_headers
from catapult_adapter.service.models import (
    AskRequest,
    AskResponse,
    AskUsage,
    Source,
)

logger = logging.getLogger("catapult_adapter.ask")


def _trace_kv(ctx: CatapultContext) -> str:
    """Per Catapult submission guide §7.3: every log line in the request path
    includes x-catapult-request-id (and trace_id when present) so the platform
    team can correlate adapter logs back to the originating SDK call.
    """
    return (
        f"request_id={ctx.request_id or '-'} "
        f"trace_id={ctx.trace_id or '-'} "
        f"tenant={ctx.tenant_id} app={ctx.app_id or '-'}"
    )


def _format_history(turns) -> str:
    """Flatten conversation_history into the plain-text shape the
    OpenAI helpers in ``openai_client.py`` already expect.
    """
    if not turns:
        return ""
    lines = []
    for t in turns:
        prefix = "User" if t.role == "user" else "Assistant"
        lines.append(f"{prefix}: {t.content}")
    return "\n".join(lines)


def _embed_one(question: str, ctx: CatapultContext) -> list[float]:
    try:
        vectors = embed_texts([question], trace_headers=trace_headers(ctx))
    except Exception:
        logger.exception("ask_embed_failed %s", _trace_kv(ctx))
        raise HTTPException(
            status_code=503, detail="Embedding service temporarily unavailable"
        )
    if not vectors:
        logger.warning("ask_embed_empty %s", _trace_kv(ctx))
        raise HTTPException(
            status_code=503, detail="Embedding service returned no vectors"
        )
    return vectors[0]


def _retrieve(
    db,
    *,
    tenant_id: str,
    user_id: str,
    q_emb_str: str,
    question: str,
    top_k: int,
    use_hybrid: bool,
):
    """Return list of (content, name, distance) rows. Mirrors the retrieval
    branches used inside ``chat_pg``."""
    if use_hybrid:
        k_vec = max(
            top_k * settings.HYBRID_VECTOR_CANDIDATE_MULTIPLIER,
            settings.HYBRID_VECTOR_CANDIDATE_MIN,
        )
        rows, _ = retrieve_hybrid_chunks(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            q_emb=q_emb_str,
            question=question,
            k_final=top_k,
            fts_language=settings.FTS_LANGUAGE,
            vector_candidate_k=k_vec,
            fts_candidate_k=settings.HYBRID_FTS_CANDIDATE_K,
            rrf_k=settings.HYBRID_RRF_K,
        )
        return list(rows)

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
            "tenant_id": tenant_id,
            "user_id": user_id,
            "q_emb": q_emb_str,
            "k": top_k,
        },
    ).fetchall()
    return [(r[0], r[1], float(r[2])) for r in rows]


def _web_fallback(question: str, agent_type: str, history: str, ctx: CatapultContext):
    """Return (answer, sources, source_type, mode) using web search if
    available, else a plain LLM answer.
    """
    th = trace_headers(ctx)
    web_results = (
        search_web(question, trace_headers=th) if settings.TAVILY_API_KEY else []
    )
    if web_results:
        try:
            answer = chat_with_web_context(
                question,
                web_results,
                agent_type=agent_type,
                history=history,
                trace_headers=th,
            )
        except Exception:
            logger.exception(
                "ask_web_chat_failed; falling through to llm_fallback %s",
                _trace_kv(ctx),
            )
        else:
            sources = [
                Source(name=r.title or r.url or "web result", url=r.url or None)
                for r in web_results[:5]
            ]
            return (
                answer,
                sources,
                "web",
                "web_grounded",
                "This answer is from web search, not from your documents.",
            )

    try:
        answer = chat_without_context(
            question, agent_type=agent_type, history=history, trace_headers=th
        )
    except Exception:
        logger.exception("ask_llm_fallback_failed %s", _trace_kv(ctx))
        raise HTTPException(
            status_code=503, detail="Chat service temporarily unavailable"
        )
    return (
        answer,
        [],
        "llm",
        "llm_fallback",
        "This answer is AI-generated and not grounded in your documents or the web.",
    )


def run(req: AskRequest, ctx: CatapultContext) -> AskResponse:
    started = time.perf_counter()
    logger.info(
        "ask_started collection=%s agent=%s top_k=%d history_turns=%d %s",
        req.collection_id, req.agent_type, req.top_k,
        len(req.conversation_history), _trace_kv(ctx),
    )
    history = _format_history(req.conversation_history)

    # 1) greeting / non-retrieval short-circuit
    g = processIncomingMessage(req.question)
    if g.skipRetrieval:
        try:
            answer = chat_conversational(
                req.question,
                history=history,
                agent_type=req.agent_type,
                trace_headers=trace_headers(ctx),
            )
        except Exception:
            logger.exception("ask_conversational_failed %s", _trace_kv(ctx))
            raise HTTPException(
                status_code=503, detail="Chat service temporarily unavailable"
            )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "ask_ok mode=conversational latency_ms=%d %s",
            elapsed_ms, _trace_kv(ctx),
        )
        return AskResponse(
            answer=answer,
            sources=[],
            source_type="none",
            mode="conversational",
            usage=AskUsage(retrieval_latency_ms=0),
            model_used=settings.OPENAI_CHAT_MODEL,
        )

    routing_question = g.cleanedQuery or req.question

    # 2) embed + retrieve
    q_vec = _embed_one(routing_question, ctx)
    q_emb_str = "[" + ",".join(str(x) for x in q_vec) + "]"
    use_hybrid = bool(settings.ENABLE_HYBRID_RAG)

    db = SessionLocal()
    try:
        try:
            rows = _retrieve(
                db,
                tenant_id=ctx.tenant_id,
                user_id=req.collection_id,
                q_emb_str=q_emb_str,
                question=routing_question,
                top_k=req.top_k,
                use_hybrid=use_hybrid,
            )
        except Exception:
            logger.exception("ask_retrieval_failed %s", _trace_kv(ctx))
            raise HTTPException(
                status_code=503, detail="Retrieval service temporarily unavailable"
            )

        threshold = settings.RAG_DISTANCE_THRESHOLD

        # 3) decide between kb_grounded and fallback
        best_finite = None
        for r in rows:
            d = float(r[2])
            if math.isfinite(d):
                best_finite = d if best_finite is None else min(best_finite, d)

        kb_usable = bool(rows) and (best_finite is None or best_finite <= threshold)

        if kb_usable:
            context_chunks = [r[0] for r in rows]
            seen: dict[str, None] = {}
            for r in rows:
                seen.setdefault(r[1], None)
            source_names = list(seen.keys())[:5]
            try:
                answer = chat_with_context(
                    routing_question,
                    context_chunks,
                    agent_type=req.agent_type,
                    history=history,
                    trace_headers=trace_headers(ctx),
                )
            except Exception:
                logger.exception("ask_chat_with_context_failed %s", _trace_kv(ctx))
                raise HTTPException(
                    status_code=503,
                    detail="Chat service temporarily unavailable",
                )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "ask_ok mode=kb_grounded chunks=%d latency_ms=%d %s",
                len(rows), elapsed_ms, _trace_kv(ctx),
            )
            return AskResponse(
                answer=answer,
                sources=(
                    [Source(name=n) for n in source_names]
                    if req.include_sources
                    else []
                ),
                source_type="documents",
                mode="kb_grounded",
                usage=AskUsage(
                    retrieval_latency_ms=elapsed_ms,
                    chunks_retrieved=len(rows),
                ),
                model_used=settings.OPENAI_CHAT_MODEL,
            )

        # 4) fallback path (web → llm)
        answer, sources, source_type, mode, message = _web_fallback(
            routing_question, req.agent_type, history, ctx
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "ask_ok mode=%s source_type=%s latency_ms=%d %s",
            mode, source_type, elapsed_ms, _trace_kv(ctx),
        )
        return AskResponse(
            answer=answer,
            sources=sources if req.include_sources else [],
            source_type=source_type,
            mode=mode,
            usage=AskUsage(retrieval_latency_ms=elapsed_ms, chunks_retrieved=0),
            model_used=settings.OPENAI_CHAT_MODEL,
            message=message,
        )
    finally:
        db.close()
