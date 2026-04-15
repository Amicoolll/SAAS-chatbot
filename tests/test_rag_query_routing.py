"""Tests for analytics → hybrid vs vector routing."""

from app.core.config import Settings
from app.schemas.query_understanding import QueryUnderstandingResult
from app.services.query_understanding import analyze_query
from app.services.rag.query_routing import (
    should_skip_kb_retrieval,
    should_use_hybrid_retrieval,
)


def _minimal_settings() -> Settings:
    return Settings.model_construct(
        DATABASE_URL="postgresql://localhost/test",
        QUERY_UNDERSTANDING_DOMAINS=[
            "medical",
            "logistics",
            "support",
            "hr",
            "finance",
            "legal",
            "general",
            "multi_domain",
        ],
        QUERY_UNDERSTANDING_INTENTS=[
            "faq",
            "search",
            "summarization",
            "troubleshooting",
            "status_lookup",
            "policy_lookup",
            "comparison",
            "analysis",
            "workflow_help",
        ],
    )


def test_hybrid_for_status_lookup() -> None:
    qu = analyze_query(
        "Why is shipment 18472 delayed?",
        settings=_minimal_settings(),
    )
    assert should_use_hybrid_retrieval(qu) is True


def test_hybrid_for_general_summarization_false() -> None:
    qu = analyze_query(
        "Summarize this document",
        settings=_minimal_settings(),
    )
    assert qu.intent == "summarization"
    assert qu.domain == "general"
    assert should_use_hybrid_retrieval(qu) is False


def test_hybrid_for_faq_general_greeting() -> None:
    qu = analyze_query("Hello", settings=_minimal_settings())
    assert qu.domain == "general"
    assert qu.intent == "faq"
    assert should_use_hybrid_retrieval(qu) is False


def test_skip_kb_for_hi() -> None:
    # analyze_query kept for parity with other tests; skip decision now delegated
    # to the greeting handler and takes only the raw question.
    analyze_query("Hi", settings=_minimal_settings())
    assert should_skip_kb_retrieval("Hi") is True


def test_skip_kb_not_for_substantive_question() -> None:
    analyze_query(
        "What is the QE Prize application process?",
        settings=_minimal_settings(),
    )
    assert should_skip_kb_retrieval("What is the QE Prize application process?") is False


def test_hybrid_when_requires_citations() -> None:
    qu = QueryUnderstandingResult(
        domain="hr",
        intent="summarization",
        complexity="medium",
        risk_level="low",
        needs_exact_match=False,
        needs_multi_hop=False,
        needs_live_data=False,
        requires_citations=True,
    )
    assert should_use_hybrid_retrieval(qu) is True
