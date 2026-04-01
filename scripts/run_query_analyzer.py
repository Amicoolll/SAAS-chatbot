#!/usr/bin/env python3
"""
Run the rule-based query analyzer on many questions.

Usage:
  python scripts/run_query_analyzer.py

Edit QUESTION_CASES below: add rows with "q" and optional "expect" (partial dict).
Only keys present in "expect" are checked; mismatch prints FAIL and sets exit code 1.

Uses minimal Settings so DATABASE_URL is not required for this script.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Repo root on sys.path
_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from app.core.config import Settings
from app.services.query_understanding import analyze_query


def _settings() -> Settings:
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


# Add or edit questions here. "expect" is optional; omit it to only print output.
QUESTION_CASES: list[dict] = [
    {
        "q": "Why is shipment 18472 delayed?",
        "expect": {
            "domain": "logistics",
            "intent": "status_lookup",
            "complexity": "medium",
            "risk_level": "medium",
            "needs_exact_match": True,
            "needs_multi_hop": False,
            "needs_live_data": True,
            "requires_citations": False,
        },
    },
    {
        "q": "What are the side effects of ibuprofen for a patient on blood thinners?",
        "expect": {
            "domain": "medical",
            "risk_level": "high",
            "requires_citations": True,
        },
    },
    {
        "q": "How do I reset my password if I cannot log in?",
        "expect": {
            "domain": "support",
            "risk_level": "low",
        },
    },
    {
        "q": "Compare Q1 revenue vs Q2 revenue for APAC",
        "expect": {
            "domain": "finance",
            "intent": "comparison",
            "complexity": "complex",
            "needs_multi_hop": True,
        },
    },
    {
        "q": "What is the capital of France?",
        "expect": {
            "domain": "general",
            "intent": "faq",
        },
    },
]


def main() -> int:
    s = _settings()
    failed = 0
    print(f"Running {len(QUESTION_CASES)} case(s)…\n")

    for i, case in enumerate(QUESTION_CASES, start=1):
        q = case["q"]
        result = analyze_query(q, settings=s)
        payload = result.model_dump()
        print(f"--- Case {i} ---")
        print(f"Q: {q}")
        print(json.dumps(payload, indent=2))

        expect = case.get("expect")
        if expect:
            mismatches: list[str] = []
            for key, want in expect.items():
                got = payload.get(key)
                if got != want:
                    mismatches.append(f"  {key}: want={want!r} got={got!r}")
            if mismatches:
                print("EXPECTATION FAIL:")
                print("\n".join(mismatches))
                failed += 1
            else:
                print("EXPECTATION OK (partial keys checked)")
        print()
    if failed:
        print(f"Done: {failed} case(s) failed expectation checks.")
        return 1
    print("Done: all expectation checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
