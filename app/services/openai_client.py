"""OpenAI client: embeddings and chat. Uses app.core.config and structured logging."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, List

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


def chat_conversational(
    question: str,
    history: str = "",
    agent_type: str = "general",
) -> str:
    """
    Greetings / light chit-chat when KB retrieval is intentionally skipped.

    Avoids the RAG-focused agent prompts (which assume document context and make
    the model say it has \"no context\").
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
) -> str:
    """Answer using web search results instead of KB documents.

    ``web_results`` is a list of objects with ``.title``, ``.url``, and
    ``.snippet`` attributes (see ``app.services.web_search.WebResult``).
    The prompt instructs the model to cite sources by number.
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
        )
        text = _extract_chat_text(resp)
        if not text:
            text = "[No response text returned.]"
        return text
    except Exception:
        logger.exception("chat_with_web_context_failed agent_type=%s", agent_type)
        raise


def matches_schema(value: Any, prop_schema: dict[str, Any]) -> bool:
    """Tiny JSON Schema subset validator for tool-parameter values.

    Covers what the slice 1+ partner-API tool schemas actually use today:
    string with ``minLength`` / ``maxLength`` / ``pattern`` / ``enum``,
    integer / number with ``minimum`` / ``maximum`` / ``enum``, and
    boolean. Unknown ``type`` values pass through (return True) so future
    schema additions don't silently fail validation — extend this function
    deliberately when those land.
    """
    expected = prop_schema.get("type")

    if expected == "string":
        if not isinstance(value, str):
            return False
        if "minLength" in prop_schema and len(value) < prop_schema["minLength"]:
            return False
        if "maxLength" in prop_schema and len(value) > prop_schema["maxLength"]:
            return False
        if "pattern" in prop_schema:
            if not re.search(prop_schema["pattern"], value):
                return False
        if "enum" in prop_schema and value not in prop_schema["enum"]:
            return False
        return True

    if expected in ("integer", "number"):
        if expected == "integer" and not isinstance(value, int):
            return False
        if expected == "number" and not isinstance(value, (int, float)):
            return False
        if "minimum" in prop_schema and value < prop_schema["minimum"]:
            return False
        if "maximum" in prop_schema and value > prop_schema["maximum"]:
            return False
        if "enum" in prop_schema and value not in prop_schema["enum"]:
            return False
        return True

    if expected == "boolean":
        return isinstance(value, bool)

    return True


def extract_param(
    text: str,
    param_name: str,
    prop_schema: dict[str, Any],
    *,
    trace_headers: dict[str, str] | None = None,
) -> Any | None:
    """Smart-validation helper: ask the LLM to extract a valid value for
    ``param_name`` from messy user input.

    Returns the extracted value (typed: str, int, float, or bool depending
    on the schema's declared type) when it satisfies ``prop_schema``;
    returns ``None`` if the LLM couldn't extract anything sensible. Caller
    decides whether to re-prompt the user.

    Used by the chip flow when the frontend submits a value that fails
    strict schema validation (e.g., user typed *"my pnr is ABC123"* instead
    of just *"ABC123"*). Cheap call (~50-150 tokens). Skipped entirely
    when the original value already validates.
    """
    description = prop_schema.get("description", "").strip()
    schema_json = json.dumps(
        {k: v for k, v in prop_schema.items() if k != "prompt"},
        ensure_ascii=False,
    )

    instruction = f"""
You are extracting a single value from a user's natural-language input.

Field: {param_name}
{f"What it is: {description}" if description else ""}
JSON Schema: {schema_json}

User input: {text!r}

Reply with ONLY the extracted value as a plain string. No quotes, no
prose, no explanation. If you cannot extract a value that satisfies the
schema, reply with the literal word: NONE
""".strip()

    try:
        client = _get_client()
        resp = client.responses.create(
            model=settings.OPENAI_CHAT_MODEL,
            input=instruction,
            extra_headers=trace_headers,
        )
        raw = _extract_chat_text(resp)
    except Exception:
        logger.exception("extract_param_failed param=%s", param_name)
        return None

    candidate = (raw or "").strip().strip("'\"")
    if not candidate or candidate.upper() == "NONE":
        return None

    # Coerce to the declared type when possible, then validate.
    expected = prop_schema.get("type")
    coerced: Any = candidate
    try:
        if expected == "integer":
            coerced = int(candidate)
        elif expected == "number":
            coerced = float(candidate)
        elif expected == "boolean":
            lowered = candidate.lower()
            if lowered in ("true", "yes"):
                coerced = True
            elif lowered in ("false", "no"):
                coerced = False
            else:
                return None
    except ValueError:
        return None

    if not matches_schema(coerced, prop_schema):
        return None
    return coerced


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
