"""Pydantic models mirroring the v1 partner API contract.

Source of truth: AVIATION_PARTNER_API.md at the repo root. When the
contract is converted to OpenAPI YAML, the codegen output replaces this
file. Until then, keep the two in sync.

Slice 1 only includes the Retrieve booking shapes. Subsequent slices
add the other endpoints' models here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---- shared primitives -----------------------------------------------


class Money(BaseModel):
    amount: float
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217")


class Contact(BaseModel):
    email: str | None = None
    phone: str | None = None


class SeatAssignment(BaseModel):
    passenger_id: str
    segment_id: str
    seat: str


class Ancillaries(BaseModel):
    checked_baggage_kg: int | None = Field(default=None, ge=0)
    seats: list[SeatAssignment] = Field(default_factory=list)


# ---- POST /v1/bookings/lookup ----------------------------------------


PassengerType = Literal["ADULT", "CHILD", "INFANT"]
CabinClass = Literal["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"]
SegmentStatus = Literal["CONFIRMED", "CHECKED_IN", "CANCELLED", "FLOWN"]
BookingStatus = Literal["CONFIRMED", "PENDING", "CANCELLED", "COMPLETED"]


class BookingLookupRequest(BaseModel):
    """Request body for ``POST /v1/bookings/lookup``."""

    booking_reference: str = Field(min_length=1, max_length=20, description="PNR")
    last_name: str = Field(min_length=1, max_length=100, description="Verifier")


class Passenger(BaseModel):
    passenger_id: str
    first_name: str
    last_name: str
    type: PassengerType


class Segment(BaseModel):
    segment_id: str
    flight_number: str
    origin: str = Field(min_length=3, max_length=3, description="IATA airport code")
    destination: str = Field(min_length=3, max_length=3, description="IATA airport code")
    scheduled_departure: datetime
    scheduled_arrival: datetime
    cabin_class: CabinClass
    fare_basis: str
    status: SegmentStatus


class BookingLookupResponse(BaseModel):
    """Response body for ``POST /v1/bookings/lookup`` (HTTP 200)."""

    booking_reference: str
    status: BookingStatus
    passengers: list[Passenger]
    segments: list[Segment]
    contact: Contact | None = None
    ancillaries: Ancillaries | None = None
    balance_due: Money | None = None


# ---- GET /v1/flights/status ------------------------------------------


FlightLiveStatus = Literal[
    "SCHEDULED",
    "ON_TIME",
    "DELAYED",
    "BOARDING",
    "DEPARTED",
    "ARRIVED",
    "CANCELLED",
    "DIVERTED",
]


class FlightStatusRequest(BaseModel):
    """Query params for ``GET /v1/flights/status``."""

    flight_number: str = Field(
        min_length=2,
        max_length=10,
        pattern=r"^[A-Z0-9]+$",
        description="IATA flight designator, e.g. AI101.",
    )
    date: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Departure date (origin local), YYYY-MM-DD.",
    )


class FlightStatusResponse(BaseModel):
    """Response body for ``GET /v1/flights/status`` (HTTP 200)."""

    flight_number: str
    date: str
    origin: str = Field(min_length=3, max_length=3, description="IATA airport code")
    destination: str = Field(min_length=3, max_length=3, description="IATA airport code")
    scheduled_departure: datetime
    scheduled_arrival: datetime
    estimated_departure: datetime | None = None
    estimated_arrival: datetime | None = None
    actual_departure: datetime | None = None
    actual_arrival: datetime | None = None
    status: FlightLiveStatus
    delay_minutes: int = Field(default=0, ge=0)
    gate: str | None = None
    terminal: str | None = None
    aircraft_type: str | None = None


# ---- POST /v1/flights/search -----------------------------------------


FareType = Literal["SAVER", "FLEXI", "PREMIUM", "BUSINESS_SAVER", "BUSINESS_FLEXI"]


class Passengers(BaseModel):
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=9)
    infants: int = Field(default=0, ge=0, le=9)


class FlightSearchRequest(BaseModel):
    """Request body for ``POST /v1/flights/search``."""

    origin: str = Field(
        min_length=3, max_length=3, pattern=r"^[A-Z]{3}$",
        description="IATA airport code (uppercase, 3 letters)",
    )
    destination: str = Field(
        min_length=3, max_length=3, pattern=r"^[A-Z]{3}$",
        description="IATA airport code (uppercase, 3 letters)",
    )
    departure_date: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Outbound date, YYYY-MM-DD.",
    )
    return_date: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description=(
            "Return date for round-trip, YYYY-MM-DD. Omit for one-way. When "
            "set, response results pair each outbound option with each "
            "return option (response.results[*].return_segments populated)."
        ),
    )
    passengers: Passengers = Field(default_factory=Passengers)
    cabin_class: CabinClass = "ECONOMY"
    max_stops: int | None = Field(default=None, ge=0, le=3)
    currency: str = Field(default="INR", min_length=3, max_length=3)


class FlightFare(BaseModel):
    base_amount: float = Field(ge=0)
    taxes_amount: float = Field(ge=0)
    total_amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    fare_type: FareType
    refundable: bool
    changes_allowed: bool


class BaggageAllowance(BaseModel):
    cabin_kg: int = Field(ge=0)
    checked_kg: int = Field(ge=0)


class FlightSegment(BaseModel):
    flight_number: str
    origin: str = Field(min_length=3, max_length=3)
    destination: str = Field(min_length=3, max_length=3)
    departure_time: datetime
    arrival_time: datetime
    duration_minutes: int = Field(ge=0)
    stops: int = Field(default=0, ge=0)
    aircraft_type: str | None = None
    cabin_class: CabinClass
    fare_basis: str


class FlightResult(BaseModel):
    """A single bookable itinerary returned by /v1/flights/search.

    For one-way searches, ``return_segments`` is empty.
    For round-trip, both lists are populated.
    """

    result_id: str
    outbound_segments: list[FlightSegment]
    return_segments: list[FlightSegment] = Field(default_factory=list)
    fare: FlightFare
    baggage_allowance: BaggageAllowance
    seats_remaining: int | None = Field(default=None, ge=0)


class FlightSearchResponse(BaseModel):
    currency: str = Field(min_length=3, max_length=3)
    results: list[FlightResult]
    total_results: int = Field(ge=0)
    next_cursor: str | None = None


# ---- POST /v1/checkin -------------------------------------------------


SegmentStatusAfterCheckin = Literal["CHECKED_IN", "PARTIALLY_CHECKED_IN"]


class MealPreference(BaseModel):
    passenger_id: str
    meal_code: str = Field(min_length=4, max_length=4, description="IATA meal code, e.g. VGML, KSML")


class CheckinPreferences(BaseModel):
    """Optional per-checkin preferences. Frontend usually omits or
    submits a small subset (e.g. wheelchair SSR if the user requested it).
    """

    ssr: list[str] = Field(
        default_factory=list,
        description="IATA Special Service Request codes, e.g. WCHR for wheelchair.",
    )
    meals: list[MealPreference] = Field(default_factory=list)


class CheckinRequest(BaseModel):
    """Body of ``POST /v1/checkin``. The Idempotency-Key is sent as an
    HTTP header, NOT in this body — see ``AirlineApiClient.checkin``.
    """

    booking_reference: str = Field(min_length=1, max_length=20)
    last_name: str = Field(min_length=1, max_length=100)
    passenger_ids: list[str] = Field(
        min_length=1,
        description="Subset of passengers to check in (from the booking).",
    )
    segment_ids: list[str] = Field(
        min_length=1,
        description="Subset of segments to check in for.",
    )
    accept_terms: bool = Field(
        description=(
            "Must be True; the airline returns 422 ACCEPT_TERMS_REQUIRED otherwise."
        ),
    )
    preferences: CheckinPreferences | None = None


class BoardingPassInfo(BaseModel):
    """Embedded boarding-pass data returned with each checked-in
    passenger. ``barcode_data`` follows the IATA BCBP standard so the
    frontend can encode it as a real PDF417 barcode.
    """

    barcode: str = Field(description="Raw IATA BCBP string for barcode rendering.")
    seat: str
    boarding_group: str
    boarding_time: datetime
    gate: str | None = None


class CheckedInPassenger(BaseModel):
    passenger_id: str
    segment_id: str
    seat: str
    boarding_pass_url: str = Field(
        description="URL the frontend can later fetch (or hand to the user)."
    )
    boarding_pass: BoardingPassInfo | None = None


class CheckinWarning(BaseModel):
    code: str
    message: str


class CheckinResponse(BaseModel):
    checkin_id: str
    checked_in: list[CheckedInPassenger]
    segment_status: SegmentStatusAfterCheckin
    warnings: list[CheckinWarning] = Field(default_factory=list)


# ---- standard error envelope (used across all endpoints) -------------


class ApiErrorBody(BaseModel):
    code: str
    message: str
    details: dict | None = None


class ApiErrorResponse(BaseModel):
    error: ApiErrorBody
