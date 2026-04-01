"""HTTP wrapper for rule-based query analysis (no LLM)."""

from fastapi import APIRouter, Query

from app.services.query_understanding import analyze_query

router = APIRouter(tags=["Query understanding"])


@router.get("/query/understand")
def query_understand(q: str = Query(..., min_length=1, description="User question to analyze.")):
    """Return domain, intent, complexity, risk, and retrieval flags for ``q``."""
    return analyze_query(q).model_dump()
