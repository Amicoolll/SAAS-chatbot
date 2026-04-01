"""Keyword + pattern-based intent classification (configurable allowed labels)."""

from __future__ import annotations

from collections.abc import Sequence

from app.services.query_understanding.rules import (
    INTENT_KEYWORDS,
    INTENT_PATTERNS,
    INTENT_TIE_BREAK_ORDER,
)


def _looks_opaque_or_gibberish(normalized: str) -> bool:
    tokens = [t for t in normalized.split() if any(c.isalpha() for c in t)]
    if not tokens:
        return True
    vow = frozenset("aeiou")
    for t in tokens:
        letters = [c for c in t if c.isalpha()]
        if len(letters) >= 4:
            vowel_count = sum(1 for c in letters if c in vow)
            if vowel_count == 0:
                return True
            if len(letters) >= 8 and vowel_count / len(letters) < 0.22:
                return True
    return False


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _score_intents(normalized: str, raw: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        total = 0
        for kw in keywords:
            if kw in normalized:
                total += 1
        if total:
            scores[intent] = total
    for intent, pattern in INTENT_PATTERNS:
        if pattern.search(raw):
            scores[intent] = scores.get(intent, 0) + 2
    return scores


def _pick_with_tiebreak(candidates: dict[str, int]) -> str | None:
    if not candidates:
        return None
    best_score = max(candidates.values())
    tops = [i for i, s in candidates.items() if s == best_score]
    if len(tops) == 1:
        return tops[0]
    order = {i: idx for idx, i in enumerate(INTENT_TIE_BREAK_ORDER)}
    tops.sort(key=lambda i: (order.get(i, 999), i))
    return tops[0]


def classify_intent(query: str, allowed_intents: Sequence[str]) -> str:
    """
    Return one intent from ``allowed_intents`` using ``rules.INTENT_KEYWORDS``
    and ``rules.INTENT_PATTERNS``.
    """
    allowed = set(allowed_intents)
    if not query or not query.strip():
        return "faq" if "faq" in allowed else sorted(allowed)[0]

    normalized = _normalize(query)
    scores = _score_intents(normalized, query)
    scored_allowed = {i: s for i, s in scores.items() if i in allowed}

    if not scored_allowed:
        if _looks_opaque_or_gibberish(normalized):
            return "search" if "search" in allowed else sorted(allowed)[0]
        # Short question → faq; otherwise search
        if len(normalized.split()) <= 6 and "faq" in allowed:
            return "faq"
        return "search" if "search" in allowed else sorted(allowed)[0]

    winner = _pick_with_tiebreak(scored_allowed)
    if winner is None:
        return "search" if "search" in allowed else sorted(allowed)[0]
    return winner
