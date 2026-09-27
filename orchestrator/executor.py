import os
import re
import asyncio
import time
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from orchestrator.registry import get_server_command

_TIMEOUT     = float(os.getenv("STEP_TIMEOUT_SEC",   "30"))
_MAX_RETRIES = int(os.getenv("STEP_MAX_RETRIES",     "3"))


def _resolve_params(params: dict, results: dict) -> dict:
    resolved = {}
    for key, value in params.items():
        if isinstance(value, str):
            resolved[key] = re.sub(
                r"\{\{step_(\w+)\.result\}\}",
                lambda m: str(results.get(m.group(1), m.group(0))),
                value,
            )
        else:
            resolved[key] = value
    return resolved


async def _call_tool(server_name: str, tool_name: str, params: dict) -> str:
    cmd = get_server_command(server_name)
    server_params = StdioServerParameters(command=cmd[0], args=cmd[1:])
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, params)
            if result.content:
                block = result.content[0]
                return block.text if hasattr(block, "text") else str(block)
            return ""


async def _execute_step(step: dict, results: dict, on_event=None) -> dict:
    server = step["server"]
    tool   = step["tool"]
    if "." in tool:
        tool = tool.split(".", 1)[1]

    entry = {
        "id":          step["id"],
        "server":      server,
        "tool":        tool,
        "description": step.get("description", ""),
        "depends_on":  step.get("depends_on", []),
        "status":      "running",
    }

    t0 = time.time()
    entry["started_at"] = t0

    if on_event:
        await on_event({"type": "step_update", **entry})

    if server == "error":
        entry.update(status="error", error=step.get("description", "Tool unavailable"), duration_ms=0)
        if on_event:
            await on_event({"type": "step_update", **entry})
        return entry

    params = _resolve_params(step.get("params", {}), results)
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            result = await asyncio.wait_for(
                _call_tool(server, tool, params),
                timeout=_TIMEOUT,
            )
            results[step["id"]] = result
            entry.update(status="done", result=result)
            last_exc = None
            break
        except asyncio.TimeoutError:
            entry.update(
                status="error",
                error=f"Timed out after {_TIMEOUT:.0f}s",
            )
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)   # 1 s, 2 s, …

    if last_exc is not None:
        entry.update(
            status="error",
            error=f"Failed after {_MAX_RETRIES} attempt(s): {last_exc}",
        )

    entry["duration_ms"] = round((time.time() - t0) * 1000)
    if on_event:
        await on_event({"type": "step_update", **entry})
    return entry


async def execute(plan: dict, on_event=None) -> list[dict]:
    steps = plan.get("steps", [])
    if not steps:
        return []

    step_map   = {s["id"]: s for s in steps}
    dep_map    = {s["id"]: set(s.get("depends_on", [])) for s in steps}
    completed: set[str] = set()
    results:   dict[str, str] = {}
    log:       list[dict] = []
    failed:    set[str] = set()

    while len(completed) + len(failed) < len(steps):
        already_scheduled = {e["id"] for e in log}
        ready = [
            sid for sid, deps in dep_map.items()
            if sid not in already_scheduled
            and deps.issubset(completed)
            and not (deps & failed)
        ]

        if not ready:
            for sid in step_map:
                if sid not in already_scheduled:
                    s = step_map[sid]
                    entry = {
                        "id":          sid,
                        "server":      s["server"],
                        "tool":        s["tool"],
                        "description": s.get("description", ""),
                        "depends_on":  s.get("depends_on", []),
                        "status":      "skipped",
                        "error":       "Skipped because a dependency failed",
                    }
                    log.append(entry)
                    failed.add(sid)
                    if on_event:
                        await on_event({"type": "step_update", **entry})
            break

        wave  = len({e.get("parallel_group", 0) for e in log})
        tasks = [_execute_step(step_map[sid], results, on_event) for sid in ready]
        entries = await asyncio.gather(*tasks)

        for entry in entries:
            entry["parallel_group"] = wave
            log.append(entry)
            if entry["status"] == "done":
                completed.add(entry["id"])
            else:
                failed.add(entry["id"])

    return log
