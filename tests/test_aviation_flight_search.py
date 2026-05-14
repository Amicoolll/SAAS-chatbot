"""Slice 3 — flight_search end-to-end (one-way + round-trip).

Covers all use cases the user asked about:
  * Single value pre-filled by chip (origin or destination) → backend
    asks for the missing fields
  * Multi-field extraction from one user message ("DEL to BOM 2026-06-01")
  * Round-trip: outbound × return seeds combined into multiple results
  * Optional fields (return_date, cabin_class) NEVER prompted but
    accepted when the frontend pre-fills them
  * 404 friendly copy for unknown route
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
    FlightSearchRequest,
    FlightSearchResponse,
)
from app.domains.aviation.plugin import AviationDomain
from tools.aviation_mock.app import app as mock_app


BEARER = {"Authorization": "Bearer test-token-123"}


# ---- model validation ----------------------------------------------


def test_request_rejects_non_iata_origin():
    with pytest.raises(ValidationError):
        FlightSearchRequest(
            origin="DELHI", destination="BOM", departure_date="2026-06-01"
        )


def test_request_rejects_lowercase_iata():
    with pytest.raises(ValidationError):
        FlightSearchRequest(
            origin="del", destination="BOM", departure_date="2026-06-01"
        )


def test_request_rejects_bad_date_format():
    with pytest.raises(ValidationError):
        FlightSearchRequest(
            origin="DEL", destination="BOM", departure_date="06/01/2026"
        )


def test_request_accepts_optional_return_date():
    r = FlightSearchRequest(
        origin="DEL", destination="BOM",
        departure_date="2026-06-01", return_date="2026-06-08",
    )
    assert r.return_date == "2026-06-08"


def test_request_rejects_bad_return_date_format():
    with pytest.raises(ValidationError):
        FlightSearchRequest(
            origin="DEL", destination="BOM",
            departure_date="2026-06-01", return_date="6 June",
        )


def test_request_defaults_one_adult_economy_inr():
    r = FlightSearchRequest(
        origin="DEL", destination="BOM", departure_date="2026-06-01",
    )
    assert r.passengers.adults == 1
    assert r.passengers.children == 0
    assert r.cabin_class == "ECONOMY"
    assert r.currency == "INR"
    assert r.return_date is None


def test_request_rejects_bad_passenger_counts():
    with pytest.raises(ValidationError):
        FlightSearchRequest(
            origin="DEL", destination="BOM", departure_date="2026-06-01",
            passengers={"adults": 0, "children": 0, "infants": 0},
        )


# ---- mock backend --------------------------------------------------


def test_mock_one_way_search_returns_two_results():
    client = TestClient(mock_app)
    r = client.post(
        "/v1/flights/search",
        headers={**BEARER, "Content-Type": "application/json"},
        json={
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-06-01",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["currency"] == "INR"
    assert body["total_results"] == 2
    assert len(body["results"]) == 2
    # All one-way results have empty return_segments
    for res in body["results"]:
        assert res["return_segments"] == []
        assert len(res["outbound_segments"]) == 1


def test_mock_round_trip_search_returns_cartesian_combinations():
    """2 outbound × 1 return = 2 round-trip results."""
    client = TestClient(mock_app)
    r = client.post(
        "/v1/flights/search",
        headers={**BEARER, "Content-Type": "application/json"},
        json={
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-06-01",
            "return_date": "2026-06-08",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_results"] == 2  # 2 outbound × 1 return
    for res in body["results"]:
        assert len(res["outbound_segments"]) == 1
        assert len(res["return_segments"]) == 1
        # Outbound origin matches request, return origin matches request destination
        assert res["outbound_segments"][0]["origin"] == "DEL"
        assert res["return_segments"][0]["origin"] == "BOM"


def test_mock_round_trip_combined_fare_is_sum_of_legs():
    client = TestClient(mock_app)
    r = client.post(
        "/v1/flights/search",
        headers={**BEARER, "Content-Type": "application/json"},
        json={
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-06-01",
            "return_date": "2026-06-08",
        },
    )
    body = r.json()
    cheapest = min(res["fare"]["total_amount"] for res in body["results"])
    # Cheapest outbound = 5000 (SAVER), only return = 5350. Total = 10350.
    assert cheapest == 10350


def test_mock_round_trip_with_no_return_seeds_falls_back_to_one_way():
    """BOM->BLR has outbound seeds but BLR->BOM doesn't. Round-trip
    request degrades to one-way (return_segments stays empty)."""
    client = TestClient(mock_app)
    r = client.post(
        "/v1/flights/search",
        headers={**BEARER, "Content-Type": "application/json"},
        json={
            "origin": "BOM", "destination": "BLR",
            "departure_date": "2026-06-15",
            "return_date": "2026-06-22",
        },
    )
    assert r.status_code == 200
    for res in r.json()["results"]:
        assert res["return_segments"] == []


def test_mock_unknown_route_returns_404():
    client = TestClient(mock_app)
    r = client.post(
        "/v1/flights/search",
        headers={**BEARER, "Content-Type": "application/json"},
        json={
            "origin": "DEL", "destination": "BLR",
            "departure_date": "2026-12-25",
        },
    )
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "NO_FLIGHTS_FOUND"
    assert body["error"]["details"]["origin"] == "DEL"


def test_mock_missing_bearer_returns_401():
    client = TestClient(mock_app)
    r = client.post(
        "/v1/flights/search",
        headers={"Content-Type": "application/json"},
        json={
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-06-01",
        },
    )
    assert r.status_code == 401


# ---- API client end-to-end against mock ---------------------------


def _client_against_mock() -> AirlineApiClient:
    test_http = TestClient(mock_app, base_url="http://mock-airline.test")
    return AirlineApiClient(
        base_url="http://mock-airline.test",
        service_token="test-token-123",
        http_client=test_http,
        max_retries=0,
    )


def test_client_search_one_way_happy_path():
    client = _client_against_mock()
    resp = client.search_flights(
        FlightSearchRequest(
            origin="DEL", destination="BOM", departure_date="2026-06-01",
        ),
        request_id="req-1", trace_id="trace-1",
    )
    assert isinstance(resp, FlightSearchResponse)
    assert resp.total_results == 2
    assert all(r.return_segments == [] for r in resp.results)


def test_client_search_round_trip_returns_combined_segments():
    client = _client_against_mock()
    resp = client.search_flights(
        FlightSearchRequest(
            origin="DEL", destination="BOM",
            departure_date="2026-06-01",
            return_date="2026-06-08",
        ),
    )
    assert resp.total_results == 2
    for r in resp.results:
        assert len(r.outbound_segments) == 1
        assert len(r.return_segments) == 1


def test_client_propagates_404_as_apierror():
    client = _client_against_mock()
    with pytest.raises(AirlineApiError) as exc_info:
        client.search_flights(
            FlightSearchRequest(
                origin="DEL", destination="BLR",
                departure_date="2026-12-25",
            ),
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "NO_FLIGHTS_FOUND"


# ---- AviationDomain.dispatch_tool ---------------------------------


def test_plugin_dispatch_returns_dict():
    domain = AviationDomain(api_client=_client_against_mock())
    result = domain.dispatch_tool(
        "flight_search",
        {
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-06-01",
        },
    )
    assert isinstance(result, dict)
    assert result["total_results"] == 2


def test_plugin_dispatch_invalid_args_raises():
    domain = AviationDomain(api_client=_client_against_mock())
    with pytest.raises(ValidationError):
        # missing required destination
        domain.dispatch_tool(
            "flight_search",
            {"origin": "DEL", "departure_date": "2026-06-01"},
        )


# ---- chip-flow integration (the user's "BOM" use cases) -----------


def _patch_domain(monkeypatch: pytest.MonkeyPatch) -> AviationDomain:
    domain = AviationDomain(api_client=_client_against_mock())
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


def test_chip_flow_origin_pre_filled_asks_for_destination_then_date(
    monkeypatch: pytest.MonkeyPatch,
):
    """The 'Flights from city' chip use case: frontend pre-fills origin
    with the user's typed value, backend asks for the next missing
    required field (destination)."""
    _patch_domain(monkeypatch)

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="flight_search",
        action_params={"origin": "BOM"},
    ))
    assert out["mode"] == "action_collecting"
    assert out["missing_param"] == "destination"
    # Prompt phrases this as "Where are you flying to?" — check for the
    # destination cue rather than the literal field name.
    assert "flying to" in out["answer"].lower()


def test_chip_flow_destination_pre_filled_asks_for_origin(
    monkeypatch: pytest.MonkeyPatch,
):
    """The 'Flights to city' chip use case: frontend pre-fills destination,
    backend asks for origin first (then date)."""
    _patch_domain(monkeypatch)

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="flight_search",
        action_params={"destination": "BOM"},
    ))
    assert out["mode"] == "action_collecting"
    assert out["missing_param"] == "origin"


def test_chip_flow_full_one_way_dispatches_with_results(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_domain(monkeypatch)

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="flight_search",
        action_params={
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-06-01",
        },
    ))
    assert out["mode"] == "tool_executed"
    assert out["render_as"] == "flight_results_card"
    assert out["tool_result"]["total_results"] == 2
    # Summary should mention one-way + count + cheapest fare
    assert "2" in out["answer"]
    assert "one-way" in out["answer"].lower()


def test_chip_flow_full_round_trip_with_pre_filled_return_date(
    monkeypatch: pytest.MonkeyPatch,
):
    """The 'I want a round trip' use case: frontend pre-fills return_date
    via a date-picker. Backend doesn't prompt for return_date (it's
    optional) but accepts it and dispatches a round-trip search."""
    _patch_domain(monkeypatch)

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="flight_search",
        action_params={
            "origin": "DEL", "destination": "BOM",
            "departure_date": "2026-06-01",
            "return_date": "2026-06-08",
        },
    ))
    assert out["mode"] == "tool_executed"
    assert out["tool_result"]["total_results"] == 2
    # Each result has return segments populated
    for res in out["tool_result"]["results"]:
        assert len(res["return_segments"]) == 1
    assert "round trip" in out["answer"].lower()


def test_chip_flow_user_input_extracts_origin_and_destination(
    monkeypatch: pytest.MonkeyPatch,
):
    """User typed 'DEL to BOM' as user_input. extract_params (mocked)
    fills origin + destination; backend asks for missing date."""
    _patch_domain(monkeypatch)

    monkeypatch.setattr(
        chat_pg_actions,
        "extract_params",
        lambda text, schemas, **_kw: {"origin": "DEL", "destination": "BOM"},
    )

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="flight_search",
        action_params={},
        user_input="DEL to BOM",
    ))
    assert out["mode"] == "action_collecting"
    assert out["missing_param"] == "departure_date"
    # Acknowledgement names BOTH fields just collected
    answer = out["answer"]
    assert "Thanks!" in answer
    assert "DEL" in answer
    assert "BOM" in answer


def test_chip_flow_user_input_extracts_all_three_for_round_trip(
    monkeypatch: pytest.MonkeyPatch,
):
    """User typed 'DEL BOM 2026-06-01 returning 2026-06-08' — extractor
    fills all four fields including the optional return_date, and the
    search dispatches as round-trip."""
    _patch_domain(monkeypatch)

    monkeypatch.setattr(
        chat_pg_actions,
        "extract_params",
        lambda text, schemas, **_kw: {
            "origin": "DEL",
            "destination": "BOM",
            "departure_date": "2026-06-01",
            "return_date": "2026-06-08",
        },
    )

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="flight_search",
        action_params={},
        user_input="DEL BOM 2026-06-01 returning 2026-06-08",
    ))
    assert out["mode"] == "tool_executed", out
    # Round-trip: each result has return segments
    for res in out["tool_result"]["results"]:
        assert len(res["return_segments"]) == 1


def test_chip_flow_unknown_route_returns_search_specific_404_copy(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression: 404 for flight_search must use the search-specific
    copy ("no flights for that route on that date"), NOT the generic
    "couldn't find what you asked about" or any other tool's phrasing.
    """
    _patch_domain(monkeypatch)

    out = _handle(ChatRequest(
        conversation_id="conv-1", agent_type="aviation",
        action="flight_search",
        action_params={
            "origin": "DEL", "destination": "BLR",
            "departure_date": "2026-12-25",  # no seeds for this route+date
        },
    ))
    assert out["mode"] == "tool_error"
    assert out["error_code"] == "NO_FLIGHTS_FOUND"
    answer_lower = out["answer"].lower()
    assert "flights" in answer_lower
    assert "route" in answer_lower or "date" in answer_lower
    # MUST NOT borrow other tools' copy
    assert "booking" not in answer_lower
    assert "reference" not in answer_lower
