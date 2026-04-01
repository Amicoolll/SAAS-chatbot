"""Structured output for the rule-based query understanding layer."""

from pydantic import BaseModel, Field


class QueryUnderstandingResult(BaseModel):
    """
    Deterministic analysis of a user query (no LLM).
    Used to route retrieval, RAG mode, and safety policies.
    """

    domain: str = Field(description="Detected business domain label.")
    intent: str = Field(description="Detected user intent label.")
    complexity: str = Field(description="simple | medium | complex")
    risk_level: str = Field(description="low | medium | high")
    needs_exact_match: bool = Field(
        default=False,
        description="Query likely needs exact ID/code/number grounding.",
    )
    needs_multi_hop: bool = Field(
        default=False,
        description="Query may need multi-step or multi-document reasoning.",
    )
    needs_live_data: bool = Field(
        default=False,
        description="Query asks for current/live/time-sensitive facts.",
    )
    requires_citations: bool = Field(
        default=False,
        description="Answer should cite sources (policy/medical/legal style).",
    )
