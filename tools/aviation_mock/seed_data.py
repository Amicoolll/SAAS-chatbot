"""Canned booking data for the reference mock airline backend.

Used by ``tools/aviation_mock/app.py`` to satisfy lookup requests during
local dev and CI. Airlines forking this mock as a starter kit should
replace the contents with real data sources.
"""

from __future__ import annotations

from typing import Any


# Booking reference → response payload (already shaped to the v1 partner
# API contract). Slice 1 only needs Retrieve booking, so seeds focus on
# that shape; later slices may extend with flight schedules, seat maps,
# etc.
SEED_BOOKINGS: dict[str, dict[str, Any]] = {
    "ABC123": {
        "booking_reference": "ABC123",
        "status": "CONFIRMED",
        "passengers": [
            {
                "passenger_id": "p1",
                "first_name": "JOHN",
                "last_name": "DOE",
                "type": "ADULT",
            },
            {
                "passenger_id": "p2",
                "first_name": "JANE",
                "last_name": "DOE",
                "type": "ADULT",
            },
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
        "contact": {"email": "john.doe@example.com", "phone": "+919876543210"},
        "ancillaries": {
            "checked_baggage_kg": 23,
            "seats": [
                {"passenger_id": "p1", "segment_id": "s1", "seat": "12A"},
                {"passenger_id": "p2", "segment_id": "s1", "seat": "12B"},
            ],
        },
        "balance_due": {"amount": 0, "currency": "INR"},
    },
    "XYZ789": {
        "booking_reference": "XYZ789",
        "status": "PENDING",
        "passengers": [
            {
                "passenger_id": "p1",
                "first_name": "ALICE",
                "last_name": "SMITH",
                "type": "ADULT",
            }
        ],
        "segments": [
            {
                "segment_id": "s1",
                "flight_number": "AI202",
                "origin": "BOM",
                "destination": "BLR",
                "scheduled_departure": "2026-06-15T14:00:00+05:30",
                "scheduled_arrival": "2026-06-15T15:30:00+05:30",
                "cabin_class": "BUSINESS",
                "fare_basis": "J",
                "status": "CONFIRMED",
            }
        ],
        "contact": {"email": "alice@example.com"},
        "balance_due": {"amount": 1500.0, "currency": "INR"},
    },
}


def lookup_booking(
    booking_reference: str, last_name: str
) -> tuple[int, dict[str, Any]]:
    """Mock lookup: returns ``(status_code, body)``.

    Mirrors the contract's behaviour:
    - 404 BOOKING_NOT_FOUND for unknown PNR (don't enumerate)
    - 403 BOOKING_VERIFICATION_FAILED when PNR exists but last name doesn't
      match any passenger (don't reveal "PNR exists" via 404)
    - 200 with the booking body otherwise

    The "lead passenger's last name" verifier is loose here — we accept a
    match against any passenger's last name to keep the mock useful for
    multi-passenger bookings.
    """
    booking = SEED_BOOKINGS.get(booking_reference.upper())
    if not booking:
        return 404, {
            "error": {
                "code": "BOOKING_NOT_FOUND",
                "message": "No booking matches the supplied reference and last name.",
                "details": {"booking_reference": booking_reference},
            }
        }

    surnames = {p["last_name"].upper() for p in booking["passengers"]}
    if last_name.upper() not in surnames:
        return 403, {
            "error": {
                "code": "BOOKING_VERIFICATION_FAILED",
                "message": "No booking matches the supplied reference and last name.",
                "details": {"booking_reference": booking_reference},
            }
        }

    return 200, booking
