"""
Map query-understanding output to retrieval strategy (hybrid vs vector-only).

Hybrid combines dense (pgvector) + sparse (FTS). Use it when analytics suggest
IDs, policies, keyword-heavy domains, or multi-hop — not for generic FAQ/summary
where semantics alone often suffice.
"""

from __future__ import annotations

import re

from app.schemas.query_understanding import QueryUnderstandingResult

# Intents where lexical grounding usually helps retrieval quality.
_HYBRID_INTENTS = frozenset(
    {
        "status_lookup",
        "search",
        "troubleshooting",
        "policy_lookup",
        "comparison",
        "analysis",
        "workflow_help",
    }
)

# Domains where exact phrases / compliance / operational terms are common.
_HYBRID_DOMAINS = frozenset(
    {
        "logistics",
        "finance",
        "legal",
        "medical",
        "hr",
        "support",
        "multi_domain",
    }
)

_SKIP_KB_EXACT = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "tell me a joke",
    }
)

_SKIP_KB_RE = re.compile(
    r"^(hi|hello|hey)(\s+(there|all|team))?[\s!?.]*$"
    r"|^good (morning|afternoon|evening)\b[\w\s!?.]*$",
    re.I,
)


def should_skip_kb_retrieval(question: str, qu: QueryUnderstandingResult) -> bool:
    """
    True for greetings / light chit-chat where vector search would pull random
    chunks and confuse the model ("context does not contain...").

    Only when analytics say general, low-risk FAQ with no retrieval-oriented flags.
    """
    if qu.domain != "general" or qu.intent != "faq":
        return False
    if qu.complexity != "simple":
        return False
    if qu.risk_level != "low":
        return False
    if qu.needs_exact_match or qu.requires_citations:
        return False
    if qu.needs_multi_hop or qu.needs_live_data:
        return False

    s = " ".join(question.strip().lower().split())
    if s in _SKIP_KB_EXACT:
        return True
    return bool(_SKIP_KB_RE.match(s.strip()))


def should_use_hybrid_retrieval(qu: QueryUnderstandingResult) -> bool:
    """
    Return True when FTS+vector fusion is preferred for this query.

    Vector-only (False) is preferred for e.g. general ``faq`` / ``summarization``
    on ``general`` domain without exact-match or citation requirements.
    """
    if qu.needs_exact_match or qu.requires_citations:
        return True
    if qu.needs_multi_hop:
        return True
    if qu.needs_live_data:
        return True
    if qu.domain in _HYBRID_DOMAINS:
        return True
    if qu.intent in _HYBRID_INTENTS:
        return True
    return False
