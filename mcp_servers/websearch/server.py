import os
import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp.server import FastMCP

load_dotenv()

mcp = FastMCP("websearch")


def _api_key() -> str:
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        raise ValueError("TAVILY_API_KEY not set in .env")
    return key


@mcp.tool()
def search(query: str, max_results: int = 5) -> str:
    """Search the web using Tavily API. Returns titles, URLs and text snippets."""
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
    if not results:
        return "Could not extract page content."
    return results[0].get("raw_content", "")[:4000]


if __name__ == "__main__":
    mcp.run()
