"""Slice 1 — reference mock backend behavior.

Contract-level tests: status codes, error envelope shape, auth handling.
The chatbot's API client (test_aviation_api_client.py) tests the round-trip
through this mock end-to-end.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tools.aviation_mock.app import app


client = TestClient(app)
BEARER = {"Authorization": "Bearer test-token-123"}


def test_lookup_returns_canned_booking():
    r = client.post(
        "/v1/bookings/lookup",
        headers={**BEARER, "X-Request-Id": "req-1"},
        json={"booking_reference": "ABC123", "last_name": "DOE"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["booking_reference"] == "ABC123"
    assert body["status"] == "CONFIRMED"
    assert {p["first_name"] for p in body["passengers"]} == {"JOHN", "JANE"}


def test_lookup_normalizes_pnr_case():
    r = client.post(
        "/v1/bookings/lookup",
        headers=BEARER,
        json={"booking_reference": "abc123", "last_name": "doe"},
    )
    assert r.status_code == 200, r.text


def test_lookup_unknown_pnr_returns_404_with_envelope():
    r = client.post(
        "/v1/bookings/lookup",
        headers=BEARER,
        json={"booking_reference": "ZZZ999", "last_name": "DOE"},
    )
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "BOOKING_NOT_FOUND"
    assert "details" in body["error"]


def test_lookup_known_pnr_wrong_lastname_returns_403():
    """Existing PNR + wrong verifier → 403, not 404. Prevents PNR enumeration."""
    r = client.post(
        "/v1/bookings/lookup",
        headers=BEARER,
        json={"booking_reference": "ABC123", "last_name": "WRONG"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "BOOKING_VERIFICATION_FAILED"


def test_lookup_missing_bearer_token_returns_401():
    r = client.post(
        "/v1/bookings/lookup",
        json={"booking_reference": "ABC123", "last_name": "DOE"},
    )
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "INVALID_CREDENTIALS"


def test_lookup_invalid_body_rejected_by_pydantic():
    r = client.post(
        "/v1/bookings/lookup",
        headers=BEARER,
        json={"booking_reference": "", "last_name": "DOE"},
    )
    # FastAPI/pydantic returns 422 for body validation. The client maps
    # that into a transport error path, but at the mock level we just
    # confirm the rejection.
    assert r.status_code == 422


def test_pending_booking_carries_balance_due():
    r = client.post(
        "/v1/bookings/lookup",
        headers=BEARER,
        json={"booking_reference": "XYZ789", "last_name": "SMITH"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "PENDING"
    assert body["balance_due"]["amount"] == 1500.0
    assert body["balance_due"]["currency"] == "INR"
