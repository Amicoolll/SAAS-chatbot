"""Unit tests for ``_answer_is_off_topic_redirect`` — the detector that
spots when a domain-locked agent (e.g. aviation) answers with a refusal
redirect rather than a real grounded answer. When this fires, the
chat_pg endpoint must strip the irrelevant sources and re-label the
mode so the response is self-consistent.
"""

from __future__ import annotations

import pytest

from app.api import chat_pg as chat_mod


@pytest.mark.parametrize(
    "answer",
    [
        "I'm focused on providing information related to aviation operations.",
        "I am set up to answer aviation-related questions only.",
        "I can only answer aviation topics. Please ask about flight ops.",
        "That's outside aviation — please ask about flight procedures.",
        "Not an aviation question. Let me redirect you.",
        "I only answer aviation-related questions.",
    ],
)
def test_redirect_detector_flags_refusal_answers(answer: str) -> None:
    assert chat_mod._answer_is_off_topic_redirect(answer) is True


@pytest.mark.parametrize(
    "answer",
    [
        "The V1 speed for a B737-800 at MTOW is 155 knots per the FCOM.",
        "Per SOP 7.4, the crew must perform a briefing 10 minutes before descent.",
        "MEL item 24-11-01 allows dispatch with one APU generator inoperative.",
        "The maintenance manual specifies a 150 FH inspection interval.",
        "According to FAR Part 121, minimum crew rest is 10 hours.",
    ],
)
def test_redirect_detector_does_not_flag_real_grounded_answers(answer: str) -> None:
    """Regression guard: real aviation answers must not be mis-classified
    as refusals. If this test starts failing, the signal list is too broad.
    """
    assert chat_mod._answer_is_off_topic_redirect(answer) is False


def test_redirect_detector_is_case_insensitive():
    assert (
        chat_mod._answer_is_off_topic_redirect(
            "I'M FOCUSED ON providing aviation information."
        )
        is True
    )
