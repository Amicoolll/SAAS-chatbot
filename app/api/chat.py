import math
import os
import json
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.core.deps import get_user_id
from app.services.openai_client import chat_with_context, embed_texts
from app.services.storage import list_files_recursive


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)

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

    # Locate paired embedding files
    index_dir = os.path.join(base_dir, "index")

    # Load all chunks + their embeddings + sources
    all_chunks: list[str] = []
    all_embeddings: list[list[float]] = []
    sources: list[str] = []

    for cf in chunk_files:
        with open(cf, "r", encoding="utf-8") as f:
            chunk_payload = json.load(f)

        src = chunk_payload.get("source_file", cf)
        chunks = chunk_payload.get("chunks", [])[:200]

        # Load the paired embedding file (written by /demo/run)
        emb_file = os.path.join(
            index_dir,
            os.path.basename(cf).replace(".chunks.json", ".embeddings.json"),
        )
        embeddings: list[list[float]] = []
        if os.path.exists(emb_file):
            with open(emb_file, "r", encoding="utf-8") as f:
                emb_payload = json.load(f)
            embeddings = emb_payload.get("embeddings", [])

        for i, c in enumerate(chunks):
            all_chunks.append(c)
            sources.append(src)
            all_embeddings.append(embeddings[i] if i < len(embeddings) else [])

    # If doc_hint provided, filter to those sources only
    if req.doc_hint:
        hint = req.doc_hint.lower()
        filtered_chunks, filtered_embeddings, filtered_sources = [], [], []

        for c, e, s in zip(all_chunks, all_embeddings, sources):
            if hint in s.lower():
                filtered_chunks.append(c)
                filtered_embeddings.append(e)
                filtered_sources.append(s)

        if filtered_chunks:
            all_chunks = filtered_chunks
            all_embeddings = filtered_embeddings
            sources = filtered_sources
        else:
            raise HTTPException(
                status_code=400,
                detail=f"No chunks matched doc_hint='{req.doc_hint}'. Check /drive/files for exact name."
            )

    # Embed the question and score by cosine similarity.
    # Fall back to keyword scoring if embeddings are missing (e.g. /demo/run not re-run).
    use_semantic = any(len(e) > 0 for e in all_embeddings)
    if use_semantic:
        q_emb = embed_texts([req.question])[0]
        scored = [
            (_cosine_similarity(q_emb, e) if e else -1.0, i)
            for i, e in enumerate(all_embeddings)
        ]
        scored.sort(reverse=True)
    else:
        q = req.question.lower()
        scored = [
            (sum(1 for w in q.split() if w and w in all_chunks[i].lower()), i)
            for i in range(len(all_chunks))
        ]
        scored.sort(reverse=True)

    top = [all_chunks[i] for _, i in scored[:12]]
    top_sources = [sources[i] for _, i in scored[:12]]

    answer = chat_with_context(req.question, top)

    return {
        "answer": answer,
        "sources": list(dict.fromkeys(top_sources))[:5]
    }