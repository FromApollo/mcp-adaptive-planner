import os
import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp.server import FastMCP
from mcp_servers.common import tool_errors
from orchestrator.errors import AppError

load_dotenv()

mcp = FastMCP("websearch")


def _api_key() -> str:
    key = os.getenv("TAVILY_API_KEY")
    if not key or key == "your_tavily_key_here":
        raise AppError("configuration_error", "TAVILY_API_KEY must be configured on the server.")
    return key


@mcp.tool()
@tool_errors
def search(query: str, max_results: int = 5) -> str:
    """Search the web using Tavily API. Returns titles, URLs and text snippets."""
    if not query.strip() or not 1 <= max_results <= 20:
        raise AppError("invalid_arguments", "A nonempty query and 1 to 20 results are required.")
    with httpx.Client(timeout=15) as client:
        resp = client.post(
            "https://api.tavily.com/search",
            json={"api_key": _api_key(), "query": query, "max_results": max_results},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    if not results:
        return "No results found."

    lines = []
    for r in results:
        snippet = r.get("content", "")[:300]
        lines.append(f"**{r['title']}**\n{r['url']}\n{snippet}")
    return "\n\n".join(lines)


@mcp.tool()
@tool_errors
def get_page_text(url: str) -> str:
    """Extract the full text content from a web page URL using Tavily."""
    with httpx.Client(timeout=20) as client:
        resp = client.post(
            "https://api.tavily.com/extract",
            json={"api_key": _api_key(), "urls": [url]},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    if not results or not results[0].get("raw_content"):
        raise AppError("extraction_failed", "The service could not extract content from this page.")
    return results[0]["raw_content"][:4000]


if __name__ == "__main__":
    mcp.run()
