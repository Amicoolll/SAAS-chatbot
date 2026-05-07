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


# ---- standard error envelope (used across all endpoints) -------------


class ApiErrorBody(BaseModel):
    code: str
    message: str
    details: dict | None = None


class ApiErrorResponse(BaseModel):
    error: ApiErrorBody
