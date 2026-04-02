"""
Greeting prefix stripping and greeting-only detection for chat / RAG routing.

Plug a future model via ``IntentClassifierProtocol`` (optional ``processIncomingMessage`` kwarg).

FastAPI integration (sketch):

    from fastapi import FastAPI
    from app.services.greetingHandler import processIncomingMessage

    @app.post("/chat")
    def chat(body: dict) -> dict:
        raw = body["question"]
        gp = processIncomingMessage(raw)
        if gp.skipRetrieval:
            return {"mode": "conversational", "usedQuery": raw}
        return {"mode": "kb", "usedQuery": gp.cleanedQuery}
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

_NON_RETRIEVAL_EXACT_NORMALIZED: frozenset[str] = frozenset(
    {
        "tell me a joke",
    }
)

# After a greeting, these carry no retrieval intent (avoid RAG on e.g. "again" alone).
_GREETING_SOFT_TAIL: frozenset[str] = frozenset(
    {
        "again",
        "there",
        "back",
        "thanks",
        "thx",
        "ty",
        "thank",
        "thankyou",
        "thank-you",
        "thank you",
        "buddy",
        "mate",
        "folks",
    }
)

_SEPARATOR_CHARS: frozenset[str] = frozenset(" \t\n\r,;")

_MULTI_HEAD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^good\s+(morning|afternoon|evening)\b",
        r"^h+i+\s+(there|all|team|everyone|guys|mate)\b",
        r"^he+l+o+\s+(there|all|team|everyone|guys|mate|world)\b",
        r"^he+y+\s+(there|all|team|everyone|guys|mate)\b",
        r"^he+l+o+\s+world\b",
    )
)

_SINGLE_HEAD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^h+i+\b",
        r"^he+l+o+\b",
        r"^he+y+\b",
        r"^h{1,2}\b(?=\s|$|[,;!?.])",
    )
)


@runtime_checkable
class IntentClassifierProtocol(Protocol):
    def classify(self, text: str) -> str: ...


@dataclass(frozen=True)
class GreetingProcessResult:
    originalText: str
    normalizedText: str
    cleanedQuery: str
    isGreetingOnly: bool
    hadGreetingPrefix: bool
    skipRetrieval: bool

    def toDict(self) -> dict[str, Any]:
        return {
            "originalText": self.originalText,
            "normalizedText": self.normalizedText,
            "cleanedQuery": self.cleanedQuery,
            "isGreetingOnly": self.isGreetingOnly,
            "hadGreetingPrefix": self.hadGreetingPrefix,
            "skipRetrieval": self.skipRetrieval,
        }


def normalizeText(text: str) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _collapseForExactMatch(normalized: str) -> str:
    return " ".join(normalized.strip().lower().split())


def _isNoiseOnly(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    return not bool(re.search(r"[\w]", stripped, flags=re.UNICODE))


def _nonRetrievalExact(normalized: str) -> bool:
    return _collapseForExactMatch(normalized) in _NON_RETRIEVAL_EXACT_NORMALIZED


def _isGreetingSoftTail(remainder: str) -> bool:
    t = _collapseForExactMatch(remainder)
    if not t:
        return True
    if t in _GREETING_SOFT_TAIL:
        return True
    return t.replace("-", " ") in _GREETING_SOFT_TAIL


def _matchLongestGreeting(lower_suffix: str) -> re.Match[str] | None:
    best: re.Match[str] | None = None
    for pat in _MULTI_HEAD_PATTERNS:
        m = pat.match(lower_suffix)
        if m and (best is None or m.end() > best.end()):
            best = m
    if best is None:
        for pat in _SINGLE_HEAD_PATTERNS:
            m = pat.match(lower_suffix)
            if m and (best is None or m.end() > best.end()):
                best = m
    return best


def _stripOneGreetingFromStart(original: str, lowered: str, start: int) -> tuple[int, bool] | None:
    n = len(original)
    i = start
    while i < n and original[i] in _SEPARATOR_CHARS:
        i += 1
    if i >= n:
        return None
    sub = lowered[i:]
    m = _matchLongestGreeting(sub)
    if not m:
        return None
    return i + m.end(), True


def _stripLeadingGreetingsRaw(text: str) -> tuple[str, bool]:
    original = text.strip()
    if not original:
        return "", False
    lowered = original.lower()
    offset = 0
    peeled_any = False
    while True:
        step = _stripOneGreetingFromStart(original, lowered, offset)
        if step is None:
            break
        offset, _peeled = step
        peeled_any = True
    while offset < len(original) and original[offset] in _SEPARATOR_CHARS:
        offset += 1
    remainder = original[offset:]
    return remainder, peeled_any


def _segmentIsGreetingToken(segment: str) -> bool:
    t = segment.strip().lower()
    if not t or _isNoiseOnly(t):
        return True
    if t in {"hi", "hello", "hey", "hii", "heyy", "yo", "hiya"}:
        return True
    if re.fullmatch(r"h+i+", t):
        return True
    if re.fullmatch(r"he+l+o+", t):
        return True
    if re.fullmatch(r"he+y+", t):
        return True
    if re.fullmatch(r"h{1,2}", t):
        return True
    if re.match(r"^good\s+(morning|afternoon|evening)$", t):
        return True
    if any(pat.match(t) for pat in _MULTI_HEAD_PATTERNS):
        return True
    if any(pat.match(t) for pat in _SINGLE_HEAD_PATTERNS):
        return True
    return t in _GREETING_SOFT_TAIL or t.replace("-", " ") in _GREETING_SOFT_TAIL


def _commaSeparatedGreetingsOnly(text: str) -> bool:
    raw = text.strip()
    if not raw:
        return False
    parts = [p.strip() for p in re.split(r"[,;]+", raw) if p.strip()]
    if len(parts) < 2:
        return False
    return all(_segmentIsGreetingToken(p) for p in parts)


def extractGreetingFreeQuery(text: str) -> str:
    norm = normalizeText(text)
    if _nonRetrievalExact(norm):
        return norm
    stripped = text.strip()
    if _commaSeparatedGreetingsOnly(stripped):
        return ""
    remainder, peeled = _stripLeadingGreetingsRaw(stripped)
    if not peeled:
        return stripped
    rest = remainder.strip()
    if _isGreetingSoftTail(rest):
        return ""
    return rest


def isGreetingOnly(text: str) -> bool:
    if not text or not text.strip():
        return False
    norm = normalizeText(text)
    if _nonRetrievalExact(norm):
        return False
    stripped = text.strip()
    if _commaSeparatedGreetingsOnly(stripped):
        return True
    remainder, peeled = _stripLeadingGreetingsRaw(stripped)
    rest = remainder.strip()
    return peeled and (_isNoiseOnly(rest) or _isGreetingSoftTail(rest))


def processIncomingMessage(
    text: str,
    intentClassifier: IntentClassifierProtocol | None = None,
) -> GreetingProcessResult:
    if intentClassifier is not None:
        _ = intentClassifier.classify(text)

    original = text
    normalized = normalizeText(original)

    if _nonRetrievalExact(normalized):
        return GreetingProcessResult(
            originalText=original,
            normalizedText=normalized,
            cleanedQuery=normalized,
            isGreetingOnly=False,
            hadGreetingPrefix=False,
            skipRetrieval=True,
        )

    stripped = original.strip()
    comma_only = _commaSeparatedGreetingsOnly(stripped)
    remainder, peeled = _stripLeadingGreetingsRaw(stripped)

    if comma_only:
        cleaned = ""
        greeting_only = True
        had_prefix = False
    elif peeled:
        rest_after = remainder.strip()
        soft_tail = _isGreetingSoftTail(rest_after)
        greeting_only = _isNoiseOnly(rest_after) or soft_tail
        had_prefix = not _isNoiseOnly(rest_after) and not soft_tail
        cleaned = "" if greeting_only else rest_after
    else:
        cleaned = stripped
        greeting_only = False
        had_prefix = False

    skip_retrieval = bool(greeting_only or _nonRetrievalExact(normalized))
    is_greeting_flag = bool(greeting_only)

    return GreetingProcessResult(
        originalText=original,
        normalizedText=normalized,
        cleanedQuery=cleaned,
        isGreetingOnly=is_greeting_flag,
        hadGreetingPrefix=had_prefix,
        skipRetrieval=skip_retrieval,
    )
