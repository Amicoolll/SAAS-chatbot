#!/usr/bin/env python3
"""Compare analyze_query() to golden EXPECTED_OUTPUTS."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))

import importlib.util

from app.core.config import Settings
from app.services.query_understanding import analyze_query

_golden_path = _root / "scripts" / "golden_expected_outputs.py"
_spec = importlib.util.spec_from_file_location("golden_expected_outputs", _golden_path)
_golden = importlib.util.module_from_spec(_spec)
assert _spec.loader
_spec.loader.exec_module(_golden)
EXPECTED_OUTPUTS = _golden.EXPECTED_OUTPUTS
KEYS = _golden.KEYS


def _settings() -> Settings:
    return Settings.model_construct(
        DATABASE_URL="postgresql://localhost/test",
        QUERY_UNDERSTANDING_DOMAINS=[
            "medical", "logistics", "support", "hr", "finance", "legal",
            "general", "multi_domain",
        ],
        QUERY_UNDERSTANDING_INTENTS=[
            "faq", "search", "summarization", "troubleshooting", "status_lookup",
            "policy_lookup", "comparison", "analysis", "workflow_help",
        ],
    )


def main() -> int:
    s = _settings()
    failed = 0
    for i, exp in enumerate(EXPECTED_OUTPUTS, start=1):
        q = exp["query"]
        got = analyze_query(q, settings=s).model_dump()
        mismatches = []
        for k in KEYS:
            if got[k] != exp[k]:
                mismatches.append(f"  {k}: expected {exp[k]!r} got {got[k]!r}")
        print(f"\n--- {i}. {q!r} ---")
        if mismatches:
            print("FAIL:")
            print("\n".join(mismatches))
            failed += 1
        else:
            print("OK")
            print(json.dumps({k: got[k] for k in KEYS}, indent=2))
    print(f"\nTotal: {len(EXPECTED_OUTPUTS)}; failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
