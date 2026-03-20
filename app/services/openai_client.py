"""OpenAI client: embeddings and chat. Uses app.core.config and structured logging."""
from __future__ import annotations

import logging
from typing import List

from openai import OpenAI

from app.agents.prompts import get_agent
from app.core.config import settings
from app.core.logging import log_operation

logger = logging.getLogger(__name__)

# Lazy client so app starts even if key is missing until first OpenAI call
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not set")
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def _extract_chat_text(response: object) -> str:
    """Parse chat response robustly for different OpenAI API response shapes."""
    try:
        # Responses API (new): response has .output list with content items
        if hasattr(response, "output") and response.output:
            first = response.output[0]
            if hasattr(first, "content") and first.content:
                part = first.content[0]
                if hasattr(part, "text"):
                    return str(part.text).strip()
        # Legacy / direct attribute
        if hasattr(response, "output_text"):
            return str(response.output_text).strip()
        if hasattr(response, "choices") and response.choices:
            c = response.choices[0]
            if hasattr(c, "message") and getattr(c.message, "content", None):
                return str(c.message.content).strip()
    except (IndexError, KeyError, TypeError, AttributeError) as e:
        logger.warning("openai_response_parse_failed error=%s", e)
    return ""


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a batch of texts. Returns list of embedding vectors."""
    if not texts:
        return []
    try:
        client = _get_client()
        resp = client.embeddings.create(
            model=settings.OPENAI_EMBEDDING_MODEL,
            input=texts,
        )
        out = [item.embedding for item in resp.data]
        log_operation(logger, "embed_batch", count=len(out), model=settings.OPENAI_EMBEDDING_MODEL)
        return out
    except Exception:
        logger.exception("embed_texts_failed input_count=%s", len(texts))
        raise


def chat_with_context(
    question: str,
    context_chunks: list[str],
    agent_type: str = "general",
    history: str = "",
) -> str:
    """Answer using provided context chunks and agent config."""
    agent = get_agent(agent_type)
    context = "\n\n---\n\n".join((context_chunks or [])[:12])
    prompt = f"""
SYSTEM:
{agent.system_prompt}

OUTPUT FORMAT:
{agent.output_format}

CHAT HISTORY:
{history}

CONTEXT:
{context}

USER QUESTION:
{question}
""".strip()
    try:
        client = _get_client()
        resp = client.responses.create(
            model=settings.OPENAI_CHAT_MODEL,
            input=prompt,
        )
        text = _extract_chat_text(resp)
        if not text:
            text = "[No response text returned.]"
        return text
    except Exception:
        logger.exception("chat_with_context_failed agent_type=%s", agent_type)
        raise


def chat_without_context(
    question: str,
    agent_type: str = "general",
    history: str = "",
) -> str:
    """Answer without KB context (fallback when retrieval is low confidence)."""
    agent = get_agent(agent_type)
    prompt = f"""
SYSTEM:
{agent.system_prompt}

OUTPUT FORMAT:
{agent.output_format}

CHAT HISTORY:
{history}

USER QUESTION:
{question}

Rules:
- This answer is not from internal documents unless explicitly supported by context.
- If you do not know, say so.
""".strip()
    try:
        client = _get_client()
        resp = client.responses.create(
            model=settings.OPENAI_CHAT_MODEL,
            input=prompt,
        )
        text = _extract_chat_text(resp)
        if not text:
            text = "[No response text returned.]"
        return text
    except Exception:
        logger.exception("chat_without_context_failed agent_type=%s", agent_type)
        raise
