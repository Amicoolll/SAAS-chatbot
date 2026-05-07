"""HTTP client for the airline partner API.

Speaks the v1 contract documented in AVIATION_PARTNER_API.md. Exists so
the rest of the chatbot is decoupled from any specific airline backend —
swap the ``base_url`` and we talk to a different airline. The reference
mock at ``tools/aviation_mock`` is the test target.

For slice 1 this client only knows how to look up a booking. Subsequent
slices add: flight status, flight search, seat map, seat select, web
check-in, boarding pass.

Cross-cutting behavior baked in from day one (so we don't have to retrofit
across 7 endpoints later):

- Bearer auth header
- ``X-Request-Id`` per call (caller-supplied or auto-uuid)
- ``X-Trace-Id`` propagation when the chatbot is in a Catapult-traced flow
- ``Idempotency-Key`` plumbing (used by write endpoints — slice 4+)
- Bounded retries with exponential backoff on connect errors and 5xx /
  429, never on 4xx (those are caller bugs)
- Standard ``{ "error": { "code", "message", "details" } }`` envelope
  parsed into :class:`AirlineApiError`
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx

from app.domains.aviation.models import (
    BookingLookupRequest,
    BookingLookupResponse,
)


logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT_SEC = 10.0
_DEFAULT_MAX_RETRIES = 2
_RETRY_BACKOFF_BASE_SEC = 0.25


class AirlineApiError(Exception):
    """Raised when the airline API returns a non-2xx response we parsed
    as the standard error envelope.
    """

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{status_code} {code}] {message}")


class AirlineApiTransportError(Exception):
    """Raised when the airline API was unreachable or returned a non-JSON
    body we couldn't interpret. Retries are already exhausted by the time
    this is raised.
    """


class AirlineApiClient:
    """Synchronous client. The chatbot is gunicorn-sync today; if we move
    to async later, mirror this class as ``AsyncAirlineApiClient`` rather
    than rewriting in place.
    """

    def __init__(
        self,
        base_url: str,
        service_token: str | None,
        *,
        timeout: float = _DEFAULT_TIMEOUT_SEC,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required (set AIRLINE_API_BASE_URL or pass explicitly)")
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._timeout = timeout
        self._max_retries = max(0, max_retries)
        # Caller may inject an httpx.Client (e.g. with ASGITransport for
        # tests); otherwise we create one bound to base_url.
        self._http = http_client or httpx.Client(base_url=self._base_url, timeout=timeout)
        self._owns_http = http_client is None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "AirlineApiClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- the only public method in slice 1 ---------------------------

    def lookup_booking(
        self,
        request: BookingLookupRequest,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> BookingLookupResponse:
        """POST /v1/bookings/lookup.

        Raises :class:`AirlineApiError` for any 4xx/5xx with a parseable
        error body, :class:`AirlineApiTransportError` on connect / decode
        failures after retries.
        """
        body = self._post(
            "/v1/bookings/lookup",
            json_body=request.model_dump(),
            request_id=request_id,
            trace_id=trace_id,
        )
        return BookingLookupResponse.model_validate(body)

    # ---- shared HTTP plumbing (will grow as more methods land) -------

    def _build_headers(
        self,
        *,
        request_id: str | None,
        trace_id: str | None,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Request-Id": request_id or str(uuid.uuid4()),
        }
        if self._service_token:
            headers["Authorization"] = f"Bearer {self._service_token}"
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _post(
        self,
        path: str,
        *,
        json_body: dict[str, Any],
        request_id: str | None,
        trace_id: str | None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = self._build_headers(
            request_id=request_id,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
        )
        return self._request_with_retry("POST", path, headers=headers, json=json_body)

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_transport_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.request(
                    method, path, headers=headers, json=json, params=params
                )
            except httpx.TransportError as exc:
                last_transport_exc = exc
                if attempt >= self._max_retries:
                    break
                self._sleep_backoff(attempt)
                continue

            # Retry on 5xx and 429 only.
            if response.status_code >= 500 or response.status_code == 429:
                if attempt >= self._max_retries:
                    return self._raise_from_response(response)
                self._sleep_backoff(attempt)
                continue

            if response.status_code >= 400:
                self._raise_from_response(response)  # always raises

            try:
                return response.json()
            except ValueError as exc:
                raise AirlineApiTransportError(
                    f"Non-JSON 2xx body from {method} {path}: {exc}"
                ) from exc

        raise AirlineApiTransportError(
            f"Transport failure after {self._max_retries + 1} attempts to {method} {path}: {last_transport_exc}"
        )

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        # Bounded exponential backoff — caps small so we stay under the
        # Catapult / chat 30s budget even with retries enabled.
        time.sleep(_RETRY_BACKOFF_BASE_SEC * (2**attempt))

    @staticmethod
    def _raise_from_response(response: httpx.Response) -> Any:
        """Always raises :class:`AirlineApiError` (or transport error
        if the body couldn't be parsed)."""
        try:
            payload = response.json()
        except ValueError:
            raise AirlineApiTransportError(
                f"HTTP {response.status_code} with non-JSON body: {response.text[:200]!r}"
            )
        err = (payload or {}).get("error") or {}
        raise AirlineApiError(
            status_code=response.status_code,
            code=str(err.get("code") or "UNKNOWN_ERROR"),
            message=str(err.get("message") or response.reason_phrase or ""),
            details=err.get("details"),
        )
