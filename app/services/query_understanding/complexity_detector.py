"""Heuristic query complexity: simple | medium | complex."""

from __future__ import annotations

import re

from app.services.query_understanding.rules import (
    COMPLEXITY_COMPLEX_KEYWORDS,
    COMPLEXITY_COMPLEX_MIN_WORDS,
    COMPLEXITY_SIMPLE_MAX_WORDS,
    EXACT_MATCH_ORDER_STYLE_PATTERN,
)

def detect_complexity(query: str, domain: str | None = None) -> str:
    """
    Classify complexity:

    - **simple**: short, no compound reasoning markers
    - **complex**: comparison/analysis signals or very long text or many clauses
    - **medium**: default between those
    """
    if not query or not query.strip():
        return "simple"

    lowered = query.lower()
    words = lowered.split()
    n = len(words)

    if not any(ch.isalnum() for ch in query):
        return "simple"

    if re.fullmatch(r"\s*track\s+package\s+\d+\s*", lowered):
        return "simple"

    if "summar" in lowered:
        return "medium"

    if "explain" in lowered and "briefly" in lowered:
        return "medium"

    support_trouble_markers = (
        "not working",
        "crashing",
        "payment failure",
        "unable to connect",
        "fix issue",
    )
    if any(m in lowered for m in support_trouble_markers):
        return "medium"

    if any(kw in lowered for kw in COMPLEXITY_COMPLEX_KEYWORDS):
        return "complex"

    if n >= COMPLEXITY_COMPLEX_MIN_WORDS:
        return "complex"

    if lowered.count("?") > 1 and any(ch.isalnum() for ch in query):
        return "complex"

    if domain in ("medical", "finance"):
        return "medium"

    if domain == "hr" and not ("holiday" in lowered and len(words) <= 5):
        return "medium"

    # Minimal track phrasing stays simple; other ID-style logistics questions are medium.
    if domain == "logistics" and re.search(r"\d", query):
        if not re.fullmatch(r"\s*track\s+package\s+\d+\s*", lowered):
            return "medium"

    # Tracking / ID questions and "why" explanations need more than a one-liner retrieval
    if re.search(r"\d{4,}", query) and not re.fullmatch(
        r"\s*track\s+package\s+\d+\s*", lowered
    ):
        return "medium"

    if EXACT_MATCH_ORDER_STYLE_PATTERN.search(query) and not re.fullmatch(
        r"\s*track\s+package\s+\d+\s*", lowered
    ):
        return "medium"

    if lowered.startswith("why ") and n >= 4:
        return "medium"

    if n <= COMPLEXITY_SIMPLE_MAX_WORDS:
        return "simple"

    return "medium"
