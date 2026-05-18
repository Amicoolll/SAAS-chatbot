"""Slice 7 — boarding_pass end-to-end.

Read-only retrieval of a previously-issued boarding pass. Pre-condition:
the passenger must already be checked in for that segment (via
web_checkin or pre-seeded). 409 NOT_CHECKED_IN otherwise.

Two state pathways verified:
  1. Default seed (ABC123 / p1, p2 on s1) — works fresh, no check-in
     needed.
  2. Post-checkin: a passenger that wasn't seeded becomes retrievable
     after web_checkin succeeds (seed_data.checkin_mock writes into the
     boarding-pass cache).
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
    BoardingPassRequest,
    BoardingPassResponse,
)
from app.domains.aviation.plugin import AviationDomain
from tools.aviation_mock.app import app as mock_app
from tools.aviation_mock.seed_data import (
    _reset_boarding_passes,
    _reset_checkin_idempotency_cache,
)


BEARER = {"Authorization": "Bearer test-token-123"}


@pytest.fixture(autouse=True)
def _clean_state():
    """Restore both the boarding-pass cache and the checkin idempotency
    cache to defaults between tests so each test starts fresh."""
    _reset_boarding_passes()
    _reset_checkin_idempotency_cache()
    yield
    _reset_boarding_passes()
    _reset_checkin_idempotency_cache()


# ---- model validation ----------------------------------------------


def test_request_minimal_defaults_to_json_format():
    r = BoardingPassRequest(
        booking_reference="ABC123", last_name="DOE",
        passenger_id="p1", segment_id="s1",
    )
    assert r.format == "json"


def test_request_rejects_invalid_format_enum():
    with pytest.raises(ValidationError):
        BoardingPassRequest(
            booking_reference="ABC123", last_name="DOE",
            passenger_id="p1", segment_id="s1",
            format="csv",
        )


def test_request_accepts_all_format_enum_values():
    for fmt in ("json", "pdf", "wallet_apple", "wallet_google"):
        r = BoardingPassRequest(
            booking_reference="ABC123", last_name="DOE",
            passenger_id="p1", segment_id="s1",
            format=fmt,
        )
        assert r.format == fmt


def test_response_round_trip_canonical_payload():
    payload = {
        "passenger": {"first_name": "JOHN", "last_name": "DOE"},
        "flight_number": "AI101",
        "origin": "DEL",
        "destination": "BOM",
        "scheduled_departure": "2026-06-01T08:00:00+05:30",
        "boarding_time": "2026-06-01T07:30:00+05:30",
        "seat": "12A",
        "boarding_group": "1",
        "sequence_number": 42,
        "gate": "B12",
        "terminal": "T3",
        "barcode_format": "PDF417",
        "barcode_data": "M1DOE/JOHN              EABC123 DELBOMAI 0101 152Y012A0042 100",
    }
    resp = BoardingPassResponse.model_validate(payload)
    assert resp.passenger.first_name == "JOHN"
    assert resp.seat == "12A"
    assert resp.barcode_format == "PDF417"
    assert resp.barcode_data.startswith("M1DOE/JOHN")


# ---- mock backend --------------------------------------------------


def _client() -> TestClient:
    return TestClient(mock_app)


def _get_bp(
    *,
    pnr: str = "ABC123",
    pid: str = "p1",
    sid: str = "s1",
    last_name: str = "DOE",
    fmt: str = "json",
):
    headers = {**BEARER}
    if last_name:
        headers["X-Booking-Verifier-LastName"] = last_name
    return _client().get(
        f"/v1/bookings/{pnr}/boarding-pass",
        params={"passenger_id": pid, "segment_id": sid, "format": fmt},
        headers=headers,
    )


def test_mock_seeded_passenger_returns_boarding_pass():
    r = _get_bp()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seat"] == "12A"
    assert body["passenger"]["first_name"] == "JOHN"
    assert body["barcode_format"] == "PDF417"


def test_mock_seeded_second_passenger_distinct_seat():
    r = _get_bp(pid="p2")
    assert r.status_code == 200
    assert r.json()["seat"] == "12B"


def test_mock_unknown_pnr_returns_404():
    r = _get_bp(pnr="ZZZ999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "BOOKING_NOT_FOUND"


def test_mock_wrong_last_name_returns_403():
    r = _get_bp(last_name="WRONG")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "BOOKING_VERIFICATION_FAILED"


def test_mock_passenger_not_checked_in_returns_409():
    """XYZ789 (Alice Smith) isn't in the default boarding-pass seed —
    she hasn't been checked in. → 409 NOT_CHECKED_IN."""
    r = _get_bp(pnr="XYZ789", last_name="SMITH", pid="p1", sid="s1")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "NOT_CHECKED_IN"
    assert err["details"]["passenger_id"] == "p1"


def test_mock_pdf_format_returns_501_not_implemented():
    r = _get_bp(fmt="pdf")
    assert r.status_code == 501
    err = r.json()["error"]
    assert err["code"] == "FORMAT_NOT_IMPLEMENTED"
    assert "json" in err["details"]["supported"]


def test_mock_missing_bearer_returns_401():
    r = _client().get(
        "/v1/bookings/ABC123/boarding-pass",
        params={"passenger_id": "p1", "segment_id": "s1"},
        headers={"X-Booking-Verifier-LastName": "DOE"},
    )
    assert r.status_code == 401


def test_mock_freshly_checked_in_passenger_becomes_retrievable():
    """End-to-end state-sharing: web_checkin a passenger that wasn't
    seeded → boarding_pass for that passenger now succeeds.

    Uses the same TestClient so the in-process state dict is shared.
    """
    client = _client()

    # Step 1: ABC123 has p1, p2 seeded — but for this test we use
    # the actual wire path: fresh check-in writes into the BP cache.
    # Simulate by checking in a passenger ID that ISN'T seeded.
    # ...we can't add new seed PNRs at runtime cleanly, so verify the
    # write-back path by checking that a fresh check-in updates seat
    # values: re-checkin would re-issue via _build_checkin_success.

    # Reset the boarding pass cache to ensure ABC123 / p1 / s1 is gone.
    from tools.aviation_mock.seed_data import _BOARDING_PASSES
    _BOARDING_PASSES.pop(("ABC123", "p1", "s1"), None)
    _BOARDING_PASSES.pop(("ABC123", "p2", "s1"), None)

    # Confirm GET fails first (NOT_CHECKED_IN)
    r1 = client.get(
        "/v1/bookings/ABC123/boarding-pass",
        params={"passenger_id": "p1", "segment_id": "s1"},
        headers={**BEARER, "X-Booking-Verifier-LastName": "DOE"},
    )
    assert r1.status_code == 409

    # Now check in via the API
    r2 = client.post(
        "/v1/checkin",
        headers={**BEARER, "Idempotency-Key": str(uuid.uuid4()), "Content-Type": "application/json"},
        json={
            "booking_reference": "ABC123", "last_name": "DOE",
            "passenger_ids": ["p1"], "segment_ids": ["s1"], "accept_terms": True,
        },
    )
    assert r2.status_code == 200

    # Now GET should succeed
    r3 = client.get(
        "/v1/bookings/ABC123/boarding-pass",
        params={"passenger_id": "p1", "segment_id": "s1"},
        headers={**BEARER, "X-Booking-Verifier-LastName": "DOE"},
    )
    assert r3.status_code == 200
    assert r3.json()["passenger"]["first_name"] == "JOHN"


# ---- API client end-to-end against mock ---------------------------


def _api_client() -> AirlineApiClient:
    test_http = TestClient(mock_app, base_url="http://mock-airline.test")
    return AirlineApiClient(
        base_url="http://mock-airline.test",
        service_token="test-token-123",
        http_client=test_http,
        max_retries=0,
    )


def test_client_get_boarding_pass_happy_path():
    client = _api_client()
    resp = client.get_boarding_pass(
        BoardingPassRequest(
            booking_reference="ABC123", last_name="DOE",
            passenger_id="p1", segment_id="s1",
        ),
        request_id="req-1", trace_id="trace-1",
    )
    assert isinstance(resp, BoardingPassResponse)
    assert resp.seat == "12A"
    assert resp.passenger.first_name == "JOHN"


def test_client_propagates_409_not_checked_in():
    client = _api_client()
    with pytest.raises(AirlineApiError) as exc_info:
        client.get_boarding_pass(
            BoardingPassRequest(
                booking_reference="XYZ789", last_name="SMITH",
                passenger_id="p1", segment_id="s1",
            ),
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "NOT_CHECKED_IN"


def test_client_sends_verifier_header():
    """Verify X-Booking-Verifier-LastName is sent (not in body)."""
    captured: dict[str, str] = {}
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update({k: v for k, v in request.headers.items()})
        return httpx.Response(
            200,
            json={
                "passenger": {"first_name": "X", "last_name": "Y"},
                "flight_number": "AI1", "origin": "DEL", "destination": "BOM",
                "scheduled_departure": "2026-06-01T08:00:00+05:30",
                "boarding_time": "2026-06-01T07:30:00+05:30",
                "seat": "1A", "boarding_group": "1", "sequence_number": 1,
                "barcode_format": "PDF417", "barcode_data": "M1...",
            },
        )

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://airline.example",
        timeout=5.0,
    )
    client = AirlineApiClient(
        base_url="http://airline.example", service_token="abc",
        http_client=http, max_retries=0,
    )
    client.get_boarding_pass(
        BoardingPassRequest(
            booking_reference="ABC123", last_name="DOE",
            passenger_id="p1", segment_id="s1",
        ),
    )
    assert captured.get("x-booking-verifier-lastname") == "DOE"


# ---- AviationDomain.dispatch_tool ---------------------------------


def test_plugin_dispatch_returns_dict():
    domain = AviationDomain(api_client=_api_client())
    result = domain.dispatch_tool(
        "boarding_pass",
        {
            "booking_reference": "ABC123", "last_name": "DOE",
            "passenger_id": "p1", "segment_id": "s1",
        },
    )
    assert isinstance(result, dict)
    assert result["seat"] == "12A"


def test_plugin_dispatch_invalid_args_raises():
    domain = AviationDomain(api_client=_api_client())
    with pytest.raises(ValidationError):
        domain.dispatch_tool(
            "boarding_pass",
            {"booking_reference": "ABC123", "last_name": "DOE"},
            # missing passenger_id, segment_id
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


def test_chip_flow_full_pre_fill_dispatches_and_returns_boarding_pass_card(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_domain(monkeypatch)

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="boarding_pass",
        action_params={
            "booking_reference": "ABC123",
            "last_name": "DOE",
            "passenger_id": "p1",
            "segment_id": "s1",
        },
    ))
    assert out["mode"] == "tool_executed", out
    assert out["render_as"] == "boarding_pass_card"
    assert out["tool_result"]["seat"] == "12A"
    # Summary mentions name + flight + seat
    assert "JOHN" in out["answer"]
    assert "AI101" in out["answer"]
    assert "12A" in out["answer"]


def test_chip_flow_not_checked_in_returns_409_specific_copy(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression: 409 NOT_CHECKED_IN must use boarding-pass-specific
    copy ("not checked in yet... complete web check-in first"), NOT
    borrowing flight or booking phrasing."""
    _patch_domain(monkeypatch)

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="boarding_pass",
        action_params={
            "booking_reference": "XYZ789",
            "last_name": "SMITH",
            "passenger_id": "p1",
            "segment_id": "s1",
        },
    ))
    assert out["mode"] == "tool_error"
    assert out["error_code"] == "NOT_CHECKED_IN"
    assert out["error_status"] == 409
    answer_lower = out["answer"].lower()
    assert "checked in" in answer_lower
    assert "check-in" in answer_lower
    # Don't borrow other tools' copy
    assert "flight number" not in answer_lower
    assert "route" not in answer_lower


def test_chip_flow_missing_passenger_id_asks_for_it(
    monkeypatch: pytest.MonkeyPatch,
):
    """Frontend bug surface — if passenger_id is missing, backend's
    per-field prompt contains the literal '[Frontend bug ...]' marker."""
    _patch_domain(monkeypatch)

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="boarding_pass",
        action_params={
            "booking_reference": "ABC123",
            "last_name": "DOE",
            "segment_id": "s1",
            # passenger_id missing
        },
    ))
    assert out["mode"] == "action_collecting"
    assert out["missing_param"] == "passenger_id"
    assert "[Frontend bug" in out["answer"]


def test_chip_flow_post_checkin_can_retrieve_boarding_pass(
    monkeypatch: pytest.MonkeyPatch,
):
    """End-to-end through chat: web_checkin a passenger that we evict
    from the BP cache first → boarding_pass call now succeeds."""
    _patch_domain(monkeypatch)

    # Evict ABC123/p1/s1 from the boarding-pass cache so it must come
    # from a fresh check-in.
    from tools.aviation_mock.seed_data import _BOARDING_PASSES
    _BOARDING_PASSES.pop(("ABC123", "p1", "s1"), None)
    _BOARDING_PASSES.pop(("ABC123", "p2", "s1"), None)

    # First: try to fetch boarding pass — should fail
    out_fail = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="boarding_pass",
        action_params={
            "booking_reference": "ABC123", "last_name": "DOE",
            "passenger_id": "p1", "segment_id": "s1",
        },
    ))
    assert out_fail["mode"] == "tool_error"
    assert out_fail["error_code"] == "NOT_CHECKED_IN"

    # Now do web_checkin
    out_checkin = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="web_checkin",
        action_params={
            "booking_reference": "ABC123", "last_name": "DOE",
            "passenger_ids": ["p1"], "segment_ids": ["s1"],
            "accept_terms": True, "idempotency_key": str(uuid.uuid4()),
        },
    ))
    assert out_checkin["mode"] == "tool_executed"

    # Now boarding_pass succeeds
    out_ok = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="boarding_pass",
        action_params={
            "booking_reference": "ABC123", "last_name": "DOE",
            "passenger_id": "p1", "segment_id": "s1",
        },
    ))
    assert out_ok["mode"] == "tool_executed", out_ok
    assert out_ok["tool_result"]["passenger"]["first_name"] == "JOHN"
