"""Domain-driven risk tier: low | medium | high."""

from __future__ import annotations

import re

from app.services.query_understanding.rules import query_requests_medical_prescribing_or_advice

_HIGH_DOMAINS = frozenset({"medical", "finance", "legal"})
_MEDIUM_DOMAINS = frozenset({"logistics", "multi_domain"})
_LOW_DOMAINS = frozenset({"support", "hr", "general"})


def analyze_risk(domain: str, query: str | None = None) -> str:
    """
    Map classified domain to a coarse risk level for guardrails / routing.

    Rules:
    - medical, finance, legal → high
    - logistics, multi_domain → medium
    - support, hr, general → low
    - Prescribing / which-medicine questions → **high** even if domain slipped to general
    """
    if query and query_requests_medical_prescribing_or_advice(query):
        return "high"
    d = domain.strip().lower()
    if query:
        ql = query.lower()
        if d == "multi_domain":
            has_finance = "finance" in ql
            has_hr = bool(re.search(r"\bhr\b", ql)) or "human resources" in ql
            has_medical = "medical" in ql
            if has_finance and has_hr:
                return "high"
            if has_medical and "finance" in ql:
                return "high"
        if d == "support" and "payment" in ql and "failure" in ql:
            return "medium"
    if d in _HIGH_DOMAINS:
        return "high"
    if d in _MEDIUM_DOMAINS:
        return "medium"
    if d in _LOW_DOMAINS:
        return "low"
    return "medium"
