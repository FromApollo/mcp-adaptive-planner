import asyncio
import json
import errno
import smtplib
from unittest.mock import AsyncMock

import pytest
import httpx

from orchestrator import executor
from orchestrator.errors import AppError, classify_error, tool_result_error
from orchestrator.validation import validate_plan
from tests.test_e2e import _plan, _step


@pytest.mark.parametrize("existing", [False, True])
async def test_missing_source_never_creates_or_overwrites_copy(tmp_path, existing):
    target = tmp_path / "copy.txt"
    if existing:
        target.write_text("keep original", encoding="utf-8")
    events = []

    async def event(value):
        events.append(value)

    log = await executor.execute(_plan(
        _step(1, "filesystem", "read_file", {"path": str(tmp_path / "definitely_missing_file.txt")}),
        _step(2, "filesystem", "write_file", {"path": str(target), "content": "{{step_1.result}}"}, ["1"]),
        _step(3, "filesystem", "write_file", {"path": str(tmp_path / "other.txt"), "content": "{{step_2.result}}"}, ["2"]),
    ), on_event=event)
    entries = {e["id"]: e for e in log}
    assert entries["1"]["status"] == "error"
    assert entries["1"]["error_code"] == "not_found"
    assert entries["1"]["attempts"] == 1
    assert "result" not in entries["1"]
    assert entries["2"]["status"] == entries["3"]["status"] == "skipped"
    assert not any(e["status"] == "done" for e in events)
    assert not (tmp_path / "other.txt").exists()
    assert target.read_text(encoding="utf-8") == "keep original" if existing else not target.exists()


@pytest.mark.parametrize("content", ["", "Error: this is legitimate file content", "Текст\nwith two lines"])
async def test_successful_copy_including_empty_and_error_text(tmp_path, content):
    source, target = tmp_path / "source.txt", tmp_path / "copy.txt"
    source.write_text(content, encoding="utf-8")
    log = await executor.execute(_plan(
        _step(1, "filesystem", "read_file", {"path": str(source)}),
        _step(2, "filesystem", "write_file", {"path": str(target), "content": "{{step_1.result}}"}, ["1"]),
    ))
    assert all(e["status"] == "done" for e in log), log
    assert target.read_text(encoding="utf-8") == content


async def test_invalid_encoding_is_not_success(tmp_path):
    source = tmp_path / "binary.dat"
    source.write_bytes(b"\xff\xfe\xfd")
    log = await executor.execute(_plan(_step(1, "filesystem", "read_file", {"path": str(source)})))
    assert log[0]["error_code"] == "invalid_encoding"


async def test_missing_note_blocks_save(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTES_DB", str(tmp_path / "notes.db"))
    target = tmp_path / "note.txt"
    log = await executor.execute(_plan(
        _step(1, "notes", "get_note", {"title": "definitely missing"}),
        _step(2, "filesystem", "write_file", {"path": str(target), "content": "{{step_1.result}}"}, ["1"]),
    ))
    assert log[0]["error_code"] == "not_found"
    assert log[1]["status"] == "skipped"
    assert not target.exists()


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(steps=[]),
    lambda p: p["steps"].append(dict(p["steps"][0])),
    lambda p: p["steps"][0].update(depends_on=["missing"]),
    lambda p: p["steps"][0].update(depends_on=["1"]),
    lambda p: p["steps"][0].update(params=[]),
    lambda p: p["steps"][0].update(params={"content": {"nested": ["{{step_2.result}}"]}}),
    lambda p: p["steps"][0].update(params={"content": "{{step_2.missing}}"}),
])
async def test_invalid_plan_rejected_before_side_effects(mutation, monkeypatch):
    call = AsyncMock()
    monkeypatch.setattr(executor, "_call_tool", call)
    task_plan = _plan(_step(1, "filesystem", "write_file", {"path": "unused", "content": "x"}))
    mutation(task_plan)
    with pytest.raises(AppError) as exc:
        await executor.execute(task_plan)
    assert exc.value.code == "invalid_plan"
    call.assert_not_called()


def test_cycle_and_undeclared_reference():
    with pytest.raises(AppError, match="cycle"):
        validate_plan(_plan(_step(1, "sysinfo", "get_cpu", depends_on=["2"]),
                            _step(2, "sysinfo", "get_cpu", depends_on=["1"])))
    with pytest.raises(AppError, match="dependency"):
        validate_plan(_plan(_step(1, "sysinfo", "get_cpu"),
                            _step(2, "filesystem", "write_file", {"content": "{{step_1.result}}"})))


@pytest.mark.parametrize("server,tool,error,expected", [
    ("filesystem", "read_file", AppError("not_found", "Missing"), 1),
    ("filesystem", "read_file", AppError("connection_failed", "Offline", retryable=True), 3),
    ("email", "send_email", AppError("connection_failed", "Offline", retryable=True), 1),
    ("filesystem", "write_file", TimeoutError(), 1),
])
async def test_retry_policy(server, tool, error, expected, monkeypatch):
    call = AsyncMock(side_effect=error)
    monkeypatch.setattr(executor, "_call_tool", call)
    monkeypatch.setattr(executor, "_MAX_RETRIES", 3)
    monkeypatch.setattr(executor.asyncio, "sleep", AsyncMock())
    log = await executor.execute(_plan(_step(1, server, tool)))
    assert log[0]["status"] == "error"
    assert log[0]["attempts"] == expected
    assert call.await_count == expected


async def test_real_timeout_no_retry_or_downstream_call(monkeypatch):
    async def hang(*args, **kwargs):
        await asyncio.Event().wait()
    call = AsyncMock(side_effect=hang)
    monkeypatch.setattr(executor, "_call_tool", call)
    monkeypatch.setattr(executor, "_TIMEOUT", .02)
    log = await executor.execute(_plan(
        _step(1, "email", "send_email"),
        _step(2, "filesystem", "write_file", depends_on=["1"]),
    ))
    assert log[0]["error_code"] == "timeout"
    assert log[1]["status"] == "skipped"
    assert call.await_count == 1


async def test_independent_branch_continues_and_nested_params_resolve(monkeypatch):
    seen = []
    async def call(server, tool, params, **kwargs):
        seen.append(params)
        if params.get("fail"):
            raise FileNotFoundError()
        return "good"
    monkeypatch.setattr(executor, "_call_tool", call)
    log = await executor.execute(_plan(
        _step(1, "filesystem", "read_file", {"fail": True}),
        _step(2, "sysinfo", "get_memory"),
        _step(3, "filesystem", "write_file", {"content": ["{{step_2.result}}"]}, ["2"]),
        _step(4, "filesystem", "write_file", depends_on=["1"]),
    ))
    assert {e["id"]: e["status"] for e in log} == {"1": "error", "2": "done", "3": "done", "4": "skipped"}
    assert seen[-1] == {"content": ["good"]}


async def test_cancellation_stops_all_parallel_work(monkeypatch):
    started, stopped = set(), set()
    async def call(server, tool, params, **kwargs):
        started.add(tool)
        try:
            await asyncio.Event().wait()
        finally:
            stopped.add(tool)
    monkeypatch.setattr(executor, "_call_tool", call)
    task = asyncio.create_task(executor.execute(_plan(_step(1, "sysinfo", "get_cpu"), _step(2, "sysinfo", "get_memory"))))
    while len(started) < 2:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped == started


async def test_input_wait_does_not_consume_tool_timeout(monkeypatch):
    async def call(server, tool, params, request_input=None):
        result = await request_input("Which?", {"type": "object"})
        return result["answer"]
    async def ask(message, schema):
        await asyncio.sleep(.06)
        return {"answer": "ok"}
    monkeypatch.setattr(executor, "_call_tool", call)
    monkeypatch.setattr(executor, "_TIMEOUT", .03)
    log = await executor.execute(_plan(_step(1, "filesystem", "read_file")), request_input=ask)
    assert log[0]["status"] == "done", log


def test_atomic_write_keeps_original_on_failure(tmp_path):
    from mcp_servers.common import atomic_output
    target = tmp_path / "original.txt"
    target.write_text("original", encoding="utf-8")
    with pytest.raises(RuntimeError):
        with atomic_output(str(target)) as temporary:
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write("partial")
            raise RuntimeError("save failed")
    assert target.read_text(encoding="utf-8") == "original"
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("exception,code", [
    (PermissionError(), "permission_denied"), (IsADirectoryError(), "invalid_path"),
    (ValueError(), "invalid_arguments"), (RuntimeError(), "internal_error"),
])
def test_precise_error_codes_survive_mcp(exception, code):
    from mcp_servers.common import tool_errors
    from mcp.server.fastmcp.exceptions import ToolError
    @tool_errors
    def fail():
        raise exception
    with pytest.raises(ToolError) as exc:
        fail()
    assert tool_result_error(str(exc.value)).code == code
    assert classify_error(ExceptionGroup("transport wrapper", [AppError(code, "failed")])).code == code


@pytest.mark.parametrize("status,code,retryable", [
    (401, "authentication_failed", False), (403, "authentication_failed", False),
    (404, "not_found", False), (429, "rate_limited", True), (503, "remote_error", True),
])
def test_remote_service_errors(status, code, retryable):
    request = httpx.Request("GET", "https://example.test/")
    exc = httpx.HTTPStatusError("private response text", request=request, response=httpx.Response(status, request=request))
    result = classify_error(exc)
    assert (result.code, result.retryable) == (code, retryable)
    assert "private" not in str(result)


@pytest.mark.parametrize("exc,code", [
    (smtplib.SMTPAuthenticationError(535, b"sensitive text"), "authentication_failed"),
    (smtplib.SMTPRecipientsRefused({"private@test": "denied"}), "recipient_rejected"),
    (OSError(errno.ENOSPC, "private path"), "storage_full"),
])
def test_email_and_disk_failures(exc, code):
    assert classify_error(exc).code == code


def test_failed_page_extraction_is_error(monkeypatch):
    from mcp_servers.websearch import server
    from mcp.server.fastmcp.exceptions import ToolError
    from unittest.mock import MagicMock
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.return_value.json.return_value = {"results": [], "failed_results": [{"error": "failed"}]}
    monkeypatch.setattr(server.httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(server, "_api_key", lambda: "test-key")
    with pytest.raises(ToolError) as exc:
        server.get_page_text("https://example.test")
    assert tool_result_error(str(exc.value)).code == "extraction_failed"


def test_invalid_pdf_table_preserves_destination(tmp_path):
    from mcp_servers.reports.server import create_pdf_with_table
    from mcp.server.fastmcp.exceptions import ToolError
    target = tmp_path / "report.pdf"
    target.write_bytes(b"original")
    with pytest.raises(ToolError) as exc:
        create_pdf_with_table("Title", "A,B", "only_one_column", str(target))
    assert tool_result_error(str(exc.value)).code == "invalid_arguments"
    assert target.read_bytes() == b"original"
