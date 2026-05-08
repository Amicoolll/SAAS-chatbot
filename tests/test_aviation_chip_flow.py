"""Chip-action flow tests.

Exercises ``app.api.chat_pg_actions.handle_action`` end-to-end against the
in-process aviation mock backend (via FastAPI TestClient as the httpx
transport). The DB is a MagicMock — chat_pg_actions only calls ``add()``
and ``commit()`` on it.

Covers:
- ChatRequest validator (mutual exclusion of question / action)
- Empty params → first prompt
- Partial params → next missing prompt
- All params valid → dispatch + tool_executed
- Smart validation success (LLM extracts a value from messy input)
- Smart validation failure (LLM returns NONE → re-prompt with hint)
- Unknown action → 422
- Unknown agent_type / no domain → 422
- Tool raises 404 → tool_error mode
- Tool raises 503 transport → tool_error mode
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import chat_pg_actions
from app.api.chat_pg import ChatRequest
from app.domains.aviation.api_client import (
    AirlineApiClient,
    AirlineApiError,
    AirlineApiTransportError,
)
from app.domains.aviation.plugin import AviationDomain
from tools.aviation_mock.app import app as mock_app


# ---- fixtures -------------------------------------------------------


@pytest.fixture
def live_aviation_domain(monkeypatch: pytest.MonkeyPatch) -> AviationDomain:
    """AviationDomain wired to the in-process mock backend, then registered
    in chat_pg_actions' lookup so handle_action picks it up.
    """
    test_http = TestClient(mock_app, base_url="http://mock-airline.test")
    api_client = AirlineApiClient(
        base_url="http://mock-airline.test",
        service_token="test-token-123",
        http_client=test_http,
        max_retries=0,
    )
    domain = AviationDomain(api_client=api_client)
    monkeypatch.setattr(
        chat_pg_actions, "get_domain_for_agent",
        lambda agent_type: domain if agent_type == "aviation" else None,
    )
    return domain


@pytest.fixture
def fake_db() -> MagicMock:
    db = MagicMock()
    db.add.return_value = None
    db.commit.return_value = None
    return db


@pytest.fixture
def conv() -> SimpleNamespace:
    """Conversation row stub. handle_action only reads .title and assigns
    it; doesn't query for it.
    """
    return SimpleNamespace(id="conv-1", title="New chat")


def _request(action: str, **action_params) -> ChatRequest:
    return ChatRequest(
        conversation_id="conv-1",
        agent_type="aviation",
        action=action,
        action_params=dict(action_params),
    )


# ---- ChatRequest validator -----------------------------------------


def test_request_rejects_both_question_and_action():
    with pytest.raises(ValidationError):
        ChatRequest(
            conversation_id="c", agent_type="aviation",
            question="hi", action="retrieve_booking",
        )


def test_request_rejects_neither_question_nor_action():
    with pytest.raises(ValidationError):
        ChatRequest(conversation_id="c", agent_type="aviation")


def test_request_accepts_question_only():
    req = ChatRequest(
        conversation_id="c", agent_type="aviation",
        question="how long to refund?",
    )
    assert req.action is None
    assert req.action_params == {}


def test_request_accepts_action_only():
    req = ChatRequest(
        conversation_id="c", agent_type="aviation",
        action="retrieve_booking",
    )
    assert req.question is None


# ---- empty / partial / full param walks ----------------------------


def test_empty_params_prompts_for_booking_reference(
    live_aviation_domain: AviationDomain,
    fake_db: MagicMock,
    conv: SimpleNamespace,
):
    req = _request("retrieve_booking")
    out = chat_pg_actions.handle_action(req, "tenant", "user", conv, fake_db)

    assert out["mode"] == "action_collecting"
    assert out["missing_param"] == "booking_reference"
    assert "PNR" in out["answer"]
    assert out["action_state"]["action"] == "retrieve_booking"
    assert out["action_state"]["collected"] == {}
    assert out["action_state"]["complete"] is False
    assert "prompt" not in out["param_schema"]  # internal key stripped
    fake_db.add.assert_called_once()  # the assistant prompt is persisted


def test_pnr_present_prompts_for_last_name(
    live_aviation_domain: AviationDomain,
    fake_db: MagicMock,
    conv: SimpleNamespace,
):
    req = _request("retrieve_booking", booking_reference="ABC123")
    out = chat_pg_actions.handle_action(req, "tenant", "user", conv, fake_db)

    assert out["mode"] == "action_collecting"
    assert out["missing_param"] == "last_name"
    assert "last name" in out["answer"].lower()
    assert out["action_state"]["collected"] == {"booking_reference": "ABC123"}


def test_all_params_valid_dispatches_and_returns_tool_executed(
    live_aviation_domain: AviationDomain,
    fake_db: MagicMock,
    conv: SimpleNamespace,
):
    req = _request("retrieve_booking", booking_reference="ABC123", last_name="DOE")
    out = chat_pg_actions.handle_action(req, "tenant", "user", conv, fake_db)

    assert out["mode"] == "tool_executed"
    assert out["source_type"] == "tool"
    assert out["render_as"] == "booking_card"
    assert out["tool_name"] == "retrieve_booking"
    assert out["tool_result"]["booking_reference"] == "ABC123"
    assert out["tool_result"]["status"] == "CONFIRMED"
    assert out["action_state"]["complete"] is True
    # Conv title got auto-populated from "New chat" → "Retrieve Booking"
    assert conv.title == "Retrieve Booking"


# ---- smart validation ----------------------------------------------


def test_smart_validation_extracts_pnr_from_messy_input(
    live_aviation_domain: AviationDomain,
    fake_db: MagicMock,
    conv: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    """User typed 'my pnr is ABC123' instead of just 'ABC123'.
    extract_param should pull 'ABC123' out and the flow continues."""
    # The booking_reference value is technically valid against the schema
    # ("my pnr is ABC123" satisfies type=string, minLength=1, maxLength=20)
    # so we have to give it something that DOES fail validation. Use the
    # max-length boundary.
    too_long = "X" * 50
    monkeypatch.setattr(
        chat_pg_actions, "extract_param",
        lambda text, name, schema: "CLEAN1" if name == "booking_reference" else None,
    )
    req = _request("retrieve_booking", booking_reference=too_long, last_name="DOE")
    out = chat_pg_actions.handle_action(req, "tenant", "user", conv, fake_db)

    # Smart validation cleaned the PNR; flow proceeded; mock returned 404
    # because CLEAN1 isn't a seed booking → tool_error.
    assert out["mode"] == "tool_error"
    assert out["error_code"] == "BOOKING_NOT_FOUND"


def test_smart_validation_failure_re_prompts_with_hint(
    live_aviation_domain: AviationDomain,
    fake_db: MagicMock,
    conv: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    """Garbage input — extract_param returns None → re-prompt with hint."""
    monkeypatch.setattr(
        chat_pg_actions, "extract_param", lambda text, name, schema: None
    )
    req = _request("retrieve_booking", booking_reference="X" * 50, last_name="DOE")
    out = chat_pg_actions.handle_action(req, "tenant", "user", conv, fake_db)

    assert out["mode"] == "action_collecting"
    assert out["missing_param"] == "booking_reference"
    assert "doesn't look like" in out["answer"]


# ---- error paths --------------------------------------------------


def test_unknown_action_returns_422(
    live_aviation_domain: AviationDomain,
    fake_db: MagicMock,
    conv: SimpleNamespace,
):
    req = _request("nonexistent_tool")
    with pytest.raises(HTTPException) as exc:
        chat_pg_actions.handle_action(req, "tenant", "user", conv, fake_db)
    assert exc.value.status_code == 422
    assert "Unknown action" in str(exc.value.detail)


def test_unknown_agent_type_returns_422(
    fake_db: MagicMock,
    conv: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        chat_pg_actions, "get_domain_for_agent", lambda agent_type: None
    )
    req = ChatRequest(
        conversation_id="c", agent_type="fictional",
        action="retrieve_booking",
    )
    with pytest.raises(HTTPException) as exc:
        chat_pg_actions.handle_action(req, "tenant", "user", conv, fake_db)
    assert exc.value.status_code == 422
    assert "No domain plugin" in str(exc.value.detail)


def test_tool_raises_404_returns_tool_error(
    live_aviation_domain: AviationDomain,
    fake_db: MagicMock,
    conv: SimpleNamespace,
):
    """Unknown PNR (mock returns 404) → tool_error mode with friendly text."""
    req = _request("retrieve_booking", booking_reference="ZZZ999", last_name="DOE")
    out = chat_pg_actions.handle_action(req, "tenant", "user", conv, fake_db)

    assert out["mode"] == "tool_error"
    assert out["error_code"] == "BOOKING_NOT_FOUND"
    assert out["error_status"] == 404
    assert "couldn't find" in out["answer"].lower()


def test_tool_raises_403_returns_friendly_verification_message(
    live_aviation_domain: AviationDomain,
    fake_db: MagicMock,
    conv: SimpleNamespace,
):
    req = _request("retrieve_booking", booking_reference="ABC123", last_name="WRONG")
    out = chat_pg_actions.handle_action(req, "tenant", "user", conv, fake_db)

    assert out["mode"] == "tool_error"
    assert out["error_status"] == 403
    assert "verify" in out["answer"].lower()


def test_transport_error_returns_503_friendly(
    live_aviation_domain: AviationDomain,
    fake_db: MagicMock,
    conv: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    """Simulate the airline API being unreachable."""

    def boom(_tool, _args):
        raise AirlineApiTransportError("connection refused")

    monkeypatch.setattr(live_aviation_domain, "dispatch_tool", boom)

    req = _request("retrieve_booking", booking_reference="ABC123", last_name="DOE")
    out = chat_pg_actions.handle_action(req, "tenant", "user", conv, fake_db)

    assert out["mode"] == "tool_error"
    assert out["error_code"] == "DEPENDENCY_UNAVAILABLE"
    assert "temporarily unavailable" in out["answer"]


# ---- summary message + DB writes ----------------------------------


def test_assistant_prompt_is_persisted(
    live_aviation_domain: AviationDomain,
    fake_db: MagicMock,
    conv: SimpleNamespace,
):
    req = _request("retrieve_booking")
    chat_pg_actions.handle_action(req, "tenant", "user", conv, fake_db)
    assert fake_db.add.call_count == 1
    msg = fake_db.add.call_args[0][0]
    assert msg.role == "assistant"
    assert "PNR" in msg.content


def test_tool_executed_persists_summary_message(
    live_aviation_domain: AviationDomain,
    fake_db: MagicMock,
    conv: SimpleNamespace,
):
    req = _request("retrieve_booking", booking_reference="ABC123", last_name="DOE")
    chat_pg_actions.handle_action(req, "tenant", "user", conv, fake_db)
    assert fake_db.add.call_count == 1
    msg = fake_db.add.call_args[0][0]
    assert msg.role == "assistant"
    assert "ABC123" in msg.content
