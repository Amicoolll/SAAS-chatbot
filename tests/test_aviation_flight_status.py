"""Slice 2 — flight_status end-to-end.

Same shape as the slice 1 tests (models / mock / api_client / plugin /
chip-flow integration), one tool: ``flight_status`` (GET /v1/flights/status).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import chat_pg_actions
from app.api.chat_pg import ChatRequest
from app.domains.aviation.api_client import (
    AirlineApiClient,
    AirlineApiError,
)
from app.domains.aviation.models import (
    FlightStatusRequest,
    FlightStatusResponse,
)
from app.domains.aviation.plugin import AviationDomain
from tools.aviation_mock.app import app as mock_app


BEARER = {"Authorization": "Bearer test-token-123"}


# ---- model validation ----------------------------------------------


def test_request_rejects_lowercase_flight_number():
    with pytest.raises(ValidationError):
        FlightStatusRequest(flight_number="ai101", date="2026-06-01")


def test_request_rejects_too_short_flight_number():
    with pytest.raises(ValidationError):
        FlightStatusRequest(flight_number="A", date="2026-06-01")


def test_request_rejects_bad_date_format():
    with pytest.raises(ValidationError):
        FlightStatusRequest(flight_number="AI101", date="06/01/2026")
    with pytest.raises(ValidationError):
        FlightStatusRequest(flight_number="AI101", date="2026-6-1")


def test_response_round_trip_canonical_payload():
    payload = {
        "flight_number": "AI101",
        "date": "2026-06-01",
        "origin": "DEL",
        "destination": "BOM",
        "scheduled_departure": "2026-06-01T08:00:00+05:30",
        "scheduled_arrival": "2026-06-01T10:00:00+05:30",
        "estimated_departure": "2026-06-01T08:25:00+05:30",
        "estimated_arrival": "2026-06-01T10:30:00+05:30",
        "actual_departure": None,
        "actual_arrival": None,
        "status": "DELAYED",
        "delay_minutes": 25,
        "gate": "B12",
        "terminal": "T3",
        "aircraft_type": "A320",
    }
    resp = FlightStatusResponse.model_validate(payload)
    assert resp.flight_number == "AI101"
    assert resp.status == "DELAYED"
    assert resp.delay_minutes == 25
    assert resp.gate == "B12"
    assert resp.estimated_departure is not None


def test_response_rejects_invalid_status_enum():
    payload = {
        "flight_number": "AI101",
        "date": "2026-06-01",
        "origin": "DEL",
        "destination": "BOM",
        "scheduled_departure": "2026-06-01T08:00:00+05:30",
        "scheduled_arrival": "2026-06-01T10:00:00+05:30",
        "status": "INVENTED_STATUS",
    }
    with pytest.raises(ValidationError):
        FlightStatusResponse.model_validate(payload)


def test_response_rejects_negative_delay():
    payload = {
        "flight_number": "AI101",
        "date": "2026-06-01",
        "origin": "DEL",
        "destination": "BOM",
        "scheduled_departure": "2026-06-01T08:00:00+05:30",
        "scheduled_arrival": "2026-06-01T10:00:00+05:30",
        "status": "ON_TIME",
        "delay_minutes": -5,
    }
    with pytest.raises(ValidationError):
        FlightStatusResponse.model_validate(payload)


# ---- mock backend --------------------------------------------------


def test_mock_returns_canned_status_for_known_flight():
    client = TestClient(mock_app)
    r = client.get(
        "/v1/flights/status",
        params={"flight_number": "AI101", "date": "2026-06-01"},
        headers={**BEARER, "X-Request-Id": "smoke-1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["flight_number"] == "AI101"
    assert body["status"] == "DELAYED"
    assert body["delay_minutes"] == 25
    assert body["origin"] == "DEL"


def test_mock_lowercase_flight_number_normalized():
    client = TestClient(mock_app)
    r = client.get(
        "/v1/flights/status",
        params={"flight_number": "ai101", "date": "2026-06-01"},
        headers=BEARER,
    )
    assert r.status_code == 200
    assert r.json()["flight_number"] == "AI101"


def test_mock_unknown_flight_returns_404_envelope():
    client = TestClient(mock_app)
    r = client.get(
        "/v1/flights/status",
        params={"flight_number": "AI999", "date": "2026-06-01"},
        headers=BEARER,
    )
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "FLIGHT_NOT_FOUND"
    assert body["error"]["details"]["flight_number"] == "AI999"


def test_mock_missing_bearer_returns_401():
    client = TestClient(mock_app)
    r = client.get(
        "/v1/flights/status",
        params={"flight_number": "AI101", "date": "2026-06-01"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_mock_cancelled_flight_has_no_estimated_times():
    """UK801 / 2026-07-04 is seeded as CANCELLED — verify shape."""
    client = TestClient(mock_app)
    r = client.get(
        "/v1/flights/status",
        params={"flight_number": "UK801", "date": "2026-07-04"},
        headers=BEARER,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "CANCELLED"
    assert body["estimated_departure"] is None
    assert body["estimated_arrival"] is None


# ---- API client end-to-end against mock ---------------------------


def _client_against_mock() -> AirlineApiClient:
    test_http = TestClient(mock_app, base_url="http://mock-airline.test")
    return AirlineApiClient(
        base_url="http://mock-airline.test",
        service_token="test-token-123",
        http_client=test_http,
        max_retries=0,
    )


def test_client_get_flight_status_happy_path():
    client = _client_against_mock()
    resp = client.get_flight_status(
        FlightStatusRequest(flight_number="AI101", date="2026-06-01"),
        request_id="req-1",
        trace_id="trace-1",
    )
    assert resp.flight_number == "AI101"
    assert resp.status == "DELAYED"
    assert resp.delay_minutes == 25
    assert resp.gate == "B12"


def test_client_propagates_404_as_apierror():
    client = _client_against_mock()
    with pytest.raises(AirlineApiError) as exc_info:
        client.get_flight_status(
            FlightStatusRequest(flight_number="AI999", date="2026-06-01")
        )
    err = exc_info.value
    assert err.status_code == 404
    assert err.code == "FLIGHT_NOT_FOUND"


# ---- AviationDomain.dispatch_tool ---------------------------------


def test_plugin_dispatch_flight_status_returns_dict(
    monkeypatch: pytest.MonkeyPatch,
):
    api_client = _client_against_mock()
    domain = AviationDomain(api_client=api_client)
    result = domain.dispatch_tool(
        "flight_status",
        {"flight_number": "AI101", "date": "2026-06-01"},
    )
    assert isinstance(result, dict)
    assert result["flight_number"] == "AI101"
    assert result["status"] == "DELAYED"


def test_plugin_dispatch_flight_status_invalid_args_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    api_client = _client_against_mock()
    domain = AviationDomain(api_client=api_client)
    with pytest.raises(ValidationError):
        # missing required date
        domain.dispatch_tool("flight_status", {"flight_number": "AI101"})


# ---- chip-flow integration ----------------------------------------


def test_chip_flow_collects_then_dispatches_flight_status(
    monkeypatch: pytest.MonkeyPatch,
):
    """Chip click → empty params → backend asks for both via intro_prompt
    → frontend submits both via action_params → tool_executed.
    """
    api_client = _client_against_mock()
    domain = AviationDomain(api_client=api_client)
    monkeypatch.setattr(
        chat_pg_actions, "get_domain_for_agent",
        lambda agent_type: domain if agent_type == "aviation" else None,
    )

    fake_db = MagicMock()
    conv = SimpleNamespace(id="conv-1", title="New chat")

    # Turn 1: empty params → intro_prompt
    req1 = ChatRequest(
        conversation_id="conv-1",
        agent_type="aviation",
        action="flight_status",
        action_params={},
    )
    out1 = chat_pg_actions.handle_action(req1, "tenant", "user", conv, fake_db)
    assert out1["mode"] == "action_collecting"
    assert "flight number" in out1["answer"].lower()
    assert "date" in out1["answer"].lower()

    # Turn 2: both fields filled → tool_executed
    req2 = ChatRequest(
        conversation_id="conv-1",
        agent_type="aviation",
        action="flight_status",
        action_params={"flight_number": "AI101", "date": "2026-06-01"},
    )
    out2 = chat_pg_actions.handle_action(req2, "tenant", "user", conv, fake_db)
    assert out2["mode"] == "tool_executed", out2
    assert out2["render_as"] == "flight_status_card"
    assert out2["tool_name"] == "flight_status"
    assert out2["tool_result"]["flight_number"] == "AI101"
    assert out2["tool_result"]["status"] == "DELAYED"
    # Summary message includes flight + status + delay
    assert "AI101" in out2["answer"]
    assert "delayed" in out2["answer"].lower()
    assert "25" in out2["answer"]


def test_chip_flow_partial_params_asks_for_missing_date(
    monkeypatch: pytest.MonkeyPatch,
):
    """User pre-filled flight_number → backend asks for date."""
    api_client = _client_against_mock()
    domain = AviationDomain(api_client=api_client)
    monkeypatch.setattr(
        chat_pg_actions, "get_domain_for_agent",
        lambda agent_type: domain if agent_type == "aviation" else None,
    )

    req = ChatRequest(
        conversation_id="conv-1",
        agent_type="aviation",
        action="flight_status",
        action_params={"flight_number": "AI101"},
    )
    out = chat_pg_actions.handle_action(
        req, "tenant", "user", SimpleNamespace(id="conv-1", title="New chat"),
        MagicMock(),
    )
    assert out["mode"] == "action_collecting"
    assert out["missing_param"] == "date"
    assert "date" in out["answer"].lower()


def test_chip_flow_unknown_flight_returns_tool_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """Both params valid format-wise but the airline has no such flight
    → mock returns 404 → handle_action wraps as tool_error mode with
    flight-specific copy (NOT borrowing the booking-PNR phrasing)."""
    api_client = _client_against_mock()
    domain = AviationDomain(api_client=api_client)
    monkeypatch.setattr(
        chat_pg_actions, "get_domain_for_agent",
        lambda agent_type: domain if agent_type == "aviation" else None,
    )

    req = ChatRequest(
        conversation_id="conv-1",
        agent_type="aviation",
        action="flight_status",
        action_params={"flight_number": "AI999", "date": "2026-06-01"},
    )
    out = chat_pg_actions.handle_action(
        req, "tenant", "user",
        SimpleNamespace(id="conv-1", title="New chat"),
        MagicMock(),
    )
    assert out["mode"] == "tool_error"
    assert out["error_code"] == "FLIGHT_NOT_FOUND"
    assert out["error_status"] == 404
    # Action-specific copy: must mention "flight" and NOT the
    # booking-PNR phrasing that's correct for retrieve_booking only.
    answer_lower = out["answer"].lower()
    assert "flight" in answer_lower, (
        f"flight_status 404 answer should mention 'flight', got: {out['answer']!r}"
    )
    assert "booking" not in answer_lower, (
        f"flight_status 404 answer must not borrow booking phrasing, got: {out['answer']!r}"
    )
    assert "reference" not in answer_lower, (
        f"flight_status 404 answer must not say 'reference', got: {out['answer']!r}"
    )
