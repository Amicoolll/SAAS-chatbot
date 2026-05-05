"""Hard domain filter via semantic similarity.

Used when a tenant has the ``strict_domain`` feature flag enabled — e.g.
aviation-only deployments that must refuse unrelated questions even if
the LLM would otherwise answer them.

How it works:
    1. At first use, embed a curated list of in-domain seed sentences
       and take their mean vector (the "domain centroid"). The centroid
       is cached in-process so the OpenAI call happens only once per
       worker lifetime.
    2. For each incoming question, compute cosine similarity between the
       question's embedding (already computed for RAG retrieval) and the
       centroid.
    3. If similarity >= threshold → on-domain, proceed with RAG.
       Otherwise → refuse.

Fail-open: if the centroid can't be computed (OpenAI down, empty seeds),
``is_on_domain`` returns True so users aren't blocked by a broken guard.
The prompt-level guardrail in the aviation agent still applies.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Sequence

from app.core.config import settings
from app.services.openai_client import embed_texts

logger = logging.getLogger(__name__)

# Aviation seed sentences covering flight ops, systems, maintenance,
# regulatory, ATC/navigation, weather, safety, and ground ops. Adding more
# seeds (especially in under-represented subdomains) is the easiest way to
# reduce false negatives without changing the threshold.
_AVIATION_SEEDS: list[str] = [
    # Flight operations
    "V1 speed calculation for a Boeing 737-800",
    "Engine failure procedure during takeoff",
    "Holding pattern entry type for a northerly approach",
    "Cold weather takeoff performance calculation",
    "Approach briefing items for CAT III ILS",
    "Stabilized approach criteria at 1000 feet",
    "Go-around procedure after rejected landing",
    "Crosswind landing technique and limits",
    # Aircraft systems
    "APU start sequence on an Airbus A320",
    "Hydraulic system failure warning indications",
    "Flight control reversion modes in direct law",
    "Electrical bus configuration during ground operations",
    "Fuel imbalance correction procedure",
    # MEL / maintenance
    "MEL item 24-11-01 APU generator dispatch",
    "Deferred defect procedure for inoperative thrust reverser",
    "CDL allowance for a missing static wick",
    "Line maintenance inspection intervals",
    "Engine fan blade inspection threshold",
    "Borescope inspection requirements after bird strike",
    # Regulatory
    "FAA Part 121 flight duty time regulations",
    "EASA crew rest requirements for ULR flights",
    "ICAO Annex 6 operational limits",
    "DGCA night currency requirements for first officers",
    "RVSM airspace certification requirement",
    # ATC / navigation
    "Standard holding pattern at 250 knots",
    "Oceanic HF position reporting procedure",
    "SID transition at a complex airport",
    "Missed approach procedure for an RNAV GNSS approach",
    # Weather
    "METAR interpretation for a cold front passage",
    "TAF windshear forecast decoding",
    "Volcanic ash encounter procedure",
    "Icing condition definitions and anti-ice activation",
    # Safety / emergency
    "Cabin depressurization emergency descent profile",
    "Ditching procedure in a widebody aircraft",
    "Smoke fire fumes checklist priorities",
    "TCAS resolution advisory response maneuver",
    # Ground operations
    "Pushback clearance procedure",
    "De-icing fluid hold-over times",
    "Fueling quantity verification",
    "Weight and balance sheet completion",
]

_centroid: list[float] | None = None
_centroid_lock = threading.Lock()


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _compute_centroid(seeds: list[str]) -> list[float] | None:
    if not seeds:
        return None
    try:
        vectors = embed_texts(seeds)
    except Exception:
        logger.exception("domain_guard_centroid_embed_failed")
        return None
    if not vectors:
        return None
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            acc[i] += x
    n = float(len(vectors))
    return [x / n for x in acc]


def get_centroid() -> list[float] | None:
    """Return the cached centroid vector, computing on first call."""
    global _centroid
    if _centroid is not None:
        return _centroid
    with _centroid_lock:
        if _centroid is not None:
            return _centroid
        _centroid = _compute_centroid(_AVIATION_SEEDS)
    return _centroid


def reset_cache() -> None:
    """Clear the cached centroid (tests + hot-reload of the seed list)."""
    global _centroid
    with _centroid_lock:
        _centroid = None


def is_on_domain(
    question_embedding: Sequence[float],
    threshold: float | None = None,
) -> bool:
    """Return True when the question is semantically close to the domain
    centroid. Fail-open: if the centroid is missing, return True so a
    broken guard doesn't block users.
    """
    centroid = get_centroid()
    if centroid is None:
        return True
    if threshold is None:
        threshold = settings.DOMAIN_GUARD_THRESHOLD
    sim = _cosine(question_embedding, centroid)
    logger.debug("domain_guard similarity=%.3f threshold=%.3f", sim, threshold)
    return sim >= threshold
