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


SEED_FLIGHTS: dict[tuple[str, str], dict[str, Any]] = {
    ("AI101", "2026-06-01"): {
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
    },
    ("AI202", "2026-06-15"): {
        "flight_number": "AI202",
        "date": "2026-06-15",
        "origin": "BOM",
        "destination": "BLR",
        "scheduled_departure": "2026-06-15T14:00:00+05:30",
        "scheduled_arrival": "2026-06-15T15:30:00+05:30",
        "estimated_departure": "2026-06-15T14:00:00+05:30",
        "estimated_arrival": "2026-06-15T15:30:00+05:30",
        "actual_departure": None,
        "actual_arrival": None,
        "status": "ON_TIME",
        "delay_minutes": 0,
        "gate": None,
        "terminal": "T2",
        "aircraft_type": "A321",
    },
    ("UK801", "2026-07-04"): {
        "flight_number": "UK801",
        "date": "2026-07-04",
        "origin": "DEL",
        "destination": "BLR",
        "scheduled_departure": "2026-07-04T17:00:00+05:30",
        "scheduled_arrival": "2026-07-04T19:30:00+05:30",
        "estimated_departure": None,
        "estimated_arrival": None,
        "actual_departure": None,
        "actual_arrival": None,
        "status": "CANCELLED",
        "delay_minutes": 0,
        "gate": None,
        "terminal": None,
        "aircraft_type": "B737",
    },
}


def lookup_flight_status(
    flight_number: str, date: str
) -> tuple[int, dict[str, Any]]:
    """Mock flight-status lookup: returns ``(status_code, body)``.

    Mirrors the contract:
        - 404 FLIGHT_NOT_FOUND when no entry matches
        - 200 with the flight body otherwise

    Lookup key is case-normalized for the flight number (``ai101`` →
    ``AI101``); the date is treated as-is and must match the seed key
    exactly (YYYY-MM-DD).
    """
    flight = SEED_FLIGHTS.get((flight_number.upper(), date))
    if not flight:
        return 404, {
            "error": {
                "code": "FLIGHT_NOT_FOUND",
                "message": f"No flight {flight_number} on {date}.",
                "details": {"flight_number": flight_number, "date": date},
            }
        }
    return 200, flight


# ---- POST /v1/flights/search mock --------------------------------

# Seed entries are keyed by (origin, destination, date). Each entry is a
# list of flight options for that O&D + date. Round-trip queries (with
# a return_date) look up BOTH legs and the helper combines them as
# outbound × return.
#
# To keep round-trip combinations sensible, we also seed the reverse
# direction for at least one date so e.g. DEL→BOM 2026-06-01 + return
# BOM→DEL 2026-06-08 produces results.
SEED_SEARCH_RESULTS: dict[tuple[str, str, str], list[dict[str, Any]]] = {
    ("DEL", "BOM", "2026-06-01"): [
        {
            "result_id": "del-bom-0601-A320-0800",
            "flight_number": "AI101",
            "departure_time": "2026-06-01T08:00:00+05:30",
            "arrival_time": "2026-06-01T10:15:00+05:30",
            "duration_minutes": 135,
            "stops": 0,
            "aircraft_type": "A320",
            "cabin_class": "ECONOMY",
            "fare_basis": "Y",
            "fare": {
                "base_amount": 4200, "taxes_amount": 800, "total_amount": 5000,
                "currency": "INR", "fare_type": "SAVER",
                "refundable": False, "changes_allowed": True,
            },
            "baggage_allowance": {"cabin_kg": 7, "checked_kg": 15},
            "seats_remaining": 12,
        },
        {
            "result_id": "del-bom-0601-A321-1430",
            "flight_number": "AI203",
            "departure_time": "2026-06-01T14:30:00+05:30",
            "arrival_time": "2026-06-01T16:30:00+05:30",
            "duration_minutes": 120,
            "stops": 0,
            "aircraft_type": "A321",
            "cabin_class": "ECONOMY",
            "fare_basis": "M",
            "fare": {
                "base_amount": 6500, "taxes_amount": 950, "total_amount": 7450,
                "currency": "INR", "fare_type": "FLEXI",
                "refundable": True, "changes_allowed": True,
            },
            "baggage_allowance": {"cabin_kg": 7, "checked_kg": 25},
            "seats_remaining": 4,
        },
    ],
    ("BOM", "DEL", "2026-06-08"): [
        {
            "result_id": "bom-del-0608-A320-1900",
            "flight_number": "AI104",
            "departure_time": "2026-06-08T19:00:00+05:30",
            "arrival_time": "2026-06-08T21:15:00+05:30",
            "duration_minutes": 135,
            "stops": 0,
            "aircraft_type": "A320",
            "cabin_class": "ECONOMY",
            "fare_basis": "Y",
            "fare": {
                "base_amount": 4500, "taxes_amount": 850, "total_amount": 5350,
                "currency": "INR", "fare_type": "SAVER",
                "refundable": False, "changes_allowed": True,
            },
            "baggage_allowance": {"cabin_kg": 7, "checked_kg": 15},
            "seats_remaining": 18,
        },
    ],
    ("BOM", "BLR", "2026-06-15"): [
        {
            "result_id": "bom-blr-0615-A321-1400",
            "flight_number": "AI202",
            "departure_time": "2026-06-15T14:00:00+05:30",
            "arrival_time": "2026-06-15T15:30:00+05:30",
            "duration_minutes": 90,
            "stops": 0,
            "aircraft_type": "A321",
            "cabin_class": "BUSINESS",
            "fare_basis": "J",
            "fare": {
                "base_amount": 14000, "taxes_amount": 1500, "total_amount": 15500,
                "currency": "INR", "fare_type": "BUSINESS_FLEXI",
                "refundable": True, "changes_allowed": True,
            },
            "baggage_allowance": {"cabin_kg": 14, "checked_kg": 35},
            "seats_remaining": 6,
        },
    ],
}


def _to_segment(option: dict[str, Any], origin: str, destination: str) -> dict[str, Any]:
    """Project a seed option into the FlightSegment shape the contract returns."""
    return {
        "flight_number": option["flight_number"],
        "origin": origin,
        "destination": destination,
        "departure_time": option["departure_time"],
        "arrival_time": option["arrival_time"],
        "duration_minutes": option["duration_minutes"],
        "stops": option["stops"],
        "aircraft_type": option["aircraft_type"],
        "cabin_class": option["cabin_class"],
        "fare_basis": option["fare_basis"],
    }


def search_flights_mock(req: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Mock flight search.

    For one-way (no return_date): returns one result per outbound option.
    For round-trip: returns the cartesian product (outbound × return) with
    a combined fare (sum of both legs' total_amount, same currency).

    404 NO_FLIGHTS_FOUND when the outbound leg has no seeds. If the
    outbound exists but the return doesn't, falls back to one-way (with
    a warning-style hint encoded in the response — keeps the demo useful
    rather than failing the whole search).
    """
    origin = req["origin"].upper()
    destination = req["destination"].upper()
    departure_date = req["departure_date"]
    return_date = req.get("return_date")
    currency = req.get("currency") or "INR"

    outbound = SEED_SEARCH_RESULTS.get((origin, destination, departure_date))
    if not outbound:
        return 404, {
            "error": {
                "code": "NO_FLIGHTS_FOUND",
                "message": (
                    f"No flights from {origin} to {destination} on {departure_date}."
                ),
                "details": {
                    "origin": origin,
                    "destination": destination,
                    "departure_date": departure_date,
                },
            }
        }

    return_options: list[dict[str, Any]] = []
    if return_date:
        return_options = SEED_SEARCH_RESULTS.get(
            (destination, origin, return_date), []
        )

    results: list[dict[str, Any]] = []
    if return_date and return_options:
        for ob in outbound:
            ob_seg = _to_segment(ob, origin, destination)
            for rb in return_options:
                rb_seg = _to_segment(rb, destination, origin)
                combined_total = round(
                    ob["fare"]["total_amount"] + rb["fare"]["total_amount"], 2
                )
                combined_base = round(
                    ob["fare"]["base_amount"] + rb["fare"]["base_amount"], 2
                )
                combined_tax = round(combined_total - combined_base, 2)
                results.append({
                    "result_id": f"{ob['result_id']}__{rb['result_id']}",
                    "outbound_segments": [ob_seg],
                    "return_segments": [rb_seg],
                    "fare": {
                        **ob["fare"],
                        "base_amount": combined_base,
                        "taxes_amount": combined_tax,
                        "total_amount": combined_total,
                        "currency": currency,
                    },
                    "baggage_allowance": ob["baggage_allowance"],
                    "seats_remaining": min(
                        ob.get("seats_remaining") or 0,
                        rb.get("seats_remaining") or 0,
                    ),
                })
    else:
        # One-way (or round-trip with no return seeds → silently degrade
        # to one-way; a real airline API would return a different code,
        # but for demo purposes keeping the user moving is fine).
        for ob in outbound:
            ob_seg = _to_segment(ob, origin, destination)
            results.append({
                "result_id": ob["result_id"],
                "outbound_segments": [ob_seg],
                "return_segments": [],
                "fare": {**ob["fare"], "currency": currency},
                "baggage_allowance": ob["baggage_allowance"],
                "seats_remaining": ob.get("seats_remaining"),
            })

    return 200, {
        "currency": currency,
        "results": results,
        "total_results": len(results),
        "next_cursor": None,
    }


# ---- GET /v1/bookings/{ref}/boarding-pass mock ---------------------

# Pre-seeded boarding passes — represent passengers already checked in
# via some other flow (so the boarding_pass chip works in a fresh demo
# without first running web_checkin). The web_checkin handler appends
# to this dict on success, so a freshly-checked-in passenger can
# immediately retrieve their boarding pass.
#
# Keyed by (booking_reference, passenger_id, segment_id). Value is the
# full BoardingPassResponse payload shape.
_DEFAULT_BOARDING_PASSES: dict[tuple[str, str, str], dict[str, Any]] = {
    ("ABC123", "p1", "s1"): {
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
    },
    ("ABC123", "p2", "s1"): {
        "passenger": {"first_name": "JANE", "last_name": "DOE"},
        "flight_number": "AI101",
        "origin": "DEL",
        "destination": "BOM",
        "scheduled_departure": "2026-06-01T08:00:00+05:30",
        "boarding_time": "2026-06-01T07:30:00+05:30",
        "seat": "12B",
        "boarding_group": "1",
        "sequence_number": 43,
        "gate": "B12",
        "terminal": "T3",
        "barcode_format": "PDF417",
        "barcode_data": "M1DOE/JANE              EABC123 DELBOMAI 0101 152Y012B0043 100",
    },
}

_BOARDING_PASSES: dict[tuple[str, str, str], dict[str, Any]] = dict(
    _DEFAULT_BOARDING_PASSES
)


def _reset_boarding_passes() -> None:
    """Test-only: restore the boarding-pass cache to its default seed."""
    global _BOARDING_PASSES
    _BOARDING_PASSES = dict(_DEFAULT_BOARDING_PASSES)


def boarding_pass_mock(
    booking_reference: str,
    passenger_id: str,
    segment_id: str,
    last_name: str | None,
    fmt: str,
) -> tuple[int, dict[str, Any]]:
    """Mock boarding-pass retrieval.

    Returns (status_code, body). Body is the JSON shape for ``format=json``
    or a 501 NOT_IMPLEMENTED envelope for binary formats (deferred in v1).
    """
    pnr = booking_reference.upper()

    booking = SEED_BOOKINGS.get(pnr)
    if not booking:
        return 404, {
            "error": {
                "code": "BOOKING_NOT_FOUND",
                "message": "No booking matches that reference.",
                "details": {"booking_reference": pnr},
            }
        }

    if last_name:
        surnames = {p["last_name"].upper() for p in booking["passengers"]}
        if last_name.upper() not in surnames:
            return 403, {
                "error": {
                    "code": "BOOKING_VERIFICATION_FAILED",
                    "message": "No booking matches the supplied reference and last name.",
                    "details": {"booking_reference": pnr},
                }
            }

    if fmt != "json":
        return 501, {
            "error": {
                "code": "FORMAT_NOT_IMPLEMENTED",
                "message": (
                    f"Format {fmt!r} is not yet implemented in the reference mock. "
                    "Use format=json and render the barcode client-side."
                ),
                "details": {"format": fmt, "supported": ["json"]},
            }
        }

    bp = _BOARDING_PASSES.get((pnr, passenger_id, segment_id))
    if not bp:
        return 409, {
            "error": {
                "code": "NOT_CHECKED_IN",
                "message": (
                    "This passenger isn't checked in yet for that segment. "
                    "Complete web check-in first."
                ),
                "details": {
                    "booking_reference": pnr,
                    "passenger_id": passenger_id,
                    "segment_id": segment_id,
                },
            }
        }

    return 200, bp


def _store_boarding_passes_from_checkin(
    booking_reference: str, checkin_response: dict[str, Any]
) -> None:
    """After a successful checkin_mock, project each checked-in
    passenger into _BOARDING_PASSES so the boarding_pass endpoint can
    serve the freshly-issued barcode.
    """
    pnr = booking_reference.upper()
    booking = SEED_BOOKINGS.get(pnr) or {}
    passengers_by_id = {
        p["passenger_id"]: p for p in (booking.get("passengers") or [])
    }
    segments_by_id = {
        s["segment_id"]: s for s in (booking.get("segments") or [])
    }

    for entry in checkin_response.get("checked_in") or []:
        pid = entry["passenger_id"]
        sid = entry["segment_id"]
        bp = entry.get("boarding_pass") or {}
        pax = passengers_by_id.get(pid, {})
        seg = segments_by_id.get(sid, {})
        _BOARDING_PASSES[(pnr, pid, sid)] = {
            "passenger": {
                "first_name": pax.get("first_name", "PAX"),
                "last_name": pax.get("last_name", ""),
            },
            "flight_number": seg.get("flight_number", "??000"),
            "origin": seg.get("origin", "???"),
            "destination": seg.get("destination", "???"),
            "scheduled_departure": seg.get(
                "scheduled_departure", "2026-06-01T08:00:00+05:30"
            ),
            "boarding_time": bp.get("boarding_time", "2026-06-01T07:30:00+05:30"),
            "seat": entry.get("seat") or bp.get("seat", "??"),
            "boarding_group": bp.get("boarding_group", "1"),
            "sequence_number": 100,  # mock: real airline assigns from inventory
            "gate": bp.get("gate"),
            "terminal": seg.get("terminal", "T3"),  # cheat: re-use seg.terminal if present
            "barcode_format": "PDF417",
            "barcode_data": bp.get("barcode", ""),
        }


# ---- POST /v1/checkin mock --------------------------------------

# Idempotency cache. Keyed by Idempotency-Key header. Repeating the same
# key returns the cached response without re-executing — implements the
# v1 partner-API idempotency contract for write endpoints. Process-local
# (resets on mock restart, which is fine for demo / tests).
_CHECKIN_IDEMPOTENCY_CACHE: dict[str, tuple[int, dict[str, Any]]] = {}


def _reset_checkin_idempotency_cache() -> None:
    """Test-only: clear the cache between test cases."""
    _CHECKIN_IDEMPOTENCY_CACHE.clear()


# Per-PNR canned check-in behaviour. The same booking+last-name verifier
# the lookup helper uses gates entry; this map then decides the
# success/failure shape so we can demo every state.
_CHECKIN_BEHAVIOUR: dict[str, str] = {
    "ABC123": "ok",                       # success — both passengers, seats 12A/12B
    "XYZ789": "checkin_not_open",         # 409 — too far in the future
    # add more here as we test more error paths
}


def _build_checkin_success(req: dict[str, Any]) -> dict[str, Any]:
    """Compose a canned success response from the request shape.

    Real airline would assign seats from inventory; the mock just gives
    the first passenger 12A and the next 12B etc. Boarding passes carry
    a fake-but-shape-correct BCBP barcode.
    """
    seats = ["12A", "12B", "12C", "12D", "12E", "12F"]
    booking = SEED_BOOKINGS.get(req["booking_reference"].upper(), {})
    passengers_by_id = {
        p["passenger_id"]: p for p in (booking.get("passengers") or [])
    }

    checked_in: list[dict[str, Any]] = []
    for i, (pid, sid) in enumerate(
        # Cartesian — every requested passenger across every requested
        # segment. For a single-segment booking the result is one entry
        # per passenger.
        ((p, s) for p in req["passenger_ids"] for s in req["segment_ids"])
    ):
        seat = seats[i % len(seats)]
        first_name = passengers_by_id.get(pid, {}).get("first_name", "PAX")
        last_name = passengers_by_id.get(pid, {}).get("last_name", req["last_name"])
        # IATA BCBP M1 format (simplified for the mock — real grammar is denser).
        barcode = (
            f"M1{last_name}/{first_name:<18}"
            f"E{req['booking_reference']:<7}"
            f"DELBOMAI 0101 152Y{seat:<4}{i:04d} 100"
        )
        checked_in.append({
            "passenger_id": pid,
            "segment_id": sid,
            "seat": seat,
            "boarding_pass_url": (
                f"/v1/bookings/{req['booking_reference']}/boarding-pass"
                f"?passenger_id={pid}&segment_id={sid}"
            ),
            "boarding_pass": {
                "barcode": barcode,
                "seat": seat,
                "boarding_group": "1",
                "boarding_time": "2026-06-01T07:30:00+05:30",
                "gate": "B12",
            },
        })

    return {
        "checkin_id": f"ci_{req['booking_reference'].lower()}_{len(checked_in)}",
        "checked_in": checked_in,
        "segment_status": "CHECKED_IN",
        "warnings": [],
    }


def checkin_mock(
    req: dict[str, Any], idempotency_key: str | None
) -> tuple[int, dict[str, Any]]:
    """Mock check-in. Returns ``(status_code, body)``.

    Behaviour:
      - 401 INVALID_CREDENTIALS handled at the endpoint layer (bearer)
      - 422 ACCEPT_TERMS_REQUIRED if accept_terms is false
      - 422 IDEMPOTENCY_KEY_REQUIRED if missing
      - Idempotency replay: same key → cached response (200 success or
        whatever was cached, no re-execution)
      - 404 BOOKING_NOT_FOUND if booking doesn't exist
      - 403 BOOKING_VERIFICATION_FAILED if last_name doesn't match
      - 409 CHECKIN_NOT_OPEN for bookings whose departure is too far away
        (XYZ789 in seeds)
      - 200 with full check-in body otherwise
    """
    if not idempotency_key:
        return 422, {
            "error": {
                "code": "IDEMPOTENCY_KEY_REQUIRED",
                "message": "Idempotency-Key header is required for /v1/checkin.",
                "details": {},
            }
        }

    # Idempotency replay — return cached response without re-validating.
    cached = _CHECKIN_IDEMPOTENCY_CACHE.get(idempotency_key)
    if cached is not None:
        return cached

    if not req.get("accept_terms"):
        return 422, {
            "error": {
                "code": "ACCEPT_TERMS_REQUIRED",
                "message": "Caller must set accept_terms=true to confirm.",
                "details": {},
            }
        }

    pnr = req["booking_reference"].upper()
    booking = SEED_BOOKINGS.get(pnr)
    if not booking:
        return 404, {
            "error": {
                "code": "BOOKING_NOT_FOUND",
                "message": "No booking matches the supplied reference and last name.",
                "details": {"booking_reference": pnr},
            }
        }

    surnames = {p["last_name"].upper() for p in booking["passengers"]}
    if req["last_name"].upper() not in surnames:
        return 403, {
            "error": {
                "code": "BOOKING_VERIFICATION_FAILED",
                "message": "No booking matches the supplied reference and last name.",
                "details": {"booking_reference": pnr},
            }
        }

    behaviour = _CHECKIN_BEHAVIOUR.get(pnr, "ok")
    if behaviour == "checkin_not_open":
        result = (409, {
            "error": {
                "code": "CHECKIN_NOT_OPEN",
                "message": (
                    "Check-in opens 48 hours before departure. Please try again closer to your flight."
                ),
                "details": {
                    "booking_reference": pnr,
                    "opens_at": "2026-06-13T14:00:00+05:30",
                },
            }
        })
    else:
        success_body = _build_checkin_success(req)
        result = (200, success_body)
        # Project the freshly-issued boarding passes into the
        # /v1/bookings/{ref}/boarding-pass cache so a follow-up GET
        # returns the same barcode without requiring a re-check-in.
        _store_boarding_passes_from_checkin(pnr, success_body)

    # Cache the outcome (success OR error) under this idempotency key
    # so a replay returns the same body. Real APIs differ on whether
    # error responses are cached; for demo simplicity we cache both.
    _CHECKIN_IDEMPOTENCY_CACHE[idempotency_key] = result
    return result


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
