import asyncio
import os
import time

from jsonschema import Draft202012Validator
from mcp import ClientSession, types
from mcp.client.stdio import stdio_client, StdioServerParameters

from orchestrator.errors import AppError, classify_error, tool_result_error
from orchestrator.logging_config import get_logger, step_id, error_details
from orchestrator.registry import get_server_command
from orchestrator.validation import REFERENCE, validate_plan

logger = get_logger("executor")
_TIMEOUT = float(os.getenv("STEP_TIMEOUT_SEC", "30"))
_MAX_RETRIES = max(1, int(os.getenv("STEP_MAX_RETRIES", "3")))
# A lost response after a write/send may mean it succeeded. Retry only reads.
_READ_ONLY = {
    ("filesystem", "read_file"), ("filesystem", "list_dir"),
    ("notes", "get_note"), ("notes", "search_notes"), ("notes", "list_notes"),
    ("websearch", "search"), ("websearch", "get_page_text"),
    *(("sysinfo", name) for name in (
        "get_system_info", "get_cpu", "get_memory", "get_disk", "get_top_processes")),
}


def _resolve_params(value, results: dict):
    if isinstance(value, dict):
        return {k: _resolve_params(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_params(v, results) for v in value]
    if not isinstance(value, str):
        return value

    def get(match):
        sid = match.group(1)
        if sid not in results:
            raise AppError("missing_result", f"Step '{sid}' has no successful result.")
        return results[sid]

    match = REFERENCE.fullmatch(value)
    if match:
        return get(match)
    return REFERENCE.sub(lambda m: str(get(m)), value)


async def _call_tool(server_name: str, tool_name: str, params: dict, request_input=None):
    try:
        cmd = get_server_command(server_name)
    except KeyError:
        raise AppError("tool_unavailable", "The requested MCP server is unavailable.") from None

    input_error = None

    async def elicit(context, request):
        nonlocal input_error
        try:
            if request_input is None:
                raise AppError("elicitation_required", "This tool requires interactive input; run it over WebSocket.")
            if getattr(request, "mode", "form") != "form":
                raise AppError("unsupported_elicitation", "Only form elicitation is supported.")
            content = await request_input(request.message, request.requestedSchema)
            return types.ElicitResult(action="accept", content=content)
        except AppError as exc:
            # The SDK converts callback exceptions to protocol errors and can lose
            # their code. Send the MCP action and retain the precise local failure.
            input_error = exc
            return types.ElicitResult(action="decline" if exc.code == "input_declined" else "cancel")

    server_params = StdioServerParameters(command=cmd[0], args=cmd[1:])
    # Check isError after closing the session to avoid task-group wrapping.
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write, elicitation_callback=elicit) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, params)
    if input_error is not None:
        raise input_error
    if result.isError:
        text = "\n".join(b.text for b in result.content if hasattr(b, "text"))
        raise tool_result_error(text)
    text_blocks = [b.text for b in result.content if hasattr(b, "text")]
    if text_blocks:
        return "\n".join(text_blocks)
    if result.structuredContent is not None:
        return result.structuredContent
    if result.content:
        raise AppError("unsupported_result", "The tool returned content this executor cannot process.")
    return ""


async def _execute_step(step: dict, results: dict, on_event=None, tools=None, request_input=None) -> dict:
    server, tool = step["server"], step["tool"].split(".")[-1]
    entry = {k: step[k] for k in ("id", "server", "tool", "description", "depends_on") if k in step}
    entry.update(tool=tool, status="running", started_at=time.time(), attempts=0)
    t0 = time.monotonic()
    token = step_id.set(step["id"])

    async def emit():
        if on_event:
            await on_event({"type": "step_update", **entry})

    try:
        await emit()
        logger.info("step_started", extra={"server": server, "tool": tool})
        if server == "error":
            raise AppError("tool_unavailable", step.get("description", "A required tool is unavailable."))
        params = _resolve_params(step.get("params", {}), results)
        if tools is not None:
            schema = next(t["input_schema"] for t in tools
                          if t.get("server") == server and t.get("name") == tool and "error" not in t)
            if not Draft202012Validator(schema).is_valid(params):
                raise AppError("invalid_arguments", "Resolved tool arguments do not match the tool schema.")

        for attempt in range(1, _MAX_RETRIES + 1):
            entry["attempts"] = attempt
            try:
                async with asyncio.timeout(_TIMEOUT) as timer:
                    async def ask(message, schema):
                        if request_input is None:
                            raise AppError("elicitation_required", "This step requires interactive input.")
                        remaining = max(0, timer.when() - asyncio.get_running_loop().time())
                        timer.reschedule(None)
                        entry["status"] = "waiting_input"
                        await emit()
                        try:
                            return await request_input(message, schema)
                        finally:
                            timer.reschedule(asyncio.get_running_loop().time() + remaining)
                            entry["status"] = "running"
                            await emit()

                    result = await _call_tool(server, tool, params, request_input=ask)
                results[step["id"]] = result
                entry.update(status="done", result=result)
                break
            except Exception as exc:
                error = classify_error(exc)
                can_retry = error.retryable and (server, tool) in _READ_ONLY and attempt < _MAX_RETRIES
                logger.warning("step_attempt_failed", extra={
                    "code": error.code, "attempt": attempt, "server": server, "tool": tool,
                    **error_details(exc),
                })
                if not can_retry:
                    raise error from exc
                await asyncio.sleep(2 ** (attempt - 1))
    except asyncio.CancelledError:
        entry.update(status="cancelled", error="Execution cancelled; an in-flight operation may have completed.",
                     error_code="cancelled")
        logger.info("step_cancelled")
        raise
    except Exception as exc:
        error = classify_error(exc)
        entry.update(status="error", error=str(error), error_code=error.code, retryable=error.retryable)
    finally:
        entry["duration_ms"] = round((time.monotonic() - t0) * 1000)
        logger.info("step_finished", extra={"status": entry["status"], "duration_ms": entry["duration_ms"],
                                             "code": entry.get("error_code", "ok")})
        try:
            await emit()
        finally:
            step_id.reset(token)
    return entry


async def execute(plan: dict, on_event=None, *, tools=None, request_input=None) -> list[dict]:
    validate_plan(plan, tools)
    steps = plan["steps"]
    results, terminal, log = {}, {}, []
    wave = 0
    while len(terminal) < len(steps):
        # Propagate failures transitively; independent branches may finish.
        while True:
            skipped = []
            for step in steps:
                sid = step["id"]
                failed = [d for d in step.get("depends_on", []) if d in terminal and terminal[d] != "done"]
                if sid in terminal or not failed:
                    continue
                entry = {k: step[k] for k in ("id", "server", "tool", "description", "depends_on") if k in step}
                entry.update(status="skipped", error_code="dependency_failed", blocked_by=failed,
                             error="Skipped because a dependency failed: " + ", ".join(failed),
                             duration_ms=0, attempts=0, parallel_group=wave)
                terminal[sid] = "skipped"
                log.append(entry)
                skipped.append(entry)
                token = step_id.set(sid)
                logger.info("step_skipped", extra={"code": "dependency_failed"})
                step_id.reset(token)
                if on_event:
                    await on_event({"type": "step_update", **entry})
            if not skipped:
                break
        ready = [s for s in steps if s["id"] not in terminal
                 and all(terminal.get(d) == "done" for d in s.get("depends_on", []))]
        if not ready:
            break
        tasks = [asyncio.create_task(_execute_step(s, results, on_event, tools, request_input)) for s in ready]
        try:
            entries = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for entry in entries:
            entry["parallel_group"] = wave
            terminal[entry["id"]] = entry["status"]
            log.append(entry)
        wave += 1
    return log
