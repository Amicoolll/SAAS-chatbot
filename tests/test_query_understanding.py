"""Tests for rule-based query understanding (no Settings / DB required)."""

from app.core.config import Settings
from app.services.query_understanding import analyze_query


def _minimal_settings() -> Settings:
    """Avoid requiring DATABASE_URL when running only QU tests."""
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


def test_shipment_delay_example() -> None:
    s = _minimal_settings()
    r = analyze_query("Why is shipment 18472 delayed?", settings=s)
    assert r.domain == "logistics"
    assert r.intent == "status_lookup"
    assert r.complexity == "medium"
    assert r.risk_level == "medium"
    assert r.needs_exact_match is True
    assert r.needs_multi_hop is False
    assert r.needs_live_data is True
    assert r.requires_citations is False


def test_medicine_for_fever_is_high_risk_medical_citations() -> None:
    s = _minimal_settings()
    r = analyze_query("What medicine should I take for fever?", settings=s)
    assert r.domain == "medical"
    assert r.risk_level == "high"
    assert r.requires_citations is True
    assert r.intent == "search"


def test_empty_query_is_general_low_risk() -> None:
    s = _minimal_settings()
    r = analyze_query("   ", settings=s)
    assert r.domain == "general"
    assert r.risk_level == "low"
