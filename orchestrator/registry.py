import sys
import asyncio
import os
import json
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from orchestrator.errors import classify_error
from orchestrator.logging_config import get_logger

logger = get_logger("registry")

SERVERS: dict[str, str] = {
    "filesystem": "mcp_servers.filesystem.server",
    "notes": "mcp_servers.notes.server",
    "reports": "mcp_servers.reports.server",
    "sysinfo": "mcp_servers.sysinfo.server",
    "email": "mcp_servers.email.server",
    "websearch": "mcp_servers.websearch.server",
}


def get_server_command(server_name: str) -> list[str]:
    module = SERVERS[server_name]
    return [sys.executable, "-m", module]


async def _list_tools(server_name: str) -> list[dict]:
    cmd = get_server_command(server_name)
    server_params = StdioServerParameters(command=cmd[0], args=cmd[1:])
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [
                    {
                        "server": server_name,
                        "name": t.name,
                        "description": t.description or "",
                        "input_schema": t.inputSchema,
                    }
                    for t in result.tools
                ]
    except Exception as exc:
        error = classify_error(exc)
        logger.warning("discovery_failed", extra={"server": server_name, "code": error.code})
        return [{"server": server_name, "error": str(error), "error_code": error.code}]


async def discover_all_tools() -> list[dict]:
    async def bounded(name):
        try:
            return await asyncio.wait_for(_list_tools(name), float(os.getenv("DISCOVERY_TIMEOUT_SEC", "20")))
        except TimeoutError:
            logger.warning("discovery_timeout", extra={"server": name, "code": "timeout"})
            return [{"server": name, "error": "Tool discovery timed out.", "error_code": "timeout"}]

    tasks = [bounded(name) for name in SERVERS]
    results = await asyncio.gather(*tasks)
    tools = []
    for server_tools in results:
        tools.extend(server_tools)
    logger.info("discovery_finished", extra={"count": sum("error" not in t for t in tools)})
    return tools


def build_tools_description(tools: list[dict]) -> str:
    lines = ["Available tools:"]
    for t in tools:
        if "error" in t:
            lines.append(f"  [WARNING] {t['server']} failed to load: {t['error']}")
            continue
        schema = json.dumps(t.get("input_schema", {}), ensure_ascii=False)
        lines.append(f"  - {t['server']}.{t['name']} — {t['description']}\n    Input schema: {schema}")
    return "\n".join(lines)
