"""Reference mock backend implementing the v1 aviation partner API.

Used in two ways:

1. **In-process by tests** — the chatbot's ``AirlineApiClient`` connects
   to this app via ``httpx.ASGITransport`` so the full HTTP path
   (auth headers, error envelopes, status codes) is exercised without
   real network.
2. **As a starter kit for airline tech teams** — fork this directory,
   replace ``seed_data.py`` with real data sources, deploy. The
   conformance harness validates compliance.

Slice 1 only implements ``POST /v1/bookings/lookup``. Subsequent slices
add the rest of the v1 surface.

Run locally:
    PYTHONPATH=. uvicorn tools.aviation_mock.app:app --port 9000
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.domains.aviation.models import BookingLookupRequest
from tools.aviation_mock.seed_data import lookup_booking, lookup_flight_status


logger = logging.getLogger("aviation_mock")


app = FastAPI(
    title="Aviation Partner API — Reference Mock",
    version="1.0.0",
    description=(
        "Reference implementation of the v1 aviation partner API. "
        "Returns canned data for development, CI, and as a starting "
        "point for airline tech teams building real adapters."
    ),
)


@app.exception_handler(HTTPException)
async def _structured_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
    """Wrap HTTPExceptions in the partner contract's error envelope.

    HTTPException ``detail`` may be a string (we wrap it) or a pre-built
    ``{"error": {...}}`` dict (the route handler chose the code itself).
    """
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    # Default code-per-status mapping. Endpoint handlers can override by
    # raising HTTPException with a pre-built {"error": {...}} dict for
    # endpoint-specific codes (e.g. FLIGHT_NOT_FOUND vs BOOKING_NOT_FOUND).
    code_map = {
        400: "INVALID_REQUEST",
        401: "INVALID_CREDENTIALS",
        403: "BOOKING_VERIFICATION_FAILED",
        404: "NOT_FOUND",
        409: "OPERATION_NOT_ALLOWED",
        429: "RATE_LIMITED",
    }
    code = code_map.get(exc.status_code, "INTERNAL_ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": str(exc.detail), "details": {}}},
    )


def _require_bearer(authorization: str | None) -> None:
    """Mock token check — accepts any non-empty bearer token. Real
    airlines should validate signature/scope. The chatbot tests inject
    a known token; production deployments configure a real one.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty bearer token")


@app.post("/v1/bookings/lookup")
def post_bookings_lookup(
    body: BookingLookupRequest,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
):
    _require_bearer(authorization)

    status_code, payload = lookup_booking(body.booking_reference, body.last_name)
    if status_code != 200:
        # Re-raise so the structured-error handler emits the right shape.
        raise HTTPException(status_code=status_code, detail=payload)

    logger.info(
        "lookup_booking ok pnr=%s request_id=%s",
        body.booking_reference,
        x_request_id or "-",
    )
    return payload


@app.get("/v1/flights/status")
def get_flights_status(
    flight_number: str,
    date: str,
    authorization: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
):
    _require_bearer(authorization)

    status_code, payload = lookup_flight_status(flight_number, date)
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=payload)

    logger.info(
        "flight_status ok flight=%s date=%s request_id=%s",
        flight_number,
        date,
        x_request_id or "-",
    )
    return payload
