import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from orchestrator import main
from orchestrator.elicitation import InputBroker
from orchestrator.errors import AppError
from tests.test_e2e import _plan, _step

TOOLS = [{"server": "filesystem", "name": "write_file", "input_schema": {
    "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
    "required": ["path", "content"],
}}]
QUESTIONS = {"questions": [{"id": "path", "question": "Куда сохранить файл?"}]}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(main, "_tools", TOOLS)
    monkeypatch.setattr(main, "_tools_description", "test tools")
    # No lifespan: tests must not spawn unrelated discovery subprocesses.
    return TestClient(main.app)


def until(ws, kind):
    for _ in range(30):
        event = ws.receive_json()
        if event["type"] == kind:
            return event
        assert event["type"] not in ("error", "done"), event
    raise AssertionError(f"No {kind} event")


@pytest.mark.parametrize("task", ["", "  ", None, [], 12])
def test_invalid_rest_requests(api, task):
    assert api.post("/run", json={"task": task}).status_code == 422


def test_rest_needs_input_and_continues(api, monkeypatch):
    calls = []
    def planner(task, description, history):
        calls.append(history.copy())
        if not history:
            return QUESTIONS
        return _plan(_step(1, "filesystem", "write_file", {"path": history[0]["answer"], "content": "hello"}))
    execution = AsyncMock(return_value=[{"id": "1", "status": "done"}])
    monkeypatch.setattr(main, "plan", planner)
    monkeypatch.setattr(main, "execute", execution)
    response = api.post("/run", json={"task": "save hello"})
    body = response.json()
    assert body["status"] == "needs_input"
    assert body["execution"] == []
    execution.assert_not_called()
    response = api.post("/run", json={"task": "save hello", "clarifications": [
        {"question": "Куда сохранить файл?", "answer": "data/hello.txt"},
    ]})
    assert response.json()["success"] is True
    assert execution.call_args.args[0]["steps"][0]["params"]["path"] == "data/hello.txt"


def test_missing_required_field_asks_before_execution(api, monkeypatch):
    monkeypatch.setattr(main, "plan", lambda *a: _plan(_step(1, "filesystem", "write_file", {"content": "hello"})))
    execution = AsyncMock()
    monkeypatch.setattr(main, "execute", execution)
    body = api.post("/run", json={"task": "save hello"}).json()
    assert body["status"] == "needs_input"
    assert body["elicitation"]["requested_schema"]["required"] == ["path"]
    execution.assert_not_called()


def test_websocket_elicitation_validation_and_multiple_tasks(api, monkeypatch):
    def planner(task, description, history):
        if not history:
            return QUESTIONS
        return _plan(_step(1, "filesystem", "write_file", {"path": history[0]["answer"], "content": "hello"}))
    execution = AsyncMock(return_value=[{"id": "1", "status": "done"}])
    monkeypatch.setattr(main, "plan", planner)
    monkeypatch.setattr(main, "execute", execution)
    with api.websocket_connect("/ws") as ws:
        for _ in range(2):
            ws.send_json({"task": "save hello"})
            request = until(ws, "elicitation")
            ws.send_json({"type": "elicitation_response", "request_id": "wrong", "action": "accept", "content": {"path": "x"}})
            assert until(ws, "input_error")["code"] == "unknown_request"
            ws.send_json({"type": "elicitation_response", "request_id": request["request_id"], "action": "accept", "content": {"path": " "}})
            assert until(ws, "input_error")["code"] == "invalid_input"
            assert execution.await_count == _
            ws.send_json({"type": "elicitation_response", "request_id": request["request_id"], "action": "accept", "content": {"path": "data/hello.txt"}})
            assert until(ws, "done")["success"] is True
    assert execution.await_count == 2


@pytest.mark.parametrize("action,kind,code", [("decline", "error", "input_declined"), ("cancel", "cancelled", None)])
def test_decline_or_cancel_performs_no_actions(api, monkeypatch, action, kind, code):
    monkeypatch.setattr(main, "plan", lambda *a: QUESTIONS)
    execution = AsyncMock()
    monkeypatch.setattr(main, "execute", execution)
    with api.websocket_connect("/ws") as ws:
        ws.send_json({"task": "save hello"})
        request = until(ws, "elicitation")
        if action == "cancel":
            ws.send_json({"type": "cancel"})
        else:
            ws.send_json({"type": "elicitation_response", "request_id": request["request_id"], "action": action})
        result = until(ws, kind)
        if code:
            assert result["code"] == code
    execution.assert_not_called()


def test_invalid_plan_is_not_executed(api, monkeypatch):
    monkeypatch.setattr(main, "plan", lambda *a: _plan(_step(1, "unavailable", "tool")))
    execution = AsyncMock()
    monkeypatch.setattr(main, "execute", execution)
    body = api.post("/run", json={"task": "anything"})
    assert body.status_code == 422
    assert body.json()["error"]["code"] == "invalid_plan"
    execution.assert_not_called()


def test_planner_failure_is_structured(api, monkeypatch):
    def fail(*a):
        raise AppError("authentication_failed", "Check configuration")
    monkeypatch.setattr(main, "plan", fail)
    result = api.post("/run", json={"task": "anything"})
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "authentication_failed"


async def test_three_clarification_rounds_then_final_plan(monkeypatch):
    calls = []
    def planner(task, description, history):
        calls.append(len(history))
        if len(history) < 3:
            return {"questions": [{"id": "value", "question": "Уточните значение"}]}
        return _plan(_step(1, "filesystem", "write_file", {"path": "unused", "content": "value"}))
    monkeypatch.setattr(main, "plan", planner)
    monkeypatch.setattr(main, "_tools", TOOLS)
    async def ask(*args):
        return {"value": "answer"}
    result = await main.prepare_plan(main.TaskRequest(task="task"), ask)
    assert result["steps"]
    assert calls == [0, 1, 2, 3]


async def test_repeated_ambiguity_is_bounded(monkeypatch):
    monkeypatch.setattr(main, "plan", lambda *a: QUESTIONS)
    ask = AsyncMock(return_value={"path": "answer"})
    with pytest.raises(AppError) as exc:
        await main.prepare_plan(main.TaskRequest(task="task"), ask)
    assert exc.value.code == "clarification_limit"
    assert ask.await_count == 3


def test_websocket_recovers_after_malformed_request(api, monkeypatch):
    monkeypatch.setattr(main, "plan", lambda *a: QUESTIONS)
    with api.websocket_connect("/ws") as ws:
        ws.send_text("not JSON")
        assert ws.receive_json()["code"] == "invalid_request"
        ws.send_json({"task": "save"})
        request = until(ws, "elicitation")
        ws.send_text("not JSON")
        assert until(ws, "input_error")["code"] == "invalid_request"
        ws.send_json({"type": "cancel"})
        until(ws, "cancelled")


def test_disconnect_cancels_waiting_execution(api, monkeypatch):
    from threading import Event
    started, cancelled = Event(), Event()
    monkeypatch.setattr(main, "plan", lambda *a: _plan(_step(1, "filesystem", "write_file", {"path": "unused", "content": "x"})))
    async def execution(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(main, "execute", execution)
    with api.websocket_connect("/ws") as ws:
        ws.send_json({"task": "save"})
        until(ws, "plan")
        assert started.wait(2)
    assert cancelled.wait(2)


async def test_input_timeout(monkeypatch):
    monkeypatch.setenv("ELICITATION_TIMEOUT_SEC", ".01")
    broker = InputBroker(AsyncMock())
    with pytest.raises(AppError) as exc:
        await broker.ask("Which?", {"type": "object", "properties": {"value": {"type": "string"}}})
    assert exc.value.code == "input_timeout"
    assert not broker.pending


async def test_parallel_input_requests_are_correlated():
    events = []
    async def send(event):
        events.append(event)
    broker = InputBroker(send)
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}
    first = asyncio.create_task(broker.ask("First", schema))
    second = asyncio.create_task(broker.ask("Second", schema))
    while len(events) < 2:
        await asyncio.sleep(0)
    for event, value in [(events[1], 2), (events[0], 1)]:
        await broker.respond({"request_id": event["request_id"], "action": "accept", "content": {"value": value}})
    assert await first == {"value": 1}
    assert await second == {"value": 2}
