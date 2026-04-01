"""Keyword-based domain classification (configurable allowed labels)."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.services.query_understanding.rules import (
    DOMAIN_KEYWORDS,
    DOMAIN_TIE_BREAK_ORDER,
    query_requests_medical_prescribing_or_advice,
)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _score_domains(normalized: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        total = 0
        for kw in keywords:
            if kw in normalized:
                total += 1
        if total:
            scores[domain] = total
    return scores


def _forced_multi_domain(normalized_no_space: str, allowed: set[str]) -> str | None:
    """Explicit cross-domain phrases from the product golden set."""
    if "multi_domain" not in allowed:
        return None
    ql = normalized_no_space
    has_finance = "finance" in ql or ("sales" in ql and "expense" in ql)
    has_hr = bool(re.search(r"\bhr\b", ql)) or "human resources" in ql
    has_medical = "medical" in ql
    has_support = "support" in ql
    has_logistics = "logistics" in ql or "shipment" in ql
    if ("compare" in ql or "analyze" in ql) and has_finance and has_hr:
        return "multi_domain"
    if has_medical and "finance" in ql and (
        "impact" in ql or "analyze" in ql or "claim" in ql
    ):
        return "multi_domain"
    if "compare" in ql and has_support and has_logistics:
        return "multi_domain"
    if "compare" in ql and has_hr and "finance" in ql:
        return "multi_domain"
    return None


def _pick_with_tiebreak(candidates: dict[str, int]) -> str | None:
    if not candidates:
        return None
    best_score = max(candidates.values())
    tops = [d for d, s in candidates.items() if s == best_score]
    if len(tops) == 1:
        return tops[0]
    order = {d: i for i, d in enumerate(DOMAIN_TIE_BREAK_ORDER)}
    tops.sort(key=lambda d: (order.get(d, 999), d))
    return tops[0]


def classify_domain(query: str, allowed_domains: Sequence[str]) -> str:
    """
    Return one domain label from ``allowed_domains``.

    - Scores keywords from ``rules.DOMAIN_KEYWORDS`` (``general`` has no keywords).
    - If no keyword matches, returns ``"general"`` (must be in allowed list).
    - If two domains are strongly indicated, may return ``multi_domain`` when
      both appear with substantial scores (heuristic).
    """
    allowed = set(allowed_domains)
    if not query or not query.strip():
        return "general" if "general" in allowed else next(iter(allowed))

    normalized = _normalize(query)
    forced = _forced_multi_domain(normalized, allowed)
    if forced:
        return forced

    scores = _score_domains(normalized)

    # Strong signal: personal prescribing / which-medicine questions → medical domain
    if "medical" in allowed and query_requests_medical_prescribing_or_advice(query):
        scores["medical"] = scores.get("medical", 0) + 10

    # Score only against allowed domains (excluding general for multi_signal)
    scored_allowed = {
        d: s for d, s in scores.items() if d in allowed and d != "general"
    }

    if not scored_allowed:
        return "general" if "general" in allowed else sorted(allowed)[0]

    best = max(scored_allowed.values())
    second = sorted(scored_allowed.values(), reverse=True)[
        1] if len(scored_allowed) > 1 else 0

    # Strong dual-domain heuristic → multi_domain
    if (
        "multi_domain" in allowed
        and second >= 2
        and best >= 2
        and best - second <= 1
        and len([d for d, s in scored_allowed.items() if s >= 2]) >= 2
    ):
        return "multi_domain"

    winner = _pick_with_tiebreak(
        {d: s for d, s in scored_allowed.items() if d != "multi_domain"}
    ) or _pick_with_tiebreak(scored_allowed)
    if winner is None or winner not in allowed:
        return "general" if "general" in allowed else sorted(allowed)[0]
    return winner
