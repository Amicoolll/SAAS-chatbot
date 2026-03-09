import os
from fastapi import APIRouter, HTTPException
from app.services.storage import ensure_dirs, write_json, read_text, list_files_recursive
from app.services.ingest.chunker import chunk_text
from app.services.openai_client import embed_texts

router = APIRouter()

@router.post("/demo/run")
def demo_run(user_id: str = "demo_user"):
    base_dir = os.path.join("data", f"user_{user_id}")
    raw_dir = os.path.join(base_dir, "raw")

    if not os.path.exists(raw_dir):
        raise HTTPException(status_code=400, detail="No raw files found. Run /drive/sync first.")

    ensure_dirs(base_dir)

    raw_files = list_files_recursive(raw_dir)

    total_docs = 0
    total_chunks = 0
    total_embeddings = 0

    # We'll only chunk text/csv/txt in MVP
    text_files = [p for p in raw_files if p.endswith(".txt") or p.endswith(".csv")]

    for path in text_files:
        total_docs += 1
        text = read_text(path)
        chunks = chunk_text(text)

        chunk_payload = {
            "source_file": path,
            "chunks": chunks,
        }

        chunk_out = os.path.join(base_dir, "chunks", os.path.basename(path) + ".chunks.json")
        write_json(chunk_out, chunk_payload)

        total_chunks += len(chunks)

        # Embed in batches
        BATCH = 64
        embeddings = []
        for i in range(0, len(chunks), BATCH):
            batch = chunks[i:i+BATCH]
            embs = embed_texts(batch)
            embeddings.extend(embs)

        total_embeddings += len(embeddings)

        idx_out = os.path.join(base_dir, "index", os.path.basename(path) + ".embeddings.json")
        write_json(idx_out, {
            "source_file": path,
            "embedding_model": "text-embedding-3-small",
            "count": len(embeddings),
            "embeddings": embeddings,  # MVP only (later store in pgvector)
        })

    return {
        "user_id": user_id,
        "docs_processed": total_docs,
        "chunks_created": total_chunks,
        "embeddings_created": total_embeddings,
        "output_folder": base_dir
    }