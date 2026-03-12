import os
import json
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.core.deps import get_user_id
from app.services.openai_client import chat_with_context
from app.services.storage import list_files_recursive

router = APIRouter()


class ChatRequest(BaseModel):
    question: str
    doc_hint: str | None = None


@router.post("/chat")
def chat(req: ChatRequest, user_id: str = Depends(get_user_id)):
    base_dir = os.path.join("data", f"user_{user_id}")
    chunks_dir = os.path.join(base_dir, "chunks")

    if not os.path.exists(chunks_dir):
        raise HTTPException(status_code=400, detail="No chunks found. Run /demo/run first.")

    chunk_files = [p for p in list_files_recursive(chunks_dir) if p.endswith(".json")]
    if not chunk_files:
        raise HTTPException(status_code=400, detail="No chunk json files found. Run /demo/run first.")

    # Load all chunks + sources
    all_chunks: list[str] = []
    sources: list[str] = []

    for cf in chunk_files:
        with open(cf, "r", encoding="utf-8") as f:
            payload = json.load(f)

        src = payload.get("source_file", cf)
        for c in payload.get("chunks", [])[:200]:  # allow more for better recall
            all_chunks.append(c)
            sources.append(src)

    # If doc_hint provided, filter to those sources only
    if req.doc_hint:
        hint = req.doc_hint.lower()
        filtered_chunks = []
        filtered_sources = []

        for c, s in zip(all_chunks, sources):
            if hint in s.lower():
                filtered_chunks.append(c)
                filtered_sources.append(s)

        if filtered_chunks:
            all_chunks = filtered_chunks
            sources = filtered_sources
        else:
            # If doc_hint doesn't match any file path, fail clearly
            raise HTTPException(
                status_code=400,
                detail=f"No chunks matched doc_hint='{req.doc_hint}'. Check /drive/files for exact name."
            )

    # Keyword scoring (still MVP, but now within doc)
    q = req.question.lower()
    scored = []
    for i, c in enumerate(all_chunks):
        text = c.lower()
        score = sum(1 for w in q.split() if w and w in text)
        scored.append((score, i))

    scored.sort(reverse=True)
    top = [all_chunks[i] for _, i in scored[:12]]  # take more context

    answer = chat_with_context(req.question, top)

    return {
        "answer": answer,
        "sources": list(dict.fromkeys(sources))[:5]  # keep order, max 5
    }