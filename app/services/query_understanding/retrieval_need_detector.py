"""Detect retrieval-related flags from query text and domain."""

from __future__ import annotations

import re

from app.services.query_understanding.rules import (
    CITATION_DOMAINS,
    CITATION_KEYWORDS,
    EXACT_MATCH_CODE_PATTERN,
    EXACT_MATCH_NUMERIC_PATTERN,
    EXACT_MATCH_ORDER_STYLE_PATTERN,
    LIVE_DATA_KEYWORDS,
    MULTI_HOP_KEYWORDS,
    query_requests_medical_prescribing_or_advice,
)


def detect_retrieval_needs(query: str, domain: str) -> dict[str, bool]:
    """
    Return flags:

    - needs_exact_match: IDs / long numbers / coded tokens
    - needs_multi_hop: comparative / relational phrasing
    - needs_live_data: time-sensitive / status language
    - requires_citations: policy/legal/medical-style or explicit cite ask
    """
    if not query:
        return {
            "needs_exact_match": False,
            "needs_multi_hop": False,
            "needs_live_data": False,
            "requires_citations": False,
        }

    lowered = query.lower()

    needs_exact_match = bool(
        EXACT_MATCH_NUMERIC_PATTERN.search(query)
        or EXACT_MATCH_CODE_PATTERN.search(query)
        or EXACT_MATCH_ORDER_STYLE_PATTERN.search(query)
    )

    needs_multi_hop = any(kw in lowered for kw in MULTI_HOP_KEYWORDS)
    if not needs_multi_hop and domain == "multi_domain":
        needs_multi_hop = True

    needs_live_data = any(kw in lowered for kw in LIVE_DATA_KEYWORDS)

    requires_citations = (
        any(kw in lowered for kw in CITATION_KEYWORDS)
        or domain in CITATION_DOMAINS
        or query_requests_medical_prescribing_or_advice(query)
        or (
            domain == "hr"
            and ("policy" in lowered or "policies" in lowered)
        )
        or (
            domain == "multi_domain"
            and (
                "finance" in lowered
                or bool(re.search(r"\bhr\b", lowered))
                or "human resources" in lowered
                or "medical" in lowered
            )
        )
    )

    return {
        "needs_exact_match": needs_exact_match,
        "needs_multi_hop": needs_multi_hop,
        "needs_live_data": needs_live_data,
        "requires_citations": requires_citations,
    }
