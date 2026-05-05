"""Catapult adapter service entry point.

A separate FastAPI app from ``app.main`` — the two run as independent
processes. This module owns ALL runtime concerns of the adapter:

    - the FastAPI app instance
    - the three Catapult operation endpoints (ask, search, ingest)
    - health endpoints (status, readiness, liveness)
    - structured-error responses

Nothing inside ``app/`` imports from here. The dependency is one-way:
this service imports a few public helpers from ``app.services.*`` and
``app.db.*`` and otherwise leaves the main app alone.
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from catapult_adapter.service import headers as catapult_headers
from catapult_adapter.service.models import (
    AskRequest,
    AskResponse,
    IngestRequest,
    IngestResponse,
    SearchRequest,
    SearchResponse,
    StructuredError,
)
from catapult_adapter.service.operations import ask as ask_op
from catapult_adapter.service.operations import ingest as ingest_op
from catapult_adapter.service.operations import search as search_op


logger = logging.getLogger("catapult_adapter")

TOOL_VERSION = "1.0.0"
BASE_PATH = "/tools/rag-chatbot"


app = FastAPI(
    title="RAG Chatbot — Catapult Adapter",
    version=TOOL_VERSION,
    description=(
        "Catapult-compatible HTTP service that exposes the Enterprise Drive "
        "Chatbot's retrieval-augmented Q&A as the rag-chatbot tool."
    ),
)


# ---------- structured error handler --------------------------------------


@app.exception_handler(HTTPException)
async def _structured_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
    """Wrap FastAPI HTTPExceptions in Catapult's {error: {code, message}} shape."""
    code_map = {
        400: "INVALID_REQUEST",
        404: "NOT_FOUND",
        409: "CONFLICT",
        503: "DEPENDENCY_UNAVAILABLE",
    }
    code = code_map.get(exc.status_code, "INTERNAL_ERROR")
    body = StructuredError(code=code, message=str(exc.detail)).model_dump()
    return JSONResponse(status_code=exc.status_code, content={"error": body})


# ---------- health endpoints ----------------------------------------------


@app.get(f"{BASE_PATH}/health")
def health() -> dict:
    """Overall tool health. Cheap — does not touch DB or external APIs."""
    return {"status": "ok", "version": TOOL_VERSION}


@app.get(f"{BASE_PATH}/health/ready")
def readiness() -> dict:
    """Readiness — confirms the database is reachable.

    Per submission guide §8: a connection ping is sufficient; do NOT run
    expensive operations like a real vector search here.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"ready": True}
    except Exception as e:
        logger.warning("readiness_check_failed error=%s", e)
        raise HTTPException(status_code=503, detail=f"Database unreachable: {e}")
    finally:
        db.close()


@app.get(f"{BASE_PATH}/health/live")
def liveness() -> dict:
    """Liveness — the process is up. Used by container orchestrators."""
    return {"alive": True}


# ---------- operations ----------------------------------------------------


@app.post(f"{BASE_PATH}/ask", response_model=AskResponse)
def ask(
    body: AskRequest,
    ctx: catapult_headers.CatapultContext = Depends(catapult_headers.resolve_context),
) -> AskResponse:
    return ask_op.run(body, ctx)


@app.post(f"{BASE_PATH}/search", response_model=SearchResponse)
def search(
    body: SearchRequest,
    ctx: catapult_headers.CatapultContext = Depends(catapult_headers.resolve_context),
) -> SearchResponse:
    return search_op.run(body, ctx)


@app.post(f"{BASE_PATH}/ingest", response_model=IngestResponse)
def ingest(
    body: IngestRequest,
    ctx: catapult_headers.CatapultContext = Depends(catapult_headers.resolve_context),
) -> IngestResponse:
    return ingest_op.run(body, ctx)
