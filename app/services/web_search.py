"""Web search via Tavily API. No external dependency — uses stdlib urllib."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger(__name__)

_TAVILY_SEARCH_URL = "https://api.tavily.com/search"


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str


def search_web(
    query: str,
    max_results: int | None = None,
    timeout: int = 15,
) -> list[WebResult]:
    """Search the web via Tavily. Returns an empty list on any failure so the
    caller can safely fall through to the plain LLM fallback.
    """
    api_key = settings.TAVILY_API_KEY
    if not api_key:
        logger.debug("web_search_skipped reason=no_tavily_api_key")
        return []

    max_results = max_results or settings.WEB_SEARCH_MAX_RESULTS

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "include_answer": True,
        "max_results": max_results,
    }

    try:
        req = urllib.request.Request(
            _TAVILY_SEARCH_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as e:
        logger.warning("web_search_failed query=%s error=%s", query[:80], e)
        return []

    results: list[WebResult] = []
    for item in body.get("results", []):
        title = item.get("title", "")
        url = item.get("url", "")
        snippet = item.get("content", "")
        if url and snippet:
            results.append(WebResult(title=title, url=url, snippet=snippet))

    tavily_answer = body.get("answer")
    if tavily_answer and not results:
        results.append(
            WebResult(title="Tavily Summary", url="", snippet=tavily_answer)
        )

    logger.info(
        "web_search_ok query=%s results=%s", query[:80], len(results)
    )
    return results
