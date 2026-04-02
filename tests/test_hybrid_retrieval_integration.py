"""
Integration tests: hybrid retrieval against a real Postgres + pgvector database.

Requires DATABASE_URL or INTEGRATION_TEST_DATABASE_URL (e.g. from .env or CI secret).
Skips automatically when unset so unit-test-only runs stay green.

Example:
  docker compose up -d db
  export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/enterprise_bot
  PYTHONPATH=. pytest tests/test_hybrid_retrieval_integration.py -v -m integration
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.orm import sessionmaker

# conftest loads .env first; skip before any app import that requires DATABASE_URL
if not (
    os.environ.get("INTEGRATION_TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
):
    pytest.skip(
        "Set DATABASE_URL or INTEGRATION_TEST_DATABASE_URL for integration tests",
        allow_module_level=True,
    )

from app.core.config import settings
from app.db.models import Chunk, Document
from app.services.rag.hybrid_retrieval import normalize_fts_config, retrieve_hybrid_chunks

TENANT = "__integration_hybrid_tenant__"
USER = "__integration_hybrid_user__"
DOC_NAME = "integration_fixture.txt"
MARKER_TOKEN = "intghybr_unique_token_77331"


def _db_url() -> str:
    return (
        os.environ.get("INTEGRATION_TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or ""
    )


def _ensure_vector_extension(conn) -> None:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def _ensure_chunk_fts(conn) -> None:
    reg = normalize_fts_config(settings.FTS_LANGUAGE)
    conn.execute(
        text(
            "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS content_tsv tsvector "
            f"GENERATED ALWAYS AS (to_tsvector('{reg}', content)) STORED"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_chunks_content_tsv ON chunks "
            "USING GIN (content_tsv)"
        )
    )


@pytest.fixture(scope="module")
def integration_engine():
    url = _db_url()
    engine = create_engine(url, pool_pre_ping=True)
    with engine.begin() as conn:
        _ensure_vector_extension(conn)
    # Minimal tables for this module (avoid creating full app schema in empty DB)
    Document.__table__.create(bind=engine, checkfirst=True)
    Chunk.__table__.create(bind=engine, checkfirst=True)
    with engine.begin() as conn:
        _ensure_chunk_fts(conn)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(integration_engine):
    Session = sessionmaker(bind=integration_engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.execute(delete(Chunk).where(Chunk.tenant_id == TENANT))
        session.execute(delete(Document).where(Document.tenant_id == TENANT))
        session.commit()
        session.close()


def _dense_first_axis(dim: int) -> list[float]:
    return [1.0] + [0.0] * (dim - 1)


def _dense_last_axis(dim: int) -> list[float]:
    return [0.0] * (dim - 1) + [1.0]


def _to_pgvector_literal(vec: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vec) + "]"


@pytest.mark.integration
def test_hybrid_retrieval_fuses_lexical_signal(db_session):
    """
    Vector-only top-1 is the embedding-close chunk; FTS should surface a second
    chunk with a unique token so fusion can include both.
    """
    dim = settings.EMBED_DIM
    emb_close = _dense_first_axis(dim)
    emb_far = _dense_last_axis(dim)
    q_emb = _to_pgvector_literal(emb_close)

    doc_id = str(uuid.uuid4())
    db_session.add(
        Document(
            id=doc_id,
            tenant_id=TENANT,
            user_id=USER,
            drive_file_id=f"test::{DOC_NAME}",
            name=DOC_NAME,
            mime_type="text/plain",
            modified_time="",
            web_view_link="",
        )
    )
    db_session.flush()

    c_vector = str(uuid.uuid4())
    c_lexical = str(uuid.uuid4())
    db_session.add_all(
        [
            Chunk(
                id=c_vector,
                tenant_id=TENANT,
                user_id=USER,
                document_id=doc_id,
                chunk_index=0,
                content="General onboarding overview for new employees.",
                embedding=emb_close,
            ),
            Chunk(
                id=c_lexical,
                tenant_id=TENANT,
                user_id=USER,
                document_id=doc_id,
                chunk_index=1,
                content=f"Shipment delay notice. {MARKER_TOKEN} customs hold procedure.",
                embedding=emb_far,
            ),
        ]
    )
    db_session.commit()

    rows, fts_hit = retrieve_hybrid_chunks(
        db_session,
        tenant_id=TENANT,
        user_id=USER,
        q_emb=q_emb,
        question=MARKER_TOKEN,
        k_final=4,
        fts_language=settings.FTS_LANGUAGE,
        vector_candidate_k=10,
        fts_candidate_k=10,
        rrf_k=60,
    )

    assert fts_hit is True
    texts = [r[0] for r in rows]
    assert any(MARKER_TOKEN in t for t in texts), (
        "FTS chunk should appear in fused results; got: %r" % texts
    )


@pytest.mark.integration
def test_hybrid_empty_question_vector_only(db_session):
    """Blank question skips FTS leg; ordering matches vector search only."""
    dim = settings.EMBED_DIM
    emb_a = _dense_first_axis(dim)
    emb_b = _dense_last_axis(dim)
    q_emb = _to_pgvector_literal(emb_a)

    doc_id = str(uuid.uuid4())
    db_session.add(
        Document(
            id=doc_id,
            tenant_id=TENANT,
            user_id=USER,
            drive_file_id="test::b.txt",
            name="b.txt",
            mime_type="text/plain",
            modified_time="",
            web_view_link="",
        )
    )
    db_session.flush()
    db_session.add_all(
        [
            Chunk(
                id=str(uuid.uuid4()),
                tenant_id=TENANT,
                user_id=USER,
                document_id=doc_id,
                chunk_index=0,
                content="alpha chunk",
                embedding=emb_a,
            ),
            Chunk(
                id=str(uuid.uuid4()),
                tenant_id=TENANT,
                user_id=USER,
                document_id=doc_id,
                chunk_index=1,
                content="beta chunk",
                embedding=emb_b,
            ),
        ]
    )
    db_session.commit()

    rows, fts_hit = retrieve_hybrid_chunks(
        db_session,
        tenant_id=TENANT,
        user_id=USER,
        q_emb=q_emb,
        question="   ",
        k_final=1,
        fts_language=settings.FTS_LANGUAGE,
        vector_candidate_k=5,
        fts_candidate_k=5,
        rrf_k=60,
    )

    assert fts_hit is False
    assert len(rows) == 1
    assert rows[0][0] == "alpha chunk"
