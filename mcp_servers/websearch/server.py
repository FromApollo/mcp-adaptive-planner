import os
import re
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp.server import FastMCP
from mcp_servers.common import tool_errors
from orchestrator.errors import AppError
from mcp_servers.websearch.summarize import summarize

load_dotenv()

mcp = FastMCP("websearch")


def _api_key() -> str:
    key = os.getenv("TAVILY_API_KEY")
    if not key or key == "your_tavily_key_here":
        raise AppError("configuration_error", "TAVILY_API_KEY must be configured on the server.")
    return key


@mcp.tool()
@tool_errors
def search(query: str, max_results: int = 5, include_domains: list[str] | None = None,
           exclude_domains: list[str] | None = None) -> str:
    """Search for query-focused evidence suitable for reports, with source URLs.

    Selects complete, non-redundant sentences from page text (not its first characters).
    include_domains restricts sources, e.g. ['support.microsoft.com']; exclude_domains
    can exclude video/social sites when written guidance is needed. Prefer authoritative
    sources for recommendations. Evidence is quoted, not verified or translated.
    """
    if not query.strip() or not 1 <= max_results <= 20:
        raise AppError("invalid_arguments", "A nonempty query and 1 to 20 results are required.")
    payload = {
        "api_key": _api_key(), "query": query, "max_results": max_results,
        "search_depth": "advanced", "include_raw_content": "markdown",
        "chunks_per_source": 3, "include_answer": False,
    }
    for field, domains in (("include_domains", include_domains), ("exclude_domains", exclude_domains)):
        if domains is not None:
            if not isinstance(domains, list) or any(
                not isinstance(domain, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", domain)
                for domain in domains
            ):
                raise AppError("invalid_arguments", "Domain filters must contain hostnames without paths.")
            payload[field] = domains
    with httpx.Client(timeout=20) as client:
        resp = client.post(
            "https://api.tavily.com/search",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    if not results:
        return "No results found."

    lines, seen = [], set()
    for result in results[:max_results]:
        url = result.get("url", "")
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        if parts.scheme not in ("http", "https") or not parts.netloc:
            continue
        canonical = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))
        if canonical in seen:
            continue
        seen.add(canonical)
        title = result.get("title") or parts.netloc
        raw = result.get("raw_content")
        summary = summarize(raw, query, title=title) if isinstance(raw, str) else ""
        origin = "Отобранные предложения страницы"
        if not summary:
            summary = summarize(result.get("content") or "", query, title=title)
            origin = "Фрагмент поисковой выдачи; текст страницы не использован"
        if not summary:
            continue  # No incomplete snippets or irrelevant filler in reports.
        lines.append(f"### {_plain_title(title)}\n\nИсточник: {url}\n\n{origin}:\n\n{summary}")
    return "\n\n".join(lines) if lines else (
        "Не удалось выделить законченные предложения по запросу из найденных источников. "
        "Уточните запрос или выберите другие источники; сведений для рекомендаций недостаточно."
    )


def _plain_title(title: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>#])", r"\\\1", " ".join(title.split()))


@mcp.tool()
@tool_errors
def get_page_text(url: str, query: str = "", max_chars: int = 4000) -> str:
    """Extract the main points of a page as complete sentences, with its source URL.

    Pass query to focus on the user's topic; without it selects representative content.
    max_chars (200..12000) bounds the evidence text, never cuts words or sentences.
    This returns an extractive summary, not the full page or generated recommendations.
    """
    try:
        parts = urlsplit(url)
        valid_url = parts.scheme in ("http", "https") and bool(parts.netloc)
    except ValueError:
        valid_url = False
    if not valid_url or not 200 <= max_chars <= 12000:
        raise AppError("invalid_arguments", "An HTTP(S) page URL and max_chars from 200 to 12000 are required.")
    with httpx.Client(timeout=20) as client:
        resp = client.post(
            "https://api.tavily.com/extract",
            # No query-side chunk limit: rank the entire extracted page locally.
            json={"api_key": _api_key(), "urls": [url], "format": "markdown"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    if not results or not results[0].get("raw_content"):
        raise AppError("extraction_failed", "The service could not extract content from this page.")
    summary = summarize(results[0]["raw_content"], query, max_chars=max_chars, max_sentences=12)
    if not summary:
        raise AppError("extraction_failed", "The page has no complete relevant sentences within the requested budget.")
    return f"Источник: {url}\n\nОтобранные предложения страницы:\n\n{summary}"


if __name__ == "__main__":
    mcp.run()
