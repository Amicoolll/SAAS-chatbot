"""Unit tests for hybrid RAG fusion (no DB)."""

from app.services.rag.hybrid_retrieval import normalize_fts_config, reciprocal_rank_fusion


def test_normalize_fts_config_whitelist() -> None:
    assert normalize_fts_config("english") == "english"
    assert normalize_fts_config("simple") == "simple"
    assert normalize_fts_config("';inject") == "simple"


def test_reciprocal_rank_fusion_shared_top_rank() -> None:
    """Chunk 'b' is rank 1 in both lists → should lead fused ordering."""
    vec = ["b", "a", "c"]
    fts = ["b", "d", "a"]
    fused = reciprocal_rank_fusion([vec, fts], rrf_k=60)
    assert fused[0][0] == "b"


def test_reciprocal_rank_fusion_one_list_empty() -> None:
    vec = ["x", "y"]
    fused = reciprocal_rank_fusion([vec, []], rrf_k=60)
    assert [t[0] for t in fused] == ["x", "y"]


def test_reciprocal_rank_fusion_both_empty() -> None:
    assert reciprocal_rank_fusion([[], []], rrf_k=60) == []
