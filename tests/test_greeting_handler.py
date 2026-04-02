"""Unit tests for ``app.services.greetingHandler``."""

from __future__ import annotations

import pytest

from app.services.greetingHandler import (
    GreetingProcessResult,
    extractGreetingFreeQuery,
    isGreetingOnly,
    normalizeText,
    processIncomingMessage,
)


@pytest.mark.parametrize(
    "raw",
    [
        "Hi",
        "  hi  ",
        "HELLO",
        "Hey!!!",
        "hii",
        "heyy",
        "Good Morning",
        "good afternoon",
        "h",
    ],
)
def testIsGreetingOnly_acceptsTypicalGreetings(raw: str) -> None:
    assert isGreetingOnly(raw) is True


@pytest.mark.parametrize(
    ("raw", "expected_cleaned"),
    [
        ("Hi, can you help me with leave policy?", "can you help me with leave policy?"),
        ("Hello I need invoice details", "I need invoice details"),
        ("HEY,,  What's the refund policy?", "What's the refund policy?"),
    ],
)
def testExtractGreetingFreeQuery_stripsLeadingGreeting(raw: str, expected_cleaned: str) -> None:
    assert extractGreetingFreeQuery(raw) == expected_cleaned


def testHelloAgain_skipRetrieval_noSpuriousRag() -> None:
    for raw in ("hello, again", "Hello again", "HEY, thanks"):
        gp = processIncomingMessage(raw)
        assert gp.skipRetrieval is True, raw
        assert gp.hadGreetingPrefix is False, raw
        assert gp.cleanedQuery == "", raw
        assert isGreetingOnly(raw) is True


def testCommaSeparatedGreetingsOnly_noRagQuery() -> None:
    assert isGreetingOnly("hello, hii , hi") is True
    gp = processIncomingMessage("hello, hii , hi")
    assert gp.skipRetrieval is True
    assert gp.cleanedQuery == ""


def testNonGreetingUnchanged() -> None:
    q = "What is the QE Prize application process?"
    gp = processIncomingMessage(q)
    assert gp.skipRetrieval is False
    assert gp.hadGreetingPrefix is False
    assert gp.cleanedQuery == q
    assert isGreetingOnly(q) is False


def testTellMeAJoke_skipRetrievalNotGreetingFlag() -> None:
    gp = processIncomingMessage("Tell me a joke")
    assert gp.skipRetrieval is True
    assert gp.isGreetingOnly is False


def testNormalizeText_collapsesWhitespace() -> None:
    assert normalizeText("  a \n\t b  ") == "a b"


def testProcessIncomingMessage_toDictShape() -> None:
    gp = processIncomingMessage("Hi")
    d = gp.toDict()
    assert set(d.keys()) >= {
        "originalText",
        "normalizedText",
        "cleanedQuery",
        "isGreetingOnly",
        "hadGreetingPrefix",
        "skipRetrieval",
    }
    assert isinstance(gp, GreetingProcessResult)
