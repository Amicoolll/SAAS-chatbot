"""RAG retrieval helpers (hybrid vector + FTS, fusion, etc.)."""

from app.services.rag.hybrid_retrieval import reciprocal_rank_fusion, retrieve_hybrid_chunks
from app.services.rag.query_routing import (
    should_skip_kb_retrieval,
    should_use_hybrid_retrieval,
)

__all__ = [
    "reciprocal_rank_fusion",
    "retrieve_hybrid_chunks",
    "should_skip_kb_retrieval",
    "should_use_hybrid_retrieval",
]
