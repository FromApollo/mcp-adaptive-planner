import sys
import asyncio
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

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
        return [{"server": server_name, "error": str(exc)}]


async def discover_all_tools() -> list[dict]:
    tasks = [_list_tools(name) for name in SERVERS]
    results = await asyncio.gather(*tasks)
    tools = []
    for server_tools in results:
        tools.extend(server_tools)
    return tools


def build_tools_description(tools: list[dict]) -> str:
    lines = ["Available tools:"]
    for t in tools:
        if "error" in t:
            lines.append(f"  [WARNING] {t['server']} failed to load: {t['error']}")
            continue
        props = t.get("input_schema", {}).get("properties", {})
        params = ", ".join(
            f"{k}: {v.get('type', 'any')}" for k, v in props.items()
        )
        lines.append(f"  - {t['server']}.{t['name']}({params}) — {t['description']}")
    return "\n".join(lines)
