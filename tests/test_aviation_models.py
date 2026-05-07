"""Slice 1 — Pydantic models for the partner API contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domains.aviation.models import (
    BookingLookupRequest,
    BookingLookupResponse,
    Money,
)


def test_booking_lookup_request_minimal():
    req = BookingLookupRequest(booking_reference="ABC123", last_name="DOE")
    assert req.booking_reference == "ABC123"
    assert req.last_name == "DOE"


def test_booking_lookup_request_rejects_empty_strings():
    with pytest.raises(ValidationError):
        BookingLookupRequest(booking_reference="", last_name="DOE")
    with pytest.raises(ValidationError):
        BookingLookupRequest(booking_reference="ABC123", last_name="")


def test_booking_lookup_response_round_trip():
    payload = {
        "booking_reference": "ABC123",
        "status": "CONFIRMED",
        "passengers": [
            {"passenger_id": "p1", "first_name": "JOHN", "last_name": "DOE", "type": "ADULT"},
        ],
        "segments": [
            {
                "segment_id": "s1",
                "flight_number": "AI101",
                "origin": "DEL",
                "destination": "BOM",
                "scheduled_departure": "2026-06-01T08:00:00+05:30",
                "scheduled_arrival": "2026-06-01T10:00:00+05:30",
                "cabin_class": "ECONOMY",
                "fare_basis": "Y",
                "status": "CONFIRMED",
            }
        ],
        "balance_due": {"amount": 0, "currency": "INR"},
    }
    resp = BookingLookupResponse.model_validate(payload)
    assert resp.booking_reference == "ABC123"
    assert resp.status == "CONFIRMED"
    assert resp.passengers[0].first_name == "JOHN"
    assert resp.segments[0].origin == "DEL"
    assert resp.balance_due == Money(amount=0, currency="INR")


def test_booking_lookup_response_rejects_invalid_status():
    payload = {
        "booking_reference": "ABC123",
        "status": "FROZEN",  # not in the contract enum
        "passengers": [],
        "segments": [],
    }
    with pytest.raises(ValidationError):
        BookingLookupResponse.model_validate(payload)


def test_booking_lookup_response_rejects_bad_iata_code():
    payload = {
        "booking_reference": "ABC123",
        "status": "CONFIRMED",
        "passengers": [],
        "segments": [
            {
                "segment_id": "s1",
                "flight_number": "AI101",
                "origin": "DELHI",  # 5 chars, not IATA
                "destination": "BOM",
                "scheduled_departure": "2026-06-01T08:00:00+05:30",
                "scheduled_arrival": "2026-06-01T10:00:00+05:30",
                "cabin_class": "ECONOMY",
                "fare_basis": "Y",
                "status": "CONFIRMED",
            }
        ],
    }
    with pytest.raises(ValidationError):
        BookingLookupResponse.model_validate(payload)


def test_money_rejects_non_iso_currency():
    with pytest.raises(ValidationError):
        Money(amount=100.0, currency="RUPEES")  # not ISO 4217 length
