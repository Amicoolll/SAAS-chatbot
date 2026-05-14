"""Chip-action handler for ``POST /chat_pg``.

Lives next to ``chat_pg.py`` (its only caller) and is purely additive — when
the request has no ``action`` field, this module is never imported.

The flow:

1. Caller (``chat_pg.chat_pg``) has already validated the conversation.
2. We resolve a :class:`DomainPlugin` for ``req.agent_type`` and look up the
   tool named by ``req.action``.
3. For each required tool parameter:
   * if present in ``action_params`` and valid → keep
   * if present but invalid → ask the LLM to extract a clean value from
     it (smart validation); on failure re-prompt the user with a hint
   * if missing → return ``action_collecting`` with the param's prompt
4. When every required field is collected, dispatch the tool. Errors map
   to friendly messages with stable ``error_code`` values.

Stateless: the frontend echoes ``action_state`` back on every turn. We
don't persist a server-side FSM.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models_chat import Conversation, Message
from app.domains.aviation.api_client import AirlineApiError, AirlineApiTransportError
from app.domains.base import DomainPlugin, ToolSpec
from app.domains.registry import get_domain_for_agent
from app.services.openai_client import extract_param, extract_params, matches_schema


if TYPE_CHECKING:
    from app.api.chat_pg import ChatRequest


logger = logging.getLogger(__name__)


# Per-action hint for how the frontend should render the tool result. Keep
# this small and explicit — unknown actions default to "text".
_RENDER_HINTS: dict[str, str] = {
    "retrieve_booking": "booking_card",
    "flight_status": "flight_status_card",
    "flight_search": "flight_results_card",
}


def handle_action(
    req: "ChatRequest",
    tenant_id: str,
    user_id: str,
    conv: Conversation,
    db: Session,
) -> dict[str, Any]:
    """Entry point for chip-driven calls. Returns the response dict the
    endpoint should send back. Never returns ``None``.
    """
    domain = get_domain_for_agent(req.agent_type)
    if domain is None:
        raise HTTPException(
            status_code=422,
            detail=f"No domain plugin handles agent_type={req.agent_type!r}.",
        )

    if req.action not in domain.tool_names():
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown action {req.action!r} for domain {domain.name!r}. "
                f"Known actions: {sorted(domain.tool_names())}"
            ),
        )

    tool = next(t for t in domain.tools() if t.name == req.action)
    properties: dict[str, Any] = tool.parameters_schema.get("properties", {})
    required: list[str] = tool.parameters_schema.get("required", [])
    collected: dict[str, Any] = dict(req.action_params or {})
    just_filled: dict[str, Any] = {}

    # 1) Multi-field extraction from user_input (the "any-order" path).
    #    When the user types e.g. "Doe ABC123" in the chat box, the
    #    frontend forwards that as user_input; the LLM here splits it
    #    across all known fields (required + optional). Optional fields
    #    are NEVER asked for, but if the user volunteers them — e.g.
    #    *"DEL BOM 2026-06-01 returning 2026-06-08"* — we extract them
    #    so the search can run as round-trip.
    #
    #    Skipped when nothing is missing (no LLM waste).
    user_input = (req.user_input or "").strip()
    extractable = [f for f in properties.keys() if f not in collected]
    if user_input and extractable:
        field_schemas = {f: properties.get(f, {}) for f in extractable}
        extracted_multi = extract_params(user_input, field_schemas)
        for field, value in extracted_multi.items():
            collected[field] = value
            # Only acknowledge required fields by name in the next prompt;
            # optional fields are silent ("by the way I noted return_date")
            # would feel chatty.
            if field in required:
                just_filled[field] = value

    # 2) Smart validation: any required param that's present but doesn't
    #    match its schema gets one shot at single-field LLM extraction.
    for field in required:
        if field not in collected:
            continue
        prop_schema = properties.get(field, {})
        value = collected[field]
        if matches_schema(value, prop_schema):
            continue

        extracted = extract_param(str(value), field, prop_schema)
        if extracted is None:
            # Drop the bad value so the next loop asks for it again.
            collected.pop(field, None)
            just_filled.pop(field, None)
            return _action_collecting(
                req=req,
                tool=tool,
                missing=field,
                collected=collected,
                just_filled=just_filled,
                hint=f"That doesn't look like a valid {_humanize(field)}. ",
                tenant_id=tenant_id,
                user_id=user_id,
                db=db,
            )
        collected[field] = extracted
        # The smart-validated value isn't an "acknowledgeable" new fill —
        # the user already supplied it; we just cleaned it up. Don't
        # add to just_filled.

    # 3) Walk required fields in declared order; ask for the first missing one.
    for field in required:
        if field not in collected:
            return _action_collecting(
                req=req,
                tool=tool,
                missing=field,
                collected=collected,
                just_filled=just_filled,
                hint=None,
                tenant_id=tenant_id,
                user_id=user_id,
                db=db,
            )

    # 4) All required params present and valid → dispatch.
    return _dispatch_and_respond(
        req=req,
        domain=domain,
        tool=tool,
        collected=collected,
        conv=conv,
        tenant_id=tenant_id,
        user_id=user_id,
        db=db,
    )


# ---- response builders ---------------------------------------------------


def _action_collecting(
    *,
    req: "ChatRequest",
    tool: ToolSpec,
    missing: str,
    collected: dict[str, Any],
    just_filled: dict[str, Any] | None,
    hint: str | None,
    tenant_id: str,
    user_id: str,
    db: Session,
) -> dict[str, Any]:
    """Tell the frontend to collect ``missing`` from the user.

    Three branches for the assistant's text:

    1. First turn (nothing collected, no hint, no fields just filled) →
       the tool's ``intro_prompt`` if it has one (warm combined opener).
       Falls back to the per-field prompt otherwise.
    2. ``just_filled`` is non-empty → acknowledge what was just collected
       (e.g. *"Thanks! Got the PNR (ABC123)."*) then ask for the next
       missing field.
    3. ``hint`` is set (smart-validation re-prompt) → prepend the hint to
       the per-field prompt.
    """
    properties = tool.parameters_schema.get("properties", {})
    prop_schema = properties.get(missing, {})
    intro = tool.parameters_schema.get("intro_prompt")
    per_field_prompt = prop_schema.get(
        "prompt",
        f"Please share the {_humanize(missing)}.",
    )

    if just_filled:
        ack = _build_ack(just_filled, properties)
        answer = f"{ack} {per_field_prompt}".strip()
    elif hint:
        answer = f"{hint}{per_field_prompt}"
    elif not collected and intro:
        answer = intro
    else:
        answer = per_field_prompt

    _save_assistant_message(
        db, tenant_id=tenant_id, user_id=user_id,
        conversation_id=req.conversation_id, content=answer,
    )

    return {
        "mode": "action_collecting",
        "source_type": "none",
        "answer": answer,
        "missing_param": missing,
        "param_schema": _public_schema(prop_schema),
        "action_state": {
            "action": req.action,
            "collected": collected,
            "complete": False,
        },
        "sources": [],
    }


def _dispatch_and_respond(
    *,
    req: "ChatRequest",
    domain: DomainPlugin,
    tool: ToolSpec,
    collected: dict[str, Any],
    conv: Conversation,
    tenant_id: str,
    user_id: str,
    db: Session,
) -> dict[str, Any]:
    """Run the tool. Map success / error to response shapes."""
    try:
        result = domain.dispatch_tool(req.action, collected)
    except AirlineApiError as e:
        return _tool_error(
            req=req, error=e, tenant_id=tenant_id, user_id=user_id, db=db,
        )
    except AirlineApiTransportError as e:
        logger.exception(
            "chip_action_transport_error action=%s tenant=%s",
            req.action, tenant_id,
        )
        return _tool_error(
            req=req,
            error=AirlineApiError(
                status_code=503,
                code="DEPENDENCY_UNAVAILABLE",
                message=str(e) or "Upstream service unavailable.",
            ),
            tenant_id=tenant_id,
            user_id=user_id,
            db=db,
        )
    except ValueError as e:
        # Pydantic-style validation error from inside dispatch_tool —
        # bubble up as a 422 so the client knows it's their fault.
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception(
            "chip_action_unexpected_error action=%s tenant=%s",
            req.action, tenant_id,
        )
        return _tool_error(
            req=req,
            error=AirlineApiError(
                status_code=500,
                code="INTERNAL_ERROR",
                message=str(e) or "Internal error.",
            ),
            tenant_id=tenant_id,
            user_id=user_id,
            db=db,
        )

    summary = _summary_for(req.action, result)
    _save_assistant_message(
        db, tenant_id=tenant_id, user_id=user_id,
        conversation_id=req.conversation_id, content=summary,
    )
    if conv.title == "New chat":
        conv.title = _humanize(req.action).title()
        db.commit()

    return {
        "mode": "tool_executed",
        "source_type": "tool",
        "answer": summary,
        "tool_name": req.action,
        "tool_result": result,
        "render_as": _RENDER_HINTS.get(req.action, "text"),
        "action_state": {
            "action": req.action,
            "collected": collected,
            "complete": True,
        },
        "sources": [],
    }


# Per-action error copy. Indexed by (action_name, status_code). Falls back
# to action-agnostic generic copy below for any (action, status) not in
# this table — keeps adding new tools cheap (no error copy required to
# ship a new slice; the generic phrasing is acceptable).
_ERROR_MESSAGES: dict[tuple[str, int], str] = {
    ("retrieve_booking", 404): (
        "We couldn't find that booking. Please double-check the reference "
        "and last name, then try again."
    ),
    ("retrieve_booking", 403): (
        "We couldn't verify that booking with the details provided. "
        "Please check and try again."
    ),
    ("flight_status", 404): (
        "We couldn't find that flight on the date you specified. "
        "Please double-check the flight number and date and try again."
    ),
    ("flight_search", 404): (
        "We couldn't find any flights for that route on that date. "
        "Try a different date or check the airport codes."
    ),
}

_GENERIC_ERROR_MESSAGES: dict[int, str] = {
    404: "We couldn't find what you asked about. Please double-check the details and try again.",
    403: "We couldn't verify the details you provided. Please check and try again.",
    401: "We couldn't reach the upstream system right now. Please try again shortly.",
    503: "The upstream system is temporarily unavailable. Please try again shortly.",
}


def _tool_error(
    *,
    req: "ChatRequest",
    error: AirlineApiError,
    tenant_id: str,
    user_id: str,
    db: Session,
) -> dict[str, Any]:
    """Friendly error message + structured error_code for the frontend.

    Tries action-specific copy first (``_ERROR_MESSAGES``), then a generic
    fallback by status code, then a last-ditch one-liner. Either way the
    raw ``error_code`` / ``error_status`` are returned for telemetry.
    """
    action_status = (req.action or "", error.status_code)
    if action_status in _ERROR_MESSAGES:
        answer = _ERROR_MESSAGES[action_status]
    elif (
        error.status_code == 503 or error.code == "DEPENDENCY_UNAVAILABLE"
    ):
        answer = _GENERIC_ERROR_MESSAGES[503]
    elif error.status_code in _GENERIC_ERROR_MESSAGES:
        answer = _GENERIC_ERROR_MESSAGES[error.status_code]
    else:
        answer = "Sorry — something went wrong. Please try again shortly."

    _save_assistant_message(
        db, tenant_id=tenant_id, user_id=user_id,
        conversation_id=req.conversation_id, content=answer,
    )

    return {
        "mode": "tool_error",
        "source_type": "none",
        "answer": answer,
        "error_code": error.code,
        "error_status": error.status_code,
        "tool_name": req.action,
        "sources": [],
    }


# ---- small helpers -------------------------------------------------------


def _save_assistant_message(
    db: Session,
    *,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    content: str,
) -> None:
    db.add(
        Message(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            role="assistant",
            content=content,
        )
    )
    db.commit()


def _humanize(field_name: str) -> str:
    """``booking_reference`` → ``booking reference``"""
    return field_name.replace("_", " ")


def _build_ack(just_filled: dict[str, Any], properties: dict[str, Any]) -> str:
    """Build a friendly acknowledgement of fields filled this turn.

    Examples:
        _build_ack({"booking_reference": "ABC123"}, props)
            → "Thanks! Got the PNR (ABC123)."

        _build_ack({"booking_reference": "ABC123", "last_name": "Doe"}, props)
            → "Thanks! Got the PNR (ABC123) and last name (Doe)."

    Uses each property's ``label`` field for the human-readable name;
    falls back to the humanized field name.
    """
    parts: list[str] = []
    for field, value in just_filled.items():
        label = properties.get(field, {}).get("label") or _humanize(field)
        parts.append(f"{label} ({value})")

    if len(parts) == 1:
        body = parts[0]
    elif len(parts) == 2:
        body = f"{parts[0]} and {parts[1]}"
    else:
        body = ", ".join(parts[:-1]) + f", and {parts[-1]}"

    return f"Thanks! Got the {body}."


def _public_schema(prop_schema: dict[str, Any]) -> dict[str, Any]:
    """Strip non-public extension keys from a property schema before
    returning it to the frontend (``prompt`` is internal copy)."""
    return {k: v for k, v in prop_schema.items() if k != "prompt"}


def _summary_for(action: str, result: dict[str, Any]) -> str:
    """Short human-readable summary for the assistant message bubble.
    Frontend renders the rich card from ``tool_result``; this string is
    only what gets stored in conversation history.
    """
    if action == "retrieve_booking":
        ref = result.get("booking_reference", "?")
        status = result.get("status", "?")
        passengers = result.get("passengers", []) or []
        names = ", ".join(
            f"{p.get('first_name','')} {p.get('last_name','')}".strip()
            for p in passengers[:3]
        )
        return (
            f"Booking {ref} — {status.lower()}"
            + (f"; passengers: {names}" if names else "")
            + "."
        )
    if action == "flight_status":
        fn = result.get("flight_number", "?")
        st = (result.get("status") or "?").replace("_", " ").lower()
        delay = result.get("delay_minutes") or 0
        delay_text = f" by {delay} min" if delay else ""
        gate = result.get("gate")
        gate_text = f", gate {gate}" if gate else ""
        return f"Flight {fn} — {st}{delay_text}{gate_text}."
    if action == "flight_search":
        results = result.get("results") or []
        n = len(results)
        if n == 0:
            return "No flights found for that route and date."
        first = results[0]
        ob = first.get("outbound_segments") or [{}]
        rb = first.get("return_segments") or []
        origin = ob[0].get("origin", "?")
        dest = ob[-1].get("destination", "?")
        trip_type = "round trip" if rb else "one-way"
        arrow = "↔" if rb else "→"
        fare = first.get("fare") or {}
        cheapest = min(
            (r.get("fare", {}).get("total_amount", 0) for r in results),
            default=0,
        )
        currency = fare.get("currency", "")
        return (
            f"Found {n} {trip_type} option{'s' if n != 1 else ''} "
            f"{origin} {arrow} {dest}. Cheapest: {currency} {cheapest:g}."
        )
    return f"{_humanize(action).title()} completed."
