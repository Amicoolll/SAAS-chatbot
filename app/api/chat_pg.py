from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text, desc

from app.core.config import settings
from app.core.deps import get_tenant_user
from app.db.session import get_db
from app.db.models_chat import Conversation, Message
from app.services.openai_client import embed_texts, chat_with_context, chat_without_context

router = APIRouter(tags=["Chat (pgvector)"])


class ChatRequest(BaseModel):
    conversation_id: str
    question: str
    agent_type: str = "general"


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

    # turn into a small history string
    history_text = "\n".join([f"{m.role.upper()}: {m.content}" for m in msgs])

    # 4) embed question for retrieval
    try:
        q_emb_list = embed_texts([req.question])[0]
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Embedding service temporarily unavailable. Please try again.",
        )
    q_emb = "[" + ",".join(str(x) for x in q_emb_list) + "]"

    # 5) retrieve topK chunks + distance
    sql = text("""
    SELECT c.content, d.name, (c.embedding <=> (:q_emb)::vector) AS distance
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.tenant_id = :tenant_id AND c.user_id = :user_id
    ORDER BY distance
    LIMIT :k
    """)

    rows = db.execute(sql, {"tenant_id": tenant_id, "user_id": user_id, "q_emb": q_emb, "k": k}).fetchall()

    # 6) confidence gate -> KB grounded vs LLM fallback
    threshold = settings.RAG_DISTANCE_THRESHOLD
    try:
        if not rows:
            mode = "llm_fallback"
            sources = []
            answer = chat_without_context(req.question, agent_type=req.agent_type, history=history_text)
        else:
            best_distance = float(rows[0][2])
            if best_distance > threshold:
                mode = "llm_fallback"
                sources = []
                answer = chat_without_context(req.question, agent_type=req.agent_type, history=history_text)
            else:
                mode = "kb_grounded"
                context_chunks = [r[0] for r in rows]
                sources = list(dict.fromkeys([r[1] for r in rows]))[:5]
                answer = chat_with_context(req.question, context_chunks, agent_type=req.agent_type, history=history_text)
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

    return {"mode": mode, "answer": answer, "sources": sources}