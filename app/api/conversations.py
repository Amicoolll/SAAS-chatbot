from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.db.session import get_db
from app.db.models_chat import Conversation, Message

router = APIRouter(tags=["Conversations"])

class CreateConversationReq(BaseModel):
    title: str | None = None

@router.post("/conversations")
def create_conversation(
    req: CreateConversationReq,
    tenant_id: str = "demo_tenant",
    user_id: str = "demo_user",
    db: Session = Depends(get_db),
):
    conv = Conversation(
        tenant_id=tenant_id,
        user_id=user_id,
        title=req.title or "New chat",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {"conversation_id": conv.id, "title": conv.title}

@router.get("/conversations")
def list_conversations(
    tenant_id: str = "demo_tenant",
    user_id: str = "demo_user",
    db: Session = Depends(get_db),
    limit: int = 30,
):
    rows = (
        db.query(Conversation)
        .filter(Conversation.tenant_id == tenant_id, Conversation.user_id == user_id)
        .order_by(desc(Conversation.updated_at))
        .limit(limit)
        .all()
    )
    return {"conversations": [{"id": r.id, "title": r.title, "updated_at": r.updated_at} for r in rows]}

@router.get("/conversations/{conversation_id}/messages")
def get_messages(
    conversation_id: str,
    tenant_id: str = "demo_tenant",
    user_id: str = "demo_user",
    db: Session = Depends(get_db),
    limit: int = 20,
):
    # ensure conversation belongs to user+tenant
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.tenant_id == tenant_id,
        Conversation.user_id == user_id
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.tenant_id == tenant_id,
            Message.user_id == user_id
        )
        .order_by(desc(Message.created_at))
        .limit(limit)
        .all()
    )
    msgs = list(reversed(msgs))
    return {"messages": [{"role": m.role, "content": m.content, "created_at": m.created_at} for m in msgs]}