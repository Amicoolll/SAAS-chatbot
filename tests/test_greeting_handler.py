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


# ---------------------------------------------------------------------------
# Extended greeting coverage: casual openers, closers, thanks, acks.
# These tests drive the constants in greetingHandler.py — adding entries there
# is the expected extension point.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "sup",
        "SUP",
        "wassup",
        "whatsup",
        "what's up",
        "whats up",
        "howdy",
        "Howdy",
        "greetings",
        "hola",
        "namaste",
        "yo",
    ],
)
def testIsGreetingOnly_acceptsCasualGreetings(raw: str) -> None:
    assert isGreetingOnly(raw) is True


@pytest.mark.parametrize(
    "raw",
    [
        "good night",
        "Good Night",
        "good day",
    ],
)
def testIsGreetingOnly_acceptsExtendedTimeOfDay(raw: str) -> None:
    gp = processIncomingMessage(raw)
    assert gp.skipRetrieval is True


@pytest.mark.parametrize(
    ("raw", "expected_cleaned"),
    [
        ("hola, what's the weather?", "what's the weather?"),
        ("howdy, can you help?", "can you help?"),
        ("sup, show me the report", "show me the report"),
        ("Greetings, what are the latest docs?", "what are the latest docs?"),
        ("what's up, when is the release?", "when is the release?"),
        ("yo can you open the file?", "can you open the file?"),
    ],
)
def testExtractGreetingFreeQuery_stripsCasualGreetingPrefix(
    raw: str, expected_cleaned: str
) -> None:
    assert extractGreetingFreeQuery(raw) == expected_cleaned


@pytest.mark.parametrize(
    "raw",
    [
        "bye",
        "Goodbye",
        "good bye",
        "bye bye",
        "see ya",
        "see you",
        "see you later",
        "take care",
        "later",
        "ttyl",
        "cya",
        "cu",
        "good night",
        "gn",
        "farewell",
        "catch you later",
    ],
)
def testProcessIncomingMessage_closersSkipRetrieval(raw: str) -> None:
    gp = processIncomingMessage(raw)
    assert gp.skipRetrieval is True


@pytest.mark.parametrize(
    "raw",
    [
        "thanks",
        "Thanks",
        "thank you",
        "thankyou",
        "thank-you",
        "thx",
        "ty",
        "thanks a lot",
        "thanks so much",
        "thank you very much",
        "appreciate it",
        "much appreciated",
        "cheers",
    ],
)
def testProcessIncomingMessage_thanksSkipRetrieval(raw: str) -> None:
    gp = processIncomingMessage(raw)
    assert gp.skipRetrieval is True


@pytest.mark.parametrize(
    "raw",
    [
        "ok",
        "Okay",
        "k",
        "kk",
        "cool",
        "nice",
        "great",
        "awesome",
        "perfect",
        "got it",
        "gotcha",
        "understood",
        "noted",
        "alright",
        "all right",
        "sure",
        "sounds good",
        "yes",
        "yep",
        "yeah",
        "no",
        "nope",
        "nah",
        "hmm",
    ],
)
def testProcessIncomingMessage_acknowledgementsSkipRetrieval(raw: str) -> None:
    gp = processIncomingMessage(raw)
    assert gp.skipRetrieval is True


@pytest.mark.parametrize(
    "raw",
    [
        "ok.",
        "ok!",
        "ok?",
        "thanks!",
        "thanks.",
        "great!",
    ],
)
def testProcessIncomingMessage_acksToleratePunctuation(raw: str) -> None:
    gp = processIncomingMessage(raw)
    assert gp.skipRetrieval is True


# --- Regression guards: colliding content queries must NOT be flagged. ---


@pytest.mark.parametrize(
    "raw",
    [
        "how do you do",
        "how do you do?",
        "How Do You Do",
        "how are you",
        "how are you?",
        "how are you doing",
        "how have you been",
        "how's it going",
        "hows it going",
        "how r u",
        "how u doing",
        "how is it going",
    ],
)
def testProcessIncomingMessage_pleasantryGreetingsSkipRetrieval(raw: str) -> None:
    gp = processIncomingMessage(raw)
    assert gp.skipRetrieval is True, raw


@pytest.mark.parametrize(
    "raw",
    [
        "how do I reset my password?",
        "how do you calibrate the pitot tube?",
        "how are you planning to deploy the feature?",
        "how is the server uptime?",
        "how do I get the latest report",
    ],
)
def testProcessIncomingMessage_realHowQuestionsStillReachRag(raw: str) -> None:
    """Regression guard: adding pleasantry exact-matches must NOT swallow
    legitimate 'how' questions that are real queries.
    """
    gp = processIncomingMessage(raw)
    assert gp.skipRetrieval is False, raw


@pytest.mark.parametrize(
    "raw",
    [
        "great wall of china",
        "morning news headlines",
        "yes she did file the report",
        "no fly zone history",
        "nice city in france",
        "perfect square definition",
        "later editions of the book",
        "ok corral shootout",
        "sure thing is the name of the song",
    ],
)
def testProcessIncomingMessage_doesNotSkipContentQuestions(raw: str) -> None:
    gp = processIncomingMessage(raw)
    assert gp.skipRetrieval is False, raw
    assert gp.isGreetingOnly is False, raw
