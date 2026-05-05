"""Pydantic mirrors of the Catapult JSON schemas.

These intentionally duplicate the JSON Schema files in ``schemas/``. The
JSON Schemas are the contract Catapult validates against; these Pydantic
models are what FastAPI uses for request parsing and response serialization
inside the adapter. Keep them in sync — but the JSON Schemas are
authoritative for the platform.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------- shared ---------------------------------------------------------


AgentType = Literal[
    "general",
    "medical",
    "logistics",
    "hr",
    "legal",
    "finance",
    "engineering",
    "sales",
    "support",
    "research",
    "education",
    "marketing",
    "product",
    "operations",
    "document_summarizer",
    "meeting_minutes",
    "rfp_proposal",
    "aviation",
]


SourceType = Literal["documents", "web", "llm", "none"]


Mode = Literal[
    "kb_grounded",
    "web_grounded",
    "llm_fallback",
    "conversational",
]


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class Source(BaseModel):
    name: str
    url: str | None = None


class StructuredError(BaseModel):
    code: str
    message: str


# ---------- ask ------------------------------------------------------------


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    collection_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    agent_type: AgentType = "general"
    conversation_history: list[ConversationTurn] = Field(default_factory=list, max_length=20)
    top_k: int = Field(default=5, ge=1, le=20)
    include_sources: bool = True


class AskUsage(BaseModel):
    retrieval_latency_ms: int = Field(ge=0)
    chunks_retrieved: int | None = None
    embedding_tokens: int | None = None
    generation_tokens: int | None = None


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]
    source_type: SourceType
    mode: Mode | None = None
    usage: AskUsage
    model_used: str | None = None
    message: str | None = None


# ---------- search ---------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    collection_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    top_k: int = Field(default=10, ge=1, le=50)
    use_hybrid: bool | None = None


class SearchResult(BaseModel):
    chunk_text: str
    document_name: str
    distance: float | None = None


class SearchResponse(BaseModel):
    results: list[SearchResult]
    retrieve_mode: Literal["vector", "hybrid"] | None = None
    total_matches: int = 0
    latency_ms: int = Field(ge=0)


# ---------- ingest ---------------------------------------------------------


class IngestDocument(BaseModel):
    document_id: str = Field(min_length=1, max_length=256)
    name: str | None = Field(default=None, max_length=512)
    content: str = Field(min_length=1, max_length=200000)
    mime_type: str = "text/plain"


class IngestRequest(BaseModel):
    collection_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    documents: list[IngestDocument] = Field(min_length=1, max_length=100)
    chunk_size: int = Field(default=1200, ge=100, le=4000)
    chunk_overlap: int = Field(default=200, ge=0, le=500)


class IngestError(BaseModel):
    document_id: str
    error: str


class IngestResponse(BaseModel):
    collection_id: str
    documents_processed: int = Field(ge=0)
    chunks_created: int = Field(ge=0)
    documents_replaced: int = Field(default=0, ge=0)
    errors: list[IngestError] = Field(default_factory=list)
