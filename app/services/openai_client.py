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


def embed_texts(
    texts: List[str],
    *,
    trace_headers: dict[str, str] | None = None,
) -> List[List[float]]:
    """Embed a batch of texts. Returns list of embedding vectors.

    ``trace_headers`` (optional, keyword-only) is forwarded to OpenAI as
    ``extra_headers``. Used by the Catapult adapter to propagate
    ``X-Trace-Id``; when ``None`` (the default for all in-app callers) the
    behavior is identical to before.
    """
    if not texts:
        return []
    try:
        client = _get_client()
        resp = client.embeddings.create(
            model=settings.OPENAI_EMBEDDING_MODEL,
            input=texts,
            extra_headers=trace_headers,
        )
        # API may return rows out of input order; index maps each row back to input.
        ordered = sorted(resp.data, key=lambda item: item.index)
        out = [item.embedding for item in ordered]
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
    *,
    trace_headers: dict[str, str] | None = None,
) -> str:
    """Answer using provided context chunks and agent config.

    ``trace_headers``: see ``embed_texts``. Default ``None`` preserves the
    pre-Catapult behavior for in-app callers.
    """
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
            extra_headers=trace_headers,
        )
        text = _extract_chat_text(resp)
        if not text:
            text = "[No response text returned.]"
        return text
    except Exception:
        logger.exception("chat_with_context_failed agent_type=%s", agent_type)
        raise


def chat_conversational(
    question: str,
    history: str = "",
    agent_type: str = "general",
    *,
    trace_headers: dict[str, str] | None = None,
) -> str:
    """
    Greetings / light chit-chat when KB retrieval is intentionally skipped.

    Avoids the RAG-focused agent prompts (which assume document context and make
    the model say it has \"no context\").

    ``trace_headers``: see ``embed_texts``.
    """
    agent = get_agent(agent_type)
    prompt = f"""
You are {agent.name} in a brief, human-facing turn.

The user sent a short greeting or casual opener. No document passages are attached to this turn—that is intentional.
Reply warmly and professionally in 1–3 short sentences. Acknowledge them and offer help with work or knowledge-base questions.
Do not apologize for lacking context, do not say you cannot access documents in general, and do not ask them to paste context—substantive questions will use documents on later turns.

CHAT HISTORY:
{history}

USER MESSAGE:
{question}
""".strip()
    try:
        client = _get_client()
        resp = client.responses.create(
            model=settings.OPENAI_CHAT_MODEL,
            input=prompt,
            extra_headers=trace_headers,
        )
        text = _extract_chat_text(resp)
        if not text:
            text = "[No response text returned.]"
        return text
    except Exception:
        logger.exception("chat_conversational_failed agent_type=%s", agent_type)
        raise


def chat_with_web_context(
    question: str,
    web_results: list,
    agent_type: str = "general",
    history: str = "",
    *,
    trace_headers: dict[str, str] | None = None,
) -> str:
    """Answer using web search results instead of KB documents.

    ``web_results`` is a list of objects with ``.title``, ``.url``, and
    ``.snippet`` attributes (see ``app.services.web_search.WebResult``).
    The prompt instructs the model to cite sources by number.

    ``trace_headers``: see ``embed_texts``.
    """
    agent = get_agent(agent_type)
    numbered = []
    for i, r in enumerate(web_results[:10], start=1):
        entry = f"[{i}] {r.title}\n{r.snippet}"
        if r.url:
            entry += f"\nSource: {r.url}"
        numbered.append(entry)
    context = "\n\n---\n\n".join(numbered)

    prompt = f"""
SYSTEM:
{agent.system_prompt}

OUTPUT FORMAT:
{agent.output_format}

CHAT HISTORY:
{history}

WEB SEARCH RESULTS:
{context}

USER QUESTION:
{question}

Rules:
- Answer the question using the web search results above.
- Cite sources using [1], [2], etc. markers that match the numbered results.
- If the results do not contain a good answer, say so honestly.
""".strip()
    try:
        client = _get_client()
        resp = client.responses.create(
            model=settings.OPENAI_CHAT_MODEL,
            input=prompt,
            extra_headers=trace_headers,
        )
        text = _extract_chat_text(resp)
        if not text:
            text = "[No response text returned.]"
        return text
    except Exception:
        logger.exception("chat_with_web_context_failed agent_type=%s", agent_type)
        raise


def chat_without_context(
    question: str,
    agent_type: str = "general",
    history: str = "",
    *,
    trace_headers: dict[str, str] | None = None,
) -> str:
    """Answer without KB context (fallback when retrieval is low confidence).

    ``trace_headers``: see ``embed_texts``.
    """
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
            extra_headers=trace_headers,
        )
        text = _extract_chat_text(resp)
        if not text:
            text = "[No response text returned.]"
        return text
    except Exception:
        logger.exception("chat_without_context_failed agent_type=%s", agent_type)
        raise
