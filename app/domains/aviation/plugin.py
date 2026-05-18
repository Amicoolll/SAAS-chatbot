"""Aviation domain plugin — registers tools the LLM can call.

Slice 1 exposes a single tool: ``retrieve_booking``. Subsequent slices
register the rest. The shape of this file should stay flat — one tool
spec + one dispatch case per partner-API endpoint.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.core.config import settings
from app.domains.aviation.api_client import AirlineApiClient
from app.domains.aviation.models import (
    BoardingPassRequest,
    BookingLookupRequest,
    CheckinRequest,
    FlightSearchRequest,
    FlightStatusRequest,
)
from app.domains.base import DomainPlugin, ToolSpec


_TOOL_RETRIEVE_BOOKING = ToolSpec(
    name="retrieve_booking",
    description=(
        "Look up an existing flight booking by its PNR (booking reference) "
        "and the lead passenger's last name. Returns the booking status, "
        "passengers, segments, and ancillaries. Use when the user asks "
        "about an existing reservation."
    ),
    parameters_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["booking_reference", "last_name"],
        # Conversational opener used on the first turn of the chip flow,
        # asking warmly for ALL required fields at once. Hand-written
        # per tool — predictable, brand-consistent, no LLM cost on the
        # critical first turn. Non-standard JSON Schema keyword;
        # OpenAI function-calling ignores it.
        "intro_prompt": (
            "Sure — to retrieve your booking, I'll need your booking "
            "reference (PNR) and the last name on the ticket. You can "
            "share them both at once or one at a time."
        ),
        "properties": {
            "booking_reference": {
                "type": "string",
                "minLength": 1,
                "maxLength": 20,
                "description": "PNR / booking reference, usually 6 alphanumeric characters.",
                # `prompt`: per-field re-prompt used when only this one
                # field is still missing.
                "prompt": (
                    "Please share your booking reference (PNR). It's usually "
                    "6 letters and numbers."
                ),
                # `label`: short human-readable name for acknowledgements,
                # e.g. "Thanks! Got the PNR (ABC123)."
                "label": "PNR",
            },
            "last_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "description": "Lead passenger's last name as on the ticket.",
                "prompt": (
                    "Please share your last name as it appears on the ticket."
                ),
                "label": "last name",
            },
        },
    },
)


_TOOL_FLIGHT_STATUS = ToolSpec(
    name="flight_status",
    description=(
        "Look up the live status of a specific flight by its flight number "
        "and departure date — on-time / delayed / boarding / departed / "
        "arrived / cancelled, plus estimated times, gate, and terminal. "
        "Use when the user asks about a flight's current status."
    ),
    parameters_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["flight_number", "date"],
        "intro_prompt": (
            "Sure — to check a flight's status, I'll need the flight "
            "number (e.g. AI101) and the departure date (YYYY-MM-DD). "
            "You can share them both at once or one at a time."
        ),
        "properties": {
            "flight_number": {
                "type": "string",
                "minLength": 2,
                "maxLength": 10,
                "pattern": "^[A-Z0-9]+$",
                "description": "IATA flight designator, e.g. AI101.",
                "prompt": "What's the flight number? (e.g. AI101)",
                "label": "flight number",
            },
            "date": {
                "type": "string",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
                "description": "Departure date in YYYY-MM-DD.",
                "prompt": (
                    "What's the departure date? (YYYY-MM-DD, e.g. 2026-06-01)"
                ),
                "label": "date",
            },
        },
    },
)


_TOOL_FLIGHT_SEARCH = ToolSpec(
    name="flight_search",
    description=(
        "Search live flights between two cities on a given date. Supports "
        "one-way (just departure_date) and round-trip (also return_date). "
        "Returns one or more bookable itineraries with fares, durations, "
        "and baggage allowances. Use when the user wants to find flights "
        "to book — origin, destination, and date(s) are the inputs."
    ),
    parameters_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["origin", "destination", "departure_date"],
        "intro_prompt": (
            "Sure — to find flights, I'll need the origin city, destination "
            "city, and the departure date (YYYY-MM-DD). For a round trip, "
            "also share the return date. You can give them in one message "
            "or one at a time."
        ),
        "properties": {
            "origin": {
                "type": "string",
                "minLength": 3,
                "maxLength": 3,
                "pattern": "^[A-Z]{3}$",
                "description": (
                    "Origin airport — IATA 3-letter code, e.g. DEL for Delhi, "
                    "BOM for Mumbai."
                ),
                "prompt": (
                    "Where are you flying from? (Airport code or city name "
                    "like DEL or Delhi)"
                ),
                "label": "origin",
            },
            "destination": {
                "type": "string",
                "minLength": 3,
                "maxLength": 3,
                "pattern": "^[A-Z]{3}$",
                "description": (
                    "Destination airport — IATA 3-letter code, e.g. BOM for "
                    "Mumbai, BLR for Bangalore."
                ),
                "prompt": (
                    "Where are you flying to? (Airport code or city name "
                    "like BOM or Mumbai)"
                ),
                "label": "destination",
            },
            "departure_date": {
                "type": "string",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
                "description": "Outbound date, YYYY-MM-DD (e.g. 2026-06-15).",
                "prompt": "What's the departure date? (YYYY-MM-DD)",
                "label": "departure date",
            },
            # Optional — required-only rule means we DON'T ask for these.
            # Multi-field extraction picks them up if user volunteers.
            "return_date": {
                "type": "string",
                "pattern": r"^\d{4}-\d{2}-\d{2}$",
                "description": (
                    "OPTIONAL. Return date for round-trip, YYYY-MM-DD. "
                    "Omit for one-way."
                ),
                "label": "return date",
            },
            "cabin_class": {
                "type": "string",
                "enum": ["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"],
                "description": "OPTIONAL. Defaults to ECONOMY.",
            },
        },
    },
)


_TOOL_WEB_CHECKIN = ToolSpec(
    name="web_checkin",
    description=(
        "Web check-in for one or more passengers on one or more segments "
        "of an existing booking. WRITE OPERATION — requires an "
        "Idempotency-Key. Returns seat assignments and IATA-format "
        "boarding-pass barcodes. Recommended workflow: call retrieve_booking "
        "first to get passenger and segment IDs, then dispatch this tool "
        "with the user's selections from a check-in widget."
    ),
    parameters_schema={
        "type": "object",
        "additionalProperties": False,
        "required": [
            "booking_reference", "last_name", "passenger_ids",
            "segment_ids", "accept_terms", "idempotency_key",
        ],
        # No intro_prompt — this tool is normally dispatched in one shot
        # by the frontend (after retrieve_booking + a check-in widget),
        # not collected conversationally. If a field IS missing the
        # per-field prompt fires as a developer-detectable error path.
        "properties": {
            "booking_reference": {
                "type": "string", "minLength": 1, "maxLength": 20,
                "description": "PNR for the booking being checked in.",
                "prompt": "What's the booking reference (PNR)?",
                "label": "PNR",
            },
            "last_name": {
                "type": "string", "minLength": 1, "maxLength": 100,
                "description": "Verifier last name on the booking.",
                "prompt": "What's the last name on the booking?",
                "label": "last name",
            },
            "passenger_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": (
                    "List of passenger IDs to check in (from the booking's "
                    "passengers[*].passenger_id). Frontend collects via the "
                    "check-in widget — never asked conversationally."
                ),
                "prompt": (
                    "[Frontend bug — passenger_ids should be pre-filled by "
                    "the check-in widget.] Which passengers are checking in?"
                ),
                "label": "passengers",
            },
            "segment_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": (
                    "List of segment IDs to check in for. Frontend pre-fills."
                ),
                "prompt": (
                    "[Frontend bug — segment_ids should be pre-filled by the "
                    "check-in widget.] Which segments are you checking in for?"
                ),
                "label": "segments",
            },
            "accept_terms": {
                "type": "boolean",
                "description": "Must be true; airline returns 422 otherwise.",
                "prompt": "Do you accept the airline's terms of service? (yes/no)",
                "label": "terms acceptance",
            },
            "idempotency_key": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "UUID generated by the frontend per check-in attempt. "
                    "Repeating the same key returns the cached response. "
                    "Frontend MUST always pre-fill this — never user-collected."
                ),
                "prompt": (
                    "[Frontend bug — idempotency_key should be auto-generated "
                    "by the frontend, never asked of the user.]"
                ),
                "label": "idempotency key",
            },
        },
    },
)


_TOOL_BOARDING_PASS = ToolSpec(
    name="boarding_pass",
    description=(
        "Retrieve a boarding pass for one passenger on one segment of an "
        "existing booking. Pre-condition: the passenger must already be "
        "checked in (via web_checkin or otherwise). Returns the seat, "
        "boarding group, gate, and IATA BCBP barcode (PDF417) the "
        "frontend can render. Use when the user wants to view or save "
        "their boarding pass — separately from the check-in flow."
    ),
    parameters_schema={
        "type": "object",
        "additionalProperties": False,
        "required": [
            "booking_reference", "last_name", "passenger_id", "segment_id",
        ],
        # No intro_prompt — like web_checkin, this tool is normally
        # dispatched in one shot by the frontend (after retrieve_booking
        # surfaces the passenger/segment IDs). Per-field prompts are a
        # developer-detectable fallback if the frontend forgets a slot.
        "properties": {
            "booking_reference": {
                "type": "string", "minLength": 1, "maxLength": 20,
                "description": "PNR for the booking.",
                "prompt": "What's the booking reference (PNR)?",
                "label": "PNR",
            },
            "last_name": {
                "type": "string", "minLength": 1, "maxLength": 100,
                "description": "Verifier last name on the booking.",
                "prompt": "What's the last name on the booking?",
                "label": "last name",
            },
            "passenger_id": {
                "type": "string", "minLength": 1,
                "description": (
                    "ID of the passenger whose boarding pass is being "
                    "fetched (from booking.passengers[*].passenger_id)."
                ),
                "prompt": (
                    "[Frontend bug — passenger_id should be pre-filled from "
                    "the booking.passengers list.] Which passenger?"
                ),
                "label": "passenger",
            },
            "segment_id": {
                "type": "string", "minLength": 1,
                "description": "ID of the flight segment for the boarding pass.",
                "prompt": (
                    "[Frontend bug — segment_id should be pre-filled.] "
                    "Which segment?"
                ),
                "label": "segment",
            },
            "format": {
                "type": "string",
                "enum": ["json", "pdf", "wallet_apple", "wallet_google"],
                "description": (
                    "OPTIONAL. Defaults to 'json'. Binary formats not "
                    "implemented in v1 — frontend renders the BCBP "
                    "barcode_data field client-side."
                ),
            },
        },
    },
)


class AviationDomain(DomainPlugin):
    """Aviation chatbot domain. Talks to any airline backend that
    implements the v1 partner API contract.
    """

    name: ClassVar[str] = "aviation"
    agent_keys: ClassVar[list[str]] = ["aviation"]

    def __init__(self, api_client: AirlineApiClient | None = None) -> None:
        # Tests inject their own client (often pointed at the FastAPI mock
        # via httpx ASGITransport). Production reads the URL/token from
        # settings — kept lazy so a missing env var doesn't crash app boot
        # for non-aviation tenants.
        self._injected_client = api_client

    @property
    def api_client(self) -> AirlineApiClient:
        if self._injected_client is not None:
            return self._injected_client
        # Lazily build the production client on first use. Errors here
        # are raised to the caller, not at import time.
        return AirlineApiClient(
            base_url=settings.AIRLINE_API_BASE_URL,
            service_token=settings.AIRLINE_API_TOKEN,
            timeout=settings.AIRLINE_API_TIMEOUT_SEC,
            max_retries=settings.AIRLINE_API_MAX_RETRIES,
        )

    def tools(self) -> list[ToolSpec]:
        return [
            _TOOL_RETRIEVE_BOOKING,
            _TOOL_FLIGHT_STATUS,
            _TOOL_FLIGHT_SEARCH,
            _TOOL_WEB_CHECKIN,
            _TOOL_BOARDING_PASS,
        ]

    def dispatch_tool(
        self, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        if tool_name == _TOOL_RETRIEVE_BOOKING.name:
            req = BookingLookupRequest(**args)
            resp = self.api_client.lookup_booking(req)
            return resp.model_dump(mode="json")
        if tool_name == _TOOL_FLIGHT_STATUS.name:
            req = FlightStatusRequest(**args)
            resp = self.api_client.get_flight_status(req)
            return resp.model_dump(mode="json")
        if tool_name == _TOOL_FLIGHT_SEARCH.name:
            req = FlightSearchRequest(**args)
            resp = self.api_client.search_flights(req)
            return resp.model_dump(mode="json")
        if tool_name == _TOOL_WEB_CHECKIN.name:
            # idempotency_key is a transport concern, not a CheckinRequest
            # body field — pull it out before validating the rest.
            args = dict(args)
            idempotency_key = args.pop("idempotency_key", None)
            if not idempotency_key:
                raise ValueError("idempotency_key is required for web_checkin")
            req = CheckinRequest(**args)
            resp = self.api_client.checkin(req, idempotency_key=idempotency_key)
            return resp.model_dump(mode="json")
        if tool_name == _TOOL_BOARDING_PASS.name:
            req = BoardingPassRequest(**args)
            resp = self.api_client.get_boarding_pass(req)
            return resp.model_dump(mode="json")
        raise ValueError(f"Unknown aviation tool: {tool_name!r}")
