"""
Compose domain, intent, complexity, risk, and retrieval detectors into one result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.schemas.query_understanding import QueryUnderstandingResult
from app.services.query_understanding.complexity_detector import detect_complexity
from app.services.query_understanding.domain_classifier import classify_domain
from app.services.query_understanding.intent_classifier import classify_intent
from app.services.query_understanding.retrieval_need_detector import detect_retrieval_needs
from app.services.query_understanding.risk_analyzer import analyze_risk

if TYPE_CHECKING:
    from app.core.config import Settings


def analyze_query(query: str, settings: "Settings | None" = None) -> QueryUnderstandingResult:
    """
    Run all rule-based classifiers and return a structured ``QueryUnderstandingResult``.

    Parameters
    ----------
    query:
        Raw user question.
    settings:
        Optional ``Settings`` instance (defaults to ``app.core.config.settings``).
        Injected for tests to avoid mandatory ``DATABASE_URL`` when unused.
    """
    if settings is None:
        from app.core.config import settings as app_settings

        settings = app_settings

    domain = classify_domain(
        query,
        allowed_domains=settings.QUERY_UNDERSTANDING_DOMAINS,
    )
    intent = classify_intent(
        query,
        allowed_intents=settings.QUERY_UNDERSTANDING_INTENTS,
    )
    complexity = detect_complexity(query, domain)
    risk_level = analyze_risk(domain, query)
    retrieval = detect_retrieval_needs(query, domain)

    return QueryUnderstandingResult(
        domain=domain,
        intent=intent,
        complexity=complexity,
        risk_level=risk_level,
        needs_exact_match=retrieval["needs_exact_match"],
        needs_multi_hop=retrieval["needs_multi_hop"],
        needs_live_data=retrieval["needs_live_data"],
        requires_citations=retrieval["requires_citations"],
    )
