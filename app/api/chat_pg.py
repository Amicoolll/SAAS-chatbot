import math
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text, desc

from app.core.config import settings
from app.core.deps import get_tenant_user
from app.db.session import get_db
from app.db.models_chat import Conversation, Message
from app.services.openai_client import (
    chat_conversational,
    chat_with_context,
    chat_with_web_context,
    chat_without_context,
    embed_texts,
)
from app.services.feature_flags import is_enabled as feature_is_enabled
from app.services.greetingHandler import processIncomingMessage
from app.services.query_understanding import analyze_query
from app.services.rag.hybrid_retrieval import retrieve_hybrid_chunks
from app.services.rag.query_routing import (
    should_skip_kb_retrieval,
    should_use_hybrid_retrieval,
)
from app.services.web_search import search_web

router = APIRouter(tags=["Chat (pgvector)"])


class ChatRequest(BaseModel):
    conversation_id: str
    question: str
    agent_type: str = "general"


def _related_images(user_id: str, sources: list[str], limit: int = 8) -> list[dict[str, str]]:
    """
    Return local synced images likely related to retrieved sources by base filename prefix.
    """
    raw_dir = os.path.join("data", f"user_{user_id}", "raw")
    if not os.path.isdir(raw_dir):
        return []
    stems = {os.path.splitext(s)[0].lower() for s in sources if s}
    if not stems:
        return []
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    out: list[dict[str, str]] = []
    for p in Path(raw_dir).iterdir():
        if not p.is_file() or p.suffix.lower() not in image_exts:
            continue
        stem = p.stem.lower()
        if stem in stems or any(stem.startswith(x) or x.startswith(stem) for x in stems):
            out.append({"name": p.name, "url": f"/drive/images/{quote(p.name)}"})
            if len(out) >= limit:
                break
    return out


_NO_ANSWER_SIGNALS = (
    "no information",
    "no relevant",
    "not available",
    "not found in",
    "does not contain",
    "no data",
    "no mention",
    "not mentioned",
    "no details",
    "cannot find",
    "couldn't find",
    "could not find",
    "don't have",
    "do not have",
    "insufficient context",
    "no context",
    "none available",
)


def _answer_admits_no_info(answer: str) -> bool:
    """Return True when the LLM's kb_grounded answer effectively says
    'the documents don't have this information'. This triggers a web
    search re-attempt instead of returning an unhelpful response.
    """
    lower = answer.lower()
    return any(signal in lower for signal in _NO_ANSWER_SIGNALS)


def _fallback_answer(
    tenant_id: str,
    question: str,
    agent_type: str,
    history: str,
) -> tuple[str, list[str], str]:
    """Try web search (if enabled for this tenant), then plain LLM fallback.

    Returns ``(answer, sources, mode)`` so the caller can assign all three
    in one shot regardless of which path was taken.
    """
    web_enabled = (
        settings.WEB_SEARCH_GLOBAL_ENABLED
        and settings.TAVILY_API_KEY
        and not feature_is_enabled(tenant_id, "web_search_disabled")
    )
    if web_enabled:
        web_results = search_web(question)
        if web_results:
            answer = chat_with_web_context(
                question,
                web_results,
                agent_type=agent_type,
                history=history,
            )
            sources = [r.url for r in web_results if r.url][:5]
            return answer, sources, "web_grounded"

    answer = chat_without_context(
        question, agent_type=agent_type, history=history
    )
    return answer, [], "llm_fallback"


@router.post("/chat_pg")
def chat_pg(
    req: ChatRequest,
    tenant_user: tuple[str, str] = Depends(get_tenant_user),
    k: int | None = None,
    history_limit: int | None = None,
    db: Session = Depends(get_db),
):
    tenant_id, user_id = tenant_user
    k = k if k is not None else settings.RETRIEVAL_TOP_K
    history_limit = history_limit if history_limit is not None else settings.CHAT_HISTORY_LIMIT
    # 1) validate conversation ownership
    conv = db.query(Conversation).filter(
        Conversation.id == req.conversation_id,
        Conversation.tenant_id == tenant_id,
        Conversation.user_id == user_id
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # 2) store user message
    db.add(Message(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=req.conversation_id,
        role="user",
        content=req.question
    ))
    db.commit()

    # 3) fetch last N messages for history
    msgs = (
        db.query(Message)
        .filter(
            Message.conversation_id == req.conversation_id,
            Message.tenant_id == tenant_id,
            Message.user_id == user_id
        )
        .order_by(desc(Message.created_at))
        .limit(history_limit)
        .all()
    )
    msgs = list(reversed(msgs))

    # Build history string with a character budget (~6 000 chars ≈ 1 500 tokens).
    # Drop oldest turns first so the most recent context is always preserved.
    _HISTORY_CHAR_LIMIT = 6_000
    history_lines = [f"{m.role.upper()}: {m.content}" for m in msgs]
    while history_lines:
        candidate = "\n".join(history_lines)
        if len(candidate) <= _HISTORY_CHAR_LIMIT:
            break
        history_lines.pop(0)  # drop the oldest turn
    history_text = "\n".join(history_lines)

    greeting = processIncomingMessage(req.question)
    routing_question = (
        req.question
        if greeting.skipRetrieval
        else (greeting.cleanedQuery if greeting.hadGreetingPrefix else req.question)
    )
    qu = analyze_query(routing_question, settings=settings)

    if greeting.skipRetrieval:
        try:
            answer = chat_conversational(
                req.question,
                history=history_text,
                agent_type=req.agent_type,
            )
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Chat service temporarily unavailable. Please try again.",
            )
        mode = "conversational"
        sources = []
        retrieve_mode = "none"
        use_hybrid = False
    else:
        # 4) embed question for retrieval
        try:
            q_emb_list = embed_texts([routing_question])[0]
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Embedding service temporarily unavailable. Please try again.",
            )
        q_emb = "[" + ",".join(str(x) for x in q_emb_list) + "]"

        use_hybrid = bool(
            settings.ENABLE_HYBRID_RAG
            and (
                not settings.HYBRID_RAG_ANALYTICS_ROUTING
                or should_use_hybrid_retrieval(qu)
            )
        )

        threshold = settings.RAG_DISTANCE_THRESHOLD
        retrieve_mode = "hybrid" if use_hybrid else "vector"
        try:
            if use_hybrid:
                k_vec = max(
                    k * settings.HYBRID_VECTOR_CANDIDATE_MULTIPLIER,
                    settings.HYBRID_VECTOR_CANDIDATE_MIN,
                )
                rows, _fts_had_hits = retrieve_hybrid_chunks(
                    db,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    q_emb=q_emb,
                    question=routing_question,
                    k_final=k,
                    fts_language=settings.FTS_LANGUAGE,
                    vector_candidate_k=k_vec,
                    fts_candidate_k=settings.HYBRID_FTS_CANDIDATE_K,
                    rrf_k=settings.HYBRID_RRF_K,
                )
            else:
                sql = text("""
                SELECT c.content, d.name, (c.embedding <=> (:q_emb)::vector) AS distance
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.tenant_id = :tenant_id AND c.user_id = :user_id
                ORDER BY distance
                LIMIT :k
                """)

                rows = db.execute(
                    sql,
                    {"tenant_id": tenant_id, "user_id": user_id, "q_emb": q_emb, "k": k},
                ).fetchall()

            if not rows:
                answer, sources, mode = _fallback_answer(
                    tenant_id, routing_question, req.agent_type, history_text
                )
            elif use_hybrid:
                vec_dists = [float(r[2]) for r in rows if math.isfinite(float(r[2]))]
                best_vec = min(vec_dists) if vec_dists else None
                if best_vec is not None and best_vec > threshold:
                    answer, sources, mode = _fallback_answer(
                        tenant_id, routing_question, req.agent_type, history_text
                    )
                else:
                    mode = "kb_grounded"
                    context_chunks = [r[0] for r in rows]
                    sources = list(dict.fromkeys([r[1] for r in rows]))[:5]
                    answer = chat_with_context(
                        routing_question,
                        context_chunks,
                        agent_type=req.agent_type,
                        history=history_text,
                    )
                    if _answer_admits_no_info(answer):
                        answer, sources, mode = _fallback_answer(
                            tenant_id, routing_question, req.agent_type, history_text
                        )
            else:
                best_distance = float(rows[0][2])
                if best_distance > threshold:
                    answer, sources, mode = _fallback_answer(
                        tenant_id, routing_question, req.agent_type, history_text
                    )
                else:
                    mode = "kb_grounded"
                    context_chunks = [r[0] for r in rows]
                    sources = list(dict.fromkeys([r[1] for r in rows]))[:5]
                    answer = chat_with_context(
                        routing_question,
                        context_chunks,
                        agent_type=req.agent_type,
                        history=history_text,
                    )
                    if _answer_admits_no_info(answer):
                        answer, sources, mode = _fallback_answer(
                            tenant_id, routing_question, req.agent_type, history_text
                        )
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Chat service temporarily unavailable. Please try again.",
            )

    # 7) store assistant message + touch conversation
    db.add(Message(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=req.conversation_id,
        role="assistant",
        content=answer
    ))
    conv.title = conv.title if conv.title != "New chat" else req.question[:40]
    db.commit()

    _SOURCE_TYPE_MAP = {
        "kb_grounded": "documents",
        "web_grounded": "web",
        "llm_fallback": "llm",
        "conversational": "none",
    }
    _MESSAGE_MAP = {
        "web_grounded": "This answer is from web search, not from your documents.",
        "llm_fallback": "This answer is AI-generated and not grounded in your documents or the web.",
    }
    source_type = _SOURCE_TYPE_MAP.get(mode, "none")

    out: dict = {
        "mode": mode,
        "source_type": source_type,
        "answer": answer,
        "sources": sources,
        "images": _related_images(user_id, sources),
        "retrieve_mode": retrieve_mode,
    }
    if mode in _MESSAGE_MAP:
        out["message"] = _MESSAGE_MAP[mode]
    if settings.CHAT_RETURN_QUERY_ROUTING:
        out["query_routing"] = {
            "domain": qu.domain,
            "intent": qu.intent,
            "complexity": qu.complexity,
            "risk_level": qu.risk_level,
            "needs_exact_match": qu.needs_exact_match,
            "needs_multi_hop": qu.needs_multi_hop,
            "needs_live_data": qu.needs_live_data,
            "requires_citations": qu.requires_citations,
            "hybrid_eligible": should_use_hybrid_retrieval(qu),
            "use_hybrid": use_hybrid,
            "skip_kb": should_skip_kb_retrieval(req.question),
        }
    return out