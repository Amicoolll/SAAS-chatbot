"""Slice 6 — web_checkin end-to-end (first WRITE workflow).

The "frontend chains it" pattern:
  1. Frontend calls retrieve_booking via the existing chip flow
  2. Frontend renders a check-in widget on top of the booking_card
  3. User picks passengers/segments + accepts terms + clicks Submit
  4. Frontend calls action="web_checkin" with all required fields
     pre-filled in action_params (including the idempotency_key it
     generated as a UUID)
  5. Backend dispatches POST /v1/checkin with Idempotency-Key header
     and returns the seat assignments + boarding pass barcodes

The chip flow itself is barely exercised conversationally — the
web_checkin schema's per-field prompts only ever fire as a
developer-detectable error if the frontend forgets to pre-fill a field.
"""

from __future__ import annotations

import uuid
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
    CheckedInPassenger,
    CheckinRequest,
    CheckinResponse,
)
from app.domains.aviation.plugin import AviationDomain
from tools.aviation_mock.app import app as mock_app
from tools.aviation_mock.seed_data import _reset_checkin_idempotency_cache


BEARER = {"Authorization": "Bearer test-token-123"}


@pytest.fixture(autouse=True)
def _clean_idempotency_cache():
    """Clear the mock's idempotency cache between tests so each test
    starts with a clean slate."""
    _reset_checkin_idempotency_cache()
    yield
    _reset_checkin_idempotency_cache()


# ---- model validation ----------------------------------------------


def test_request_rejects_empty_passenger_ids():
    with pytest.raises(ValidationError):
        CheckinRequest(
            booking_reference="ABC123", last_name="DOE",
            passenger_ids=[], segment_ids=["s1"], accept_terms=True,
        )


def test_request_rejects_empty_segment_ids():
    with pytest.raises(ValidationError):
        CheckinRequest(
            booking_reference="ABC123", last_name="DOE",
            passenger_ids=["p1"], segment_ids=[], accept_terms=True,
        )


def test_request_accept_terms_must_be_supplied():
    with pytest.raises(ValidationError):
        CheckinRequest(
            booking_reference="ABC123", last_name="DOE",
            passenger_ids=["p1"], segment_ids=["s1"],
            # accept_terms missing
        )


def test_response_round_trip_canonical_payload():
    payload = {
        "checkin_id": "ci_abc123_2",
        "checked_in": [
            {
                "passenger_id": "p1",
                "segment_id": "s1",
                "seat": "12A",
                "boarding_pass_url": "/v1/bookings/ABC123/boarding-pass?passenger_id=p1&segment_id=s1",
                "boarding_pass": {
                    "barcode": "M1DOE/JOHN          EABC123 DELBOMAI 0101 152Y012A0000 100",
                    "seat": "12A",
                    "boarding_group": "1",
                    "boarding_time": "2026-06-01T07:30:00+05:30",
                    "gate": "B12",
                },
            }
        ],
        "segment_status": "CHECKED_IN",
        "warnings": [],
    }
    resp = CheckinResponse.model_validate(payload)
    assert resp.checkin_id == "ci_abc123_2"
    assert resp.segment_status == "CHECKED_IN"
    assert resp.checked_in[0].seat == "12A"
    assert resp.checked_in[0].boarding_pass.gate == "B12"


# ---- mock backend --------------------------------------------------


def _client() -> TestClient:
    return TestClient(mock_app)


def _checkin_body(
    *,
    pnr: str = "ABC123",
    last_name: str = "DOE",
    passengers: list[str] = None,
    segments: list[str] = None,
    accept_terms: bool = True,
) -> dict:
    return {
        "booking_reference": pnr,
        "last_name": last_name,
        "passenger_ids": passengers or ["p1", "p2"],
        "segment_ids": segments or ["s1"],
        "accept_terms": accept_terms,
    }


def test_mock_happy_path_returns_seats_and_barcode():
    r = _client().post(
        "/v1/checkin",
        headers={**BEARER, "Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"},
        json=_checkin_body(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["segment_status"] == "CHECKED_IN"
    assert len(body["checked_in"]) == 2
    assert body["checked_in"][0]["seat"] == "12A"
    assert body["checked_in"][1]["seat"] == "12B"
    assert body["checked_in"][0]["boarding_pass"]["barcode"].startswith("M1DOE")


def test_mock_idempotency_replay_returns_cached_response():
    """Same key → cached body. Different key → fresh body."""
    key = str(uuid.uuid4())
    r1 = _client().post(
        "/v1/checkin",
        headers={**BEARER, "Idempotency-Key": key, "Content-Type": "application/json"},
        json=_checkin_body(),
    )
    assert r1.status_code == 200
    first_checkin_id = r1.json()["checkin_id"]

    # Replay with same key → should get cached body (no re-execution)
    r2 = _client().post(
        "/v1/checkin",
        headers={**BEARER, "Idempotency-Key": key, "Content-Type": "application/json"},
        json=_checkin_body(passengers=["p1"]),  # different body — should be IGNORED
    )
    assert r2.status_code == 200
    assert r2.json()["checkin_id"] == first_checkin_id
    assert len(r2.json()["checked_in"]) == 2  # original 2-pax response, not new 1-pax

    # Different key with new body → fresh response
    r3 = _client().post(
        "/v1/checkin",
        headers={**BEARER, "Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"},
        json=_checkin_body(passengers=["p1"]),
    )
    assert r3.status_code == 200
    assert len(r3.json()["checked_in"]) == 1


def test_mock_missing_idempotency_key_returns_422():
    r = _client().post(
        "/v1/checkin",
        headers={**BEARER, "Content-Type": "application/json"},
        json=_checkin_body(),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_mock_accept_terms_false_returns_422():
    r = _client().post(
        "/v1/checkin",
        headers={**BEARER, "Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"},
        json=_checkin_body(accept_terms=False),
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "ACCEPT_TERMS_REQUIRED"


def test_mock_unknown_pnr_returns_404():
    r = _client().post(
        "/v1/checkin",
        headers={**BEARER, "Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"},
        json=_checkin_body(pnr="ZZZ999"),
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "BOOKING_NOT_FOUND"


def test_mock_wrong_last_name_returns_403():
    r = _client().post(
        "/v1/checkin",
        headers={**BEARER, "Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"},
        json=_checkin_body(last_name="WRONG"),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "BOOKING_VERIFICATION_FAILED"


def test_mock_checkin_not_open_returns_409_with_opens_at():
    """XYZ789 is seeded with CHECKIN_NOT_OPEN behaviour."""
    r = _client().post(
        "/v1/checkin",
        headers={**BEARER, "Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"},
        json=_checkin_body(pnr="XYZ789", last_name="SMITH", passengers=["p1"]),
    )
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "CHECKIN_NOT_OPEN"
    assert "opens_at" in err["details"]


def test_mock_missing_bearer_returns_401():
    r = _client().post(
        "/v1/checkin",
        headers={"Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"},
        json=_checkin_body(),
    )
    assert r.status_code == 401


# ---- API client end-to-end against mock ---------------------------


def _api_client() -> AirlineApiClient:
    test_http = TestClient(mock_app, base_url="http://mock-airline.test")
    return AirlineApiClient(
        base_url="http://mock-airline.test",
        service_token="test-token-123",
        http_client=test_http,
        max_retries=0,
    )


def test_client_checkin_happy_path_sends_idempotency_header():
    client = _api_client()
    resp = client.checkin(
        CheckinRequest(
            booking_reference="ABC123", last_name="DOE",
            passenger_ids=["p1", "p2"], segment_ids=["s1"], accept_terms=True,
        ),
        idempotency_key=str(uuid.uuid4()),
        request_id="req-1", trace_id="trace-1",
    )
    assert isinstance(resp, CheckinResponse)
    assert resp.segment_status == "CHECKED_IN"
    assert len(resp.checked_in) == 2


def test_client_checkin_propagates_409_as_apierror():
    client = _api_client()
    with pytest.raises(AirlineApiError) as exc_info:
        client.checkin(
            CheckinRequest(
                booking_reference="XYZ789", last_name="SMITH",
                passenger_ids=["p1"], segment_ids=["s1"], accept_terms=True,
            ),
            idempotency_key=str(uuid.uuid4()),
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "CHECKIN_NOT_OPEN"


def test_client_checkin_requires_idempotency_key():
    client = _api_client()
    with pytest.raises(ValueError, match="idempotency_key"):
        client.checkin(
            CheckinRequest(
                booking_reference="ABC123", last_name="DOE",
                passenger_ids=["p1"], segment_ids=["s1"], accept_terms=True,
            ),
            idempotency_key="",
        )


# ---- AviationDomain.dispatch_tool ---------------------------------


def test_plugin_dispatch_returns_dict():
    domain = AviationDomain(api_client=_api_client())
    result = domain.dispatch_tool(
        "web_checkin",
        {
            "booking_reference": "ABC123",
            "last_name": "DOE",
            "passenger_ids": ["p1", "p2"],
            "segment_ids": ["s1"],
            "accept_terms": True,
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert isinstance(result, dict)
    assert result["segment_status"] == "CHECKED_IN"


def test_plugin_dispatch_idempotency_key_separated_from_body():
    """idempotency_key is a transport concern — pulled out of args before
    the CheckinRequest body is constructed. CheckinRequest doesn't have
    such a field, so passing it through would raise ValidationError."""
    domain = AviationDomain(api_client=_api_client())
    # Should not raise — idempotency_key is consumed before model_validate.
    domain.dispatch_tool(
        "web_checkin",
        {
            "booking_reference": "ABC123",
            "last_name": "DOE",
            "passenger_ids": ["p1"],
            "segment_ids": ["s1"],
            "accept_terms": True,
            "idempotency_key": "key-1",
        },
    )


def test_plugin_dispatch_missing_idempotency_key_raises_value_error():
    domain = AviationDomain(api_client=_api_client())
    with pytest.raises(ValueError, match="idempotency_key"):
        domain.dispatch_tool(
            "web_checkin",
            {
                "booking_reference": "ABC123",
                "last_name": "DOE",
                "passenger_ids": ["p1"],
                "segment_ids": ["s1"],
                "accept_terms": True,
            },
        )


# ---- chip-flow integration ----------------------------------------


def _patch_domain(monkeypatch: pytest.MonkeyPatch) -> AviationDomain:
    domain = AviationDomain(api_client=_api_client())
    monkeypatch.setattr(
        chat_pg_actions, "get_domain_for_agent",
        lambda agent_type: domain if agent_type == "aviation" else None,
    )
    return domain


def _handle(req: ChatRequest):
    return chat_pg_actions.handle_action(
        req, "tenant", "user",
        SimpleNamespace(id="conv-1", title="New chat"),
        MagicMock(),
    )


def test_chip_flow_full_pre_fill_dispatches_and_returns_checkin_card(
    monkeypatch: pytest.MonkeyPatch,
):
    """Standard happy path: frontend pre-fills every field (including
    idempotency_key generated as a UUID) → tool_executed."""
    _patch_domain(monkeypatch)

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="web_checkin",
        action_params={
            "booking_reference": "ABC123",
            "last_name": "DOE",
            "passenger_ids": ["p1", "p2"],
            "segment_ids": ["s1"],
            "accept_terms": True,
            "idempotency_key": str(uuid.uuid4()),
        },
    ))
    assert out["mode"] == "tool_executed", out
    assert out["render_as"] == "checkin_card"
    assert out["tool_name"] == "web_checkin"
    assert out["tool_result"]["segment_status"] == "CHECKED_IN"
    assert len(out["tool_result"]["checked_in"]) == 2
    # Summary mentions seats
    assert "12A" in out["answer"] and "12B" in out["answer"]


def test_chip_flow_idempotency_replay_returns_same_checkin_id(
    monkeypatch: pytest.MonkeyPatch,
):
    """Two dispatches with the same idempotency_key → same checkin_id
    (mock cache returns cached body). The chatbot doesn't have to know
    about idempotency — the airline backend handles it."""
    _patch_domain(monkeypatch)
    key = str(uuid.uuid4())

    def submit():
        return _handle(ChatRequest(
            conversation_id="conv-1", agent_type="aviation",
            action="web_checkin",
            action_params={
                "booking_reference": "ABC123",
                "last_name": "DOE",
                "passenger_ids": ["p1", "p2"],
                "segment_ids": ["s1"],
                "accept_terms": True,
                "idempotency_key": key,
            },
        ))

    out1 = submit()
    out2 = submit()
    assert out1["tool_result"]["checkin_id"] == out2["tool_result"]["checkin_id"]


def test_chip_flow_409_not_open_returns_action_specific_copy(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression: the 409 error message must use web-checkin-specific
    copy ("Check-in isn't available right now…"), NOT the generic 503
    "upstream system unavailable" or any other tool's phrasing."""
    _patch_domain(monkeypatch)

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="web_checkin",
        action_params={
            "booking_reference": "XYZ789",
            "last_name": "SMITH",
            "passenger_ids": ["p1"],
            "segment_ids": ["s1"],
            "accept_terms": True,
            "idempotency_key": str(uuid.uuid4()),
        },
    ))
    assert out["mode"] == "tool_error"
    assert out["error_code"] == "CHECKIN_NOT_OPEN"
    assert out["error_status"] == 409
    answer_lower = out["answer"].lower()
    assert "check-in" in answer_lower
    assert "available" in answer_lower
    # Don't borrow other tools' copy
    assert "booking system" not in answer_lower
    assert "flight" not in answer_lower


def test_chip_flow_missing_accept_terms_asks_for_it(
    monkeypatch: pytest.MonkeyPatch,
):
    """If the frontend forgets accept_terms, backend asks per the
    schema's per-field prompt. (User-friendly fallback even though
    accept_terms should be set by the widget's checkbox.)"""
    _patch_domain(monkeypatch)

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="web_checkin",
        action_params={
            "booking_reference": "ABC123",
            "last_name": "DOE",
            "passenger_ids": ["p1"],
            "segment_ids": ["s1"],
            "idempotency_key": str(uuid.uuid4()),
            # accept_terms missing
        },
    ))
    assert out["mode"] == "action_collecting"
    assert out["missing_param"] == "accept_terms"
    assert "terms" in out["answer"].lower()


def test_chip_flow_missing_idempotency_key_asks_for_it(
    monkeypatch: pytest.MonkeyPatch,
):
    """If the frontend forgets idempotency_key, backend asks per the
    schema's per-field prompt. The prompt deliberately contains
    '[Frontend bug ...]' so the developer notices on the first manual
    test."""
    _patch_domain(monkeypatch)

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="web_checkin",
        action_params={
            "booking_reference": "ABC123",
            "last_name": "DOE",
            "passenger_ids": ["p1"],
            "segment_ids": ["s1"],
            "accept_terms": True,
            # idempotency_key missing
        },
    ))
    assert out["mode"] == "action_collecting"
    assert out["missing_param"] == "idempotency_key"
    assert "[Frontend bug" in out["answer"]
