"""
Hybrid retrieval: dense (pgvector) + sparse (Postgres FTS), fused with RRF.

When FTS is unavailable or returns nothing, callers should fall back to vector-only lists.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Allowed PostgreSQL text search configurations (regconfig).
_FTS_CONFIG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def normalize_fts_config(raw: str) -> str:
    s = (raw or "simple").strip().lower()
    if _FTS_CONFIG_RE.match(s):
        return s
    return "simple"


def reciprocal_rank_fusion(
    ranked_id_lists: list[list[str]],
    *,
    rrf_k: int = 60,
) -> list[tuple[str, float]]:
    """
    Reciprocal Rank Fusion over chunk id lists (order = rank, best first).

    Returns chunk ids sorted by fused score descending.
    """
    scores: dict[str, float] = {}
    for ranked_ids in ranked_id_lists:
        if not ranked_ids:
            continue
        for rank, cid in enumerate(ranked_ids, start=1):
            if not cid:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (rrf_k + rank)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))


def _chunk_meta_maps(
    vector_rows: list[Any],
    fts_rows: list[Any],
) -> tuple[dict[str, tuple[str, str, float]], dict[str, tuple[str, str]]]:
    """
    vector_rows: (id, content, name, distance)
    fts_rows: (id, content, name)
    """
    vec: dict[str, tuple[str, str, float]] = {}
    for r in vector_rows:
        cid, content, name, dist = r[0], r[1], r[2], float(r[3])
        vec[cid] = (content, name, dist)
    fts: dict[str, tuple[str, str]] = {}
    for r in fts_rows:
        cid, content, name = r[0], r[1], r[2]
        fts[cid] = (content, name)
    return vec, fts


def retrieve_hybrid_chunks(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    q_emb: str,
    question: str,
    k_final: int,
    fts_language: str,
    vector_candidate_k: int,
    fts_candidate_k: int,
    rrf_k: int,
) -> tuple[list[tuple[str, str, float]], bool]:
    """
    Return rows as (content, name, distance) for top-k_final chunks after RRF.

    ``distance`` is the pgvector distance when the chunk appeared in the vector
    list; otherwise ``float('inf')`` (FTS-only; caller may still use chunk text).

    Second value is True if FTS leg ran and contributed at least one candidate row.
    """
    fts_lang = normalize_fts_config(fts_language)

    sql_vec = text("""
    SELECT c.id, c.content, d.name, (c.embedding <=> (:q_emb)::vector) AS distance
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.tenant_id = :tenant_id AND c.user_id = :user_id
    ORDER BY distance
    LIMIT :k_vec
    """)

    vec_rows = db.execute(
        sql_vec,
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "q_emb": q_emb,
            "k_vec": vector_candidate_k,
        },
    ).fetchall()

    vec_ids = [str(r[0]) for r in vec_rows]
    fts_ran_non_empty = False
    fts_rows: list[Any] = []

    qstrip = (question or "").strip()
    if qstrip:
        sql_fts = text("""
        SELECT c.id, c.content, d.name
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.tenant_id = :tenant_id AND c.user_id = :user_id
          AND c.content_tsv @@ plainto_tsquery(:fts_lang, :q_plain)
        ORDER BY ts_rank_cd(c.content_tsv, plainto_tsquery(:fts_lang, :q_plain)) DESC
        LIMIT :k_fts
        """)
        try:
            fts_rows = db.execute(
                sql_fts,
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "fts_lang": fts_lang,
                    "q_plain": qstrip,
                    "k_fts": fts_candidate_k,
                },
            ).fetchall()
            fts_ran_non_empty = len(fts_rows) > 0
        except Exception:
            logger.warning("hybrid_fts_query_failed; using vector-only", exc_info=True)
            fts_rows = []

    fts_ids = [str(r[0]) for r in fts_rows]

    if not vec_ids and not fts_ids:
        return [], fts_ran_non_empty

    if not fts_ids:
        # Vector only (same ordering as pure vector retrieve).
        out: list[tuple[str, str, float]] = []
        for r in vec_rows[:k_final]:
            _, content, name, dist = r[0], r[1], r[2], float(r[3])
            out.append((content, name, dist))
        return out, False

    if not vec_ids:
        # FTS only
        out = []
        for r in fts_rows[:k_final]:
            content, name = r[1], r[2]
            out.append((content, name, float("inf")))
        return out, fts_ran_non_empty

    fused = reciprocal_rank_fusion([vec_ids, fts_ids], rrf_k=rrf_k)
    vec_map, fts_map = _chunk_meta_maps(vec_rows, fts_rows)

    rows_out: list[tuple[str, str, float]] = []
    for cid, _score in fused:
        if len(rows_out) >= k_final:
            break
        if cid in vec_map:
            content, name, dist = vec_map[cid]
            rows_out.append((content, name, dist))
        elif cid in fts_map:
            content, name = fts_map[cid]
            rows_out.append((content, name, float("inf")))

    return rows_out, fts_ran_non_empty
