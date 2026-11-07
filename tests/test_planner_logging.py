import asyncio
import io
import json
import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orchestrator import executor, planner
from orchestrator.errors import AppError
from orchestrator.logging_config import JsonFormatter, configure_logging, run_id
from tests.test_e2e import _plan, _step


@pytest.mark.parametrize("raw", ["not JSON", "null", "[]", "{}", '{"steps":[]}',
                                  '{"questions":[]}', '{"questions":[{"id":"x"}]}',
                                  '{"steps":[{}]}'])
def test_invalid_model_response_is_rejected(monkeypatch, raw):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = MagicMock()
    client.__enter__.return_value = client
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=raw),
    )])
    monkeypatch.setattr(planner, "OpenAI", MagicMock(return_value=client))
    with pytest.raises(AppError) as exc:
        planner.plan("task", "tools")
    assert exc.value.code == "invalid_plan"


def test_planner_accepts_clarifications_and_requests_input(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = MagicMock()
    client.__enter__.return_value = client
    questions = {"questions": [{"id": "recipient", "question": "Кому отправить письмо?"}]}
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=json.dumps(questions)),
    )])
    monkeypatch.setattr(planner, "OpenAI", MagicMock(return_value=client))
    history = [{"question": "Тема?", "answer": "Отчёт"}]
    assert planner.plan("send an email", "tools", history) == questions
    sent = json.loads(client.chat.completions.create.call_args.kwargs["messages"][1]["content"])
    assert sent["clarifications"] == history


def test_missing_llm_key_is_configuration_error(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(AppError) as exc:
        planner.plan("task", "tools")
    assert exc.value.code == "configuration_error"


async def test_real_mcp_elicitation_round_trip(monkeypatch):
    monkeypatch.setattr(executor, "get_server_command", lambda name: [sys.executable, "-m", "tests.elicitation_server"])
    events = []
    async def on_event(event):
        events.append(event)
    async def ask(message, schema):
        assert "значение" in message
        assert schema["required"] == ["value"]
        return {"value": "user supplied value"}
    result = await executor.execute(_plan(_step(1, "inputtest", "request_value")),
                                    request_input=ask, on_event=on_event)
    assert result[0]["status"] == "done", result
    assert result[0]["result"] == "user supplied value"
    assert [e["status"] for e in events] == ["running", "waiting_input", "running", "done"]


async def test_real_mcp_declined_input_stops_dependents(monkeypatch):
    monkeypatch.setattr(executor, "get_server_command", lambda name: [sys.executable, "-m", "tests.elicitation_server"])
    async def ask(message, schema):
        raise AppError("input_declined", "User declined")
    result = await executor.execute(_plan(
        _step(1, "inputtest", "request_value"),
        _step(2, "filesystem", "write_file", depends_on=["1"]),
    ), request_input=ask)
    assert result[0]["status"] == "error", result
    assert result[0]["error_code"] == "input_declined", result
    assert result[1]["status"] == "skipped"


async def test_logs_correlate_parallel_runs_without_payloads(monkeypatch):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("planner")
    original = logger.handlers[:], logger.level, logger.propagate
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    async def call(*args, **kwargs):
        await asyncio.sleep(0)
        return "SECRET_RESULT"
    monkeypatch.setattr(executor, "_call_tool", call)
    async def run(identifier):
        token = run_id.set(identifier)
        try:
            return await executor.execute(_plan(_step(1, "filesystem", "write_file", {"content": "SECRET_CONTENT"})))
        finally:
            run_id.reset(token)
    try:
        await asyncio.gather(run("run_a"), run("run_b"))
        records = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert {r["run_id"] for r in records} == {"run_a", "run_b"}
        assert {r["step_id"] for r in records} == {"1"}
        assert all(any(r["event"] == "step_finished" and r["run_id"] == identifier for r in records)
                   for identifier in ("run_a", "run_b"))
        assert "SECRET" not in stream.getvalue()
    finally:
        logger.handlers, logger.level, logger.propagate = original


def test_file_logging_is_idempotent_and_rotates(tmp_path, monkeypatch):
    logger = logging.getLogger("planner")
    original = logger.handlers[:], logger.level, logger.propagate
    logger.handlers = []
    path = tmp_path / "logs" / "planner.log"
    monkeypatch.setenv("LOG_FILE", str(path))
    try:
        configure_logging()
        configure_logging()
        assert len(logger.handlers) == 2
        file_handler = logger.handlers[1]
        file_handler.maxBytes = 100
        logger.info("first_event")
        logger.info("second_event")
        assert json.loads(path.read_text(encoding="utf-8"))["event"] == "second_event"
        assert (path.parent / "planner.log.1").exists()
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers, logger.level, logger.propagate = original
