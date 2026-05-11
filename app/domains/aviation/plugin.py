"""Aviation domain plugin — registers tools the LLM can call.

Slice 1 exposes a single tool: ``retrieve_booking``. Subsequent slices
register the rest. The shape of this file should stay flat — one tool
spec + one dispatch case per partner-API endpoint.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.core.config import settings
from app.domains.aviation.api_client import AirlineApiClient
from app.domains.aviation.models import BookingLookupRequest
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
        return [_TOOL_RETRIEVE_BOOKING]

    def dispatch_tool(
        self, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        if tool_name == _TOOL_RETRIEVE_BOOKING.name:
            req = BookingLookupRequest(**args)
            resp = self.api_client.lookup_booking(req)
            return resp.model_dump(mode="json")
        raise ValueError(f"Unknown aviation tool: {tool_name!r}")
