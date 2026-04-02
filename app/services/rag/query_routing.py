"""
Map query-understanding output to retrieval strategy (hybrid vs vector-only).

Hybrid combines dense (pgvector) + sparse (FTS). Use it when analytics suggest
IDs, policies, keyword-heavy domains, or multi-hop — not for generic FAQ/summary
where semantics alone often suffice.
"""

from __future__ import annotations

from app.schemas.query_understanding import QueryUnderstandingResult
from app.services.greetingHandler import processIncomingMessage

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

def should_skip_kb_retrieval(question: str, qu: QueryUnderstandingResult) -> bool:
    """
    True when ``greetingHandler`` marks the message as non-retrieval (greeting-only
    or configured light chit-chat). ``qu`` is kept for call-site compatibility and
    optional future classifier fusion.
    """
    _ = qu
    return processIncomingMessage(question).skipRetrieval


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
