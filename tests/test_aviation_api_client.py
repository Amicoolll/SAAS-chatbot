"""Slice 1 — AirlineApiClient end-to-end against the mock backend.

The client uses ``httpx.ASGITransport`` to hit the mock FastAPI app
in-process. This exercises the full HTTP path (auth headers, error
envelope parsing, status-code mapping) without real network.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.domains.aviation.api_client import (
    AirlineApiClient,
    AirlineApiError,
    AirlineApiTransportError,
)
from app.domains.aviation.models import BookingLookupRequest
from tools.aviation_mock.app import app as mock_app


@pytest.fixture
def client() -> AirlineApiClient:
    """An AirlineApiClient wired to the in-process mock app.

    FastAPI's TestClient is an ``httpx.Client`` subclass with a sync-capable
    transport — drops in where AirlineApiClient expects an httpx.Client.
    Avoids needing a real localhost server in unit tests.
    """
    test_http = TestClient(mock_app, base_url="http://mock-airline.test")
    return AirlineApiClient(
        base_url="http://mock-airline.test",
        service_token="test-token-123",
        http_client=test_http,
        max_retries=0,  # explicit: don't retry in unit tests
    )


def test_lookup_booking_success(client: AirlineApiClient):
    resp = client.lookup_booking(
        BookingLookupRequest(booking_reference="ABC123", last_name="DOE"),
        request_id="req-success",
        trace_id="trace-success",
    )
    assert resp.booking_reference == "ABC123"
    assert resp.status == "CONFIRMED"
    assert len(resp.passengers) == 2
    assert resp.segments[0].flight_number == "AI101"


def test_lookup_booking_unknown_pnr_raises_404_apierror(client: AirlineApiClient):
    with pytest.raises(AirlineApiError) as exc_info:
        client.lookup_booking(
            BookingLookupRequest(booking_reference="ZZZ999", last_name="DOE")
        )
    err = exc_info.value
    assert err.status_code == 404
    assert err.code == "BOOKING_NOT_FOUND"
    assert "ZZZ999" in str(err.details)


def test_lookup_booking_wrong_lastname_raises_403(client: AirlineApiClient):
    with pytest.raises(AirlineApiError) as exc_info:
        client.lookup_booking(
            BookingLookupRequest(booking_reference="ABC123", last_name="WRONG")
        )
    err = exc_info.value
    assert err.status_code == 403
    assert err.code == "BOOKING_VERIFICATION_FAILED"


def test_client_sends_bearer_token():
    """Client must include the configured bearer token; mock returns 401 otherwise."""
    test_http = TestClient(mock_app, base_url="http://mock-airline.test")
    no_token_client = AirlineApiClient(
        base_url="http://mock-airline.test",
        service_token=None,  # explicit: no token
        http_client=test_http,
        max_retries=0,
    )
    with pytest.raises(AirlineApiError) as exc_info:
        no_token_client.lookup_booking(
            BookingLookupRequest(booking_reference="ABC123", last_name="DOE")
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "INVALID_CREDENTIALS"


def test_client_sends_request_id_and_trace_id():
    """Captured outbound headers should include both id headers when supplied."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(
            200,
            json={
                "booking_reference": "ABC123",
                "status": "CONFIRMED",
                "passengers": [],
                "segments": [],
            },
        )

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://airline.example",
        timeout=5.0,
    )
    client = AirlineApiClient(
        base_url="http://airline.example",
        service_token="abc",
        http_client=http,
        max_retries=0,
    )
    client.lookup_booking(
        BookingLookupRequest(booking_reference="ABC123", last_name="DOE"),
        request_id="req-xyz",
        trace_id="trace-xyz",
    )
    assert captured["x-request-id"] == "req-xyz"
    assert captured["x-trace-id"] == "trace-xyz"
    assert captured["authorization"] == "Bearer abc"


def test_client_auto_generates_request_id_when_missing():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(
            200,
            json={
                "booking_reference": "ABC123",
                "status": "CONFIRMED",
                "passengers": [],
                "segments": [],
            },
        )

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://airline.example",
        timeout=5.0,
    )
    client = AirlineApiClient(
        base_url="http://airline.example",
        service_token="abc",
        http_client=http,
        max_retries=0,
    )
    client.lookup_booking(
        BookingLookupRequest(booking_reference="ABC123", last_name="DOE")
    )
    # auto-generated UUID present
    assert captured.get("x-request-id"), "request id should always be sent"


def test_client_retries_on_5xx_then_raises():
    call_count = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(
            503,
            json={"error": {"code": "INTERNAL_ERROR", "message": "down"}},
        )

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://airline.example",
        timeout=5.0,
    )
    client = AirlineApiClient(
        base_url="http://airline.example",
        service_token="abc",
        http_client=http,
        max_retries=2,
    )
    with pytest.raises(AirlineApiError) as exc_info:
        client.lookup_booking(
            BookingLookupRequest(booking_reference="ABC123", last_name="DOE")
        )
    assert exc_info.value.status_code == 503
    assert call_count["n"] == 3  # initial + 2 retries


def test_client_does_not_retry_on_4xx():
    call_count = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(
            404,
            json={"error": {"code": "BOOKING_NOT_FOUND", "message": "no such pnr"}},
        )

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://airline.example",
        timeout=5.0,
    )
    client = AirlineApiClient(
        base_url="http://airline.example",
        service_token="abc",
        http_client=http,
        max_retries=3,
    )
    with pytest.raises(AirlineApiError):
        client.lookup_booking(
            BookingLookupRequest(booking_reference="ABC123", last_name="DOE")
        )
    assert call_count["n"] == 1  # no retries for client errors


def test_client_raises_transport_error_on_unparseable_body():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json", headers={"content-type": "text/plain"})

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://airline.example",
        timeout=5.0,
    )
    client = AirlineApiClient(
        base_url="http://airline.example",
        service_token="abc",
        http_client=http,
        max_retries=0,
    )
    with pytest.raises(AirlineApiTransportError):
        client.lookup_booking(
            BookingLookupRequest(booking_reference="ABC123", last_name="DOE")
        )


def test_client_rejects_empty_base_url():
    with pytest.raises(ValueError, match="base_url is required"):
        AirlineApiClient(base_url="", service_token="abc")
