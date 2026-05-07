"""Slice 1 — AviationDomain plugin: tool registration and dispatch."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domains.aviation.api_client import AirlineApiClient
from app.domains.aviation.plugin import AviationDomain
from tools.aviation_mock.app import app as mock_app


@pytest.fixture
def domain() -> AviationDomain:
    """AviationDomain wired to the in-process mock backend via TestClient."""
    test_http = TestClient(mock_app, base_url="http://mock-airline.test")
    api_client = AirlineApiClient(
        base_url="http://mock-airline.test",
        service_token="test-token-123",
        http_client=test_http,
        max_retries=0,
    )
    return AviationDomain(api_client=api_client)


def test_domain_metadata():
    d = AviationDomain()
    assert d.name == "aviation"
    assert d.agent_keys == ["aviation"]


def test_tools_includes_retrieve_booking():
    d = AviationDomain()
    tool_names = d.tool_names()
    assert "retrieve_booking" in tool_names


def test_retrieve_booking_tool_schema_is_valid_jsonschema_subset():
    d = AviationDomain()
    [tool] = [t for t in d.tools() if t.name == "retrieve_booking"]
    schema = tool.parameters_schema
    # Sanity-check the OpenAI-function-calling-shaped schema.
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"booking_reference", "last_name"}
    assert "booking_reference" in schema["properties"]
    assert "last_name" in schema["properties"]


def test_dispatch_retrieve_booking_happy_path(domain: AviationDomain):
    result = domain.dispatch_tool(
        "retrieve_booking",
        {"booking_reference": "ABC123", "last_name": "DOE"},
    )
    # Result is the BookingLookupResponse, dumped to a JSON-serializable dict.
    assert isinstance(result, dict)
    assert result["booking_reference"] == "ABC123"
    assert result["status"] == "CONFIRMED"
    assert any(p["first_name"] == "JOHN" for p in result["passengers"])


def test_dispatch_unknown_tool_raises_value_error(domain: AviationDomain):
    with pytest.raises(ValueError, match="Unknown aviation tool"):
        domain.dispatch_tool("unknown_thing", {})


def test_dispatch_invalid_args_raises_validation_error(domain: AviationDomain):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        # missing required last_name
        domain.dispatch_tool("retrieve_booking", {"booking_reference": "ABC123"})
