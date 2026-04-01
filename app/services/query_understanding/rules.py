"""
Central keyword and pattern definitions for rule-based query understanding.

Keep matching data here; keep scoring logic in the classifier modules.
"""

from __future__ import annotations

import re
from typing import Final

# --- Domain keywords (excluding "general"; that is the fallback) ---

DOMAIN_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "medical": (
        "symptom",
        "symptoms",
        "diagnosis",
        "diagnose",
        "medication",
        "medicine",
        "medicines",
        "drug",
        "drugs",
        "pill",
        "pills",
        "tablet",
        "tablets",
        "fever",
        "hypertension",
        "diabetes",
        "prescription",
        "prescribe",
        "otc",
        "over the counter",
        "over-the-counter",
        "antibiotic",
        "dose",
        "dosage",
        "patient",
        "doctor",
        "physician",
        "clinic",
        "hospital",
        "fda",
        "treatment",
        "side effect",
        "allergy",
        "mri",
        "x-ray",
        "lab result",
        "icd",
        "hypertension",
        "diabetes",
    ),
    "logistics": (
        "shipment",
        "shipments",
        "order id",
        "track package",
        "package",
        "tracking number",
        "tracking",
        "carrier",
        "freight",
        "delivery",
        "deliveries",
        "where is my",
        "for order",
        "warehouse",
        "inventory",
        "sku",
        "asn",
        "bol",
        "customs",
        "dispatch",
        "route",
        "fulfillment",
        "logistics",
        "courier",
        "delayed",
        "delay",
    ),
    "support": (
        "ticket",
        "bug",
        "error code",
        "not working",
        "broken",
        "login",
        "password reset",
        "reset password",
        "crash",
        "how do i",
        "can't access",
        "cannot access",
        "support",
        "troubleshoot",
        "my profile",
        "update my profile",
        "email settings",
        "change email",
        "payment failure",
        "fix issue",
        "unable to connect",
        "connect to server",
        "i need help",
        "need help",
        "profile",
    ),
    "hr": (
        "leave policy",
        "company holidays",
        "holidays",
        "onboarding",
        "pto",
        "vacation",
        "sick leave",
        "hiring",
        "onboarding",
        "offboarding",
        "performance review",
        "promotion",
        "salary",
        "benefits",
        "401k",
        "payroll",
        "hr ",
        " human resources",
        "policy vacation",
    ),
    "finance": (
        "gst",
        "interest rate",
        "insurance plans",
        "insurance plan",
        "insurance",
        "investment policy",
        "filing process",
        "loan",
        "loans",
        "invoice",
        "invoices",
        "payment",
        "payments",
        "budget",
        "tax",
        "revenue",
        "profit",
        "ledger",
        "accounts payable",
        "accounts receivable",
        "quarterly",
        "fiscal",
        "audit",
        "financial statement",
        "expense",
    ),
    "legal": (
        "contract",
        "contracts",
        "lawsuit",
        "litigation",
        "liability",
        "indemnify",
        "subpoena",
        "compliance",
        "gdpr",
        "hipaa",
        "nda",
        "terms of service",
        "intellectual property",
        "statute",
        "regulated",
    ),
    "multi_domain": (
        "as well as",
        "and also",
        "both ",
        "on one hand",
        "not only",
        "in addition",
    ),
}

# When two domains tie, prefer earlier entries (more specific for ops).
DOMAIN_TIE_BREAK_ORDER: Final[tuple[str, ...]] = (
    "legal",
    "medical",
    "finance",
    "logistics",
    "hr",
    "support",
    "multi_domain",
)

# --- Intent keywords / phrases ---

INTENT_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "faq": (
        "what is",
        "what are",
        "who is",
        "when was",
        "where is",
        "where can i",
        "how does",
        "how do i",
        "definition",
        "meaning of",
    ),
    "search": (
        "i need help",
        "find",
        "look up",
        "lookup",
        "search",
        "where can i find",
        "show me",
        "list ",
        "retrieve",
        "tell me something",
        "symptoms",
        "what medicine",
        "which medicine",
        "should i take",
        "suggest dosage",
        "company holidays",
        "interest rate",
    ),
    "summarization": (
        "summarize",
        "summary",
        "tl;dr",
        "tldr",
        "in brief",
        "key points",
        "bullet points",
    ),
    "troubleshooting": (
        "what can i do",
        "login issue",
        "payment failure",
        "fix",
        "broken",
        "error",
        "failing",
        "not working",
        "won't",
        "doesn't work",
        "issue with",
        "unable to",
        "crashing",
    ),
    "status_lookup": (
        "status",
        "delayed",
        "where is my",
        "tracking",
        "eta",
        "arrived",
        "shipped",
        "current state",
    ),
    "policy_lookup": (
        "treatment protocol",
        "protocol for",
        "policy",
        "policies",
        "guideline",
        "permitted",
        "allowed",
        "requirement",
        "according to",
    ),
    "comparison": (
        "compare",
        "versus",
        " vs ",
        "difference between",
        "pros and cons",
        "better than",
        "which is better",
    ),
    "analysis": (
        "show impact",
        "analyze",
        "analyse",
        "analysis",
        "trend",
        "root cause",
        "why did",
        "explain why",
        "impact of",
    ),
    "workflow_help": (
        "step by step",
        "how to calculate",
        "calculate tax",
        "employee onboarding",
        "onboarding process",
        "workflow",
        "process for",
        "filing process",
        "procedure",
        "checklist",
    ),
}

INTENT_TIE_BREAK_ORDER: Final[tuple[str, ...]] = (
    "troubleshooting",
    "status_lookup",
    "policy_lookup",
    "workflow_help",
    "analysis",
    "comparison",
    "summarization",
    "search",
    "faq",
)

# Regex patterns for intent (first match can boost score)
INTENT_PATTERNS: Final[list[tuple[str, re.Pattern[str]]]] = [
    (
        "search",
        re.compile(
            r"\b(what|which)\s+(medicine|drug|medication|pill|tablet)\b",
            re.I,
        ),
    ),
    ("search", re.compile(r"\bshould\s+i\s+take\b", re.I)),
    ("summarization", re.compile(r"\bexplain\b.*\bbriefly\b", re.I)),
    ("troubleshooting", re.compile(r"\bwhat\s+can\s+i\s+do\b", re.I)),
    ("status_lookup", re.compile(r"\b(order|shipment|ticket|case)\s*[#:]?\s*\d+", re.I)),
    ("status_lookup", re.compile(r"\btrack\s+package\b", re.I)),
    ("analysis", re.compile(r"\banalyze\b", re.I)),
    ("comparison", re.compile(r"\b(compare|vs\.?|versus)\b", re.I)),
    ("workflow_help", re.compile(r"\bhow\s+to\s+calculate\b", re.I)),
    ("summarization", re.compile(r"\b(summarize|summary)\b", re.I)),
    ("search", re.compile(r"\b(find|search|look\s+up)\b", re.I)),
]

# --- Complexity ---

COMPLEXITY_COMPLEX_KEYWORDS: Final[tuple[str, ...]] = (
    "compare",
    "versus",
    " vs ",
    "difference between",
    "analyze",
    "analysis",
    "relationship between",
    "on the other hand",
    "trade-off",
    "tradeoff",
    "root cause",
    "multi-step",
    "step 1",
    "show impact",
    "impact of",
)

COMPLEXITY_SIMPLE_MAX_WORDS: Final[int] = 12
COMPLEXITY_COMPLEX_MIN_WORDS: Final[int] = 45

# --- Retrieval need ---

# IDs, order numbers, long numeric tokens
EXACT_MATCH_NUMERIC_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:\b(?:id|sku|order|ticket|case|shipment|tracking)\b\s*[#:]?\s*)?\d{4,}\b",
    re.I,
)

EXACT_MATCH_CODE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Z]{2,}-[A-Z0-9]+\b|\b[A-Z0-9]{2,}-[A-Z0-9-]+\b",
)

# Shorter numeric IDs when tied to order / package / shipment / delivery
EXACT_MATCH_ORDER_STYLE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:order|shipment|package|tracking|delivery)\b[^0-9]{0,15}\d{3,}\b"
    r"|\bfor\s+order\s+\d{3,}\b|\btrack\s+package\s+\d+\b|\border\s+id\s+\d+\b",
    re.I,
)

MULTI_HOP_KEYWORDS: Final[tuple[str, ...]] = (
    "compare",
    "versus",
    " vs ",
    "difference between",
    "relationship between",
    "explain reason",
    "and also",
    "how does x relate",
    "impact on",
    "impact of",
    "cause and effect",
)

LIVE_DATA_KEYWORDS: Final[tuple[str, ...]] = (
    "last 3 days",
    "last three days",
    "interest rate",
    "rate for",
    "track package",
    "current",
    "right now",
    "as of today",
    "latest",
    "real-time",
    "realtime",
    "live ",
    "status",
    "delayed",
    "delivery",
    "eta",
    "updated",
    "in progress",
)

CITATION_KEYWORDS: Final[tuple[str, ...]] = (
    "cite",
    "citation",
    "source",
    "according to policy",
    "per policy",
    "regulation",
    "statute",
    "legal requirement",
    "clinical guideline",
)

# Domains that imply citations when combined with policy/medical/legal cues
CITATION_DOMAINS: Final[frozenset[str]] = frozenset({"legal", "medical", "finance"})

# Personal medical advice / prescribing-style questions → high risk + cite-only answers downstream
MEDICAL_PRESCRIBING_OR_ADVICE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bwhat\s+medicine\b", re.I),
    re.compile(r"\bwhich\s+medicine\b", re.I),
    re.compile(r"\bwhat\s+(drug|medication|pill|tablets?)\b", re.I),
    re.compile(r"\bwhich\s+(drug|medication|pill)\b", re.I),
    re.compile(r"\bshould\s+i\s+take\b", re.I),
    re.compile(r"\bprescrib(e|ed|ing)\b", re.I),
    re.compile(
        r"\bsuggest\s+(a\s+)?(dose|dosage|medicine|drug|medication|pill)\b",
        re.I,
    ),
    re.compile(r"\bdosage\s+for\b", re.I),
    re.compile(r"\btake\s+for\s+(my\s+)?(fever|pain|cold|cough|headache)\b", re.I),
    re.compile(r"\b(?:give|recommend)\s+me\s+(a\s+)?medicine\b", re.I),
)


def query_requests_medical_prescribing_or_advice(query: str) -> bool:
    """True when the user asks for personal drug/prescribing guidance (KB-grounding required)."""
    if not query or not query.strip():
        return False
    lowered = query.lower()
    return any(p.search(lowered) for p in MEDICAL_PRESCRIBING_OR_ADVICE_PATTERNS)
