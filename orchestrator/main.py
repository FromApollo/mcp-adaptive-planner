import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError, field_validator

load_dotenv()  # Load timeout/logging settings before importing the executor.

from orchestrator.elicitation import InputBroker, NeedsInput
from orchestrator.errors import AppError, classify_error
from orchestrator.logging_config import configure_logging, get_logger, run_id, error_details
from orchestrator.planner import plan
from orchestrator.executor import execute
from orchestrator.registry import discover_all_tools, build_tools_description
from orchestrator.validation import validate_plan

logger = get_logger("api")
_tools: list[dict] = []
_tools_description = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tools, _tools_description
    configure_logging()
    logger.info("application_started")
    _tools = await discover_all_tools()
    _tools_description = build_tools_description(_tools)
    yield
    logger.info("application_stopped")


app = FastAPI(title="Planner Agent", version="0.4.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    identifier = uuid.uuid4().hex
    return JSONResponse(status_code=422, content={
        "status": "error", "success": False, "run_id": identifier, "execution": [],
        "error": AppError("invalid_request", "Provide a nonempty task string and valid question/answer clarifications.").as_dict(),
    })


class Clarification(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    answer: str = Field(min_length=1, max_length=10000)


class TaskRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20000)
    clarifications: list[Clarification] = Field(default_factory=list, max_length=30)

    @field_validator("task")
    @classmethod
    def nonblank_task(cls, value):
        if not value.strip():
            raise ValueError("Task must not be blank")
        return value.strip()


async def prepare_plan(request, ask):
    history = [c.model_dump() for c in request.clarifications]
    for round_number in range(4):
        task_plan = await asyncio.to_thread(plan, request.task, _tools_description, history)
        if "questions" in task_plan:
            if round_number == 3:
                raise AppError("clarification_limit", "The task still lacks essential information after three clarification rounds.")
            questions = task_plan["questions"]
            schema = {"type": "object", "properties": {
                q["id"]: {"type": "string", "title": q["question"], "minLength": 1, "maxLength": 10000}
                for q in questions
            }, "required": [q["id"] for q in questions], "additionalProperties": False}
            content = await ask("Для продолжения необходимо уточнить данные задачи.", schema, history)
            history.extend({"question": q["question"], "answer": content[q["id"]]} for q in questions)
            continue
        validate_plan(task_plan, _tools, allow_missing_required=True)
        # A deterministic safety net even if the model omitted a required field
        # instead of asking. Complete every form BEFORE executing any step.
        inventory = {(t["server"], t["name"]): t for t in _tools if "error" not in t}
        for step in task_plan["steps"]:
            if step["server"] == "error":
                continue
            tool_schema = inventory[step["server"], step["tool"].split(".")[-1]]["input_schema"]
            params = step.setdefault("params", {})
            missing = [k for k in tool_schema.get("required", []) if k not in params]
            if missing:
                schema = {"type": "object", "properties": {
                    k: {**tool_schema["properties"][k], "title": f"{step['server']}.{step['tool']}: {k}"}
                    for k in missing
                }, "required": missing, "additionalProperties": False}
                content = await ask("Укажите недостающие параметры шага.", schema, history)
                params.update(content)
                history.extend({"question": schema["properties"][k]["title"], "answer": str(content[k])}
                               for k in missing)
        validate_plan(task_plan, _tools)
        return task_plan
    raise AppError("clarification_limit", "The task still lacks essential information after three clarification rounds.")


@app.get("/")
async def index():
    return FileResponse(Path(__file__).resolve().parents[1] / "frontend" / "index.html")


@app.websocket("/ws")
async def ws_run(websocket: WebSocket):
    await websocket.accept()
    send_lock = asyncio.Lock()

    async def send(event):
        async with send_lock:
            await websocket.send_json(event)

    while True:
        try:
            data = await websocket.receive_json()
            request = TaskRequest.model_validate(data)
        except WebSocketDisconnect:
            return
        except (ValidationError, ValueError, TypeError):
            await send({"type": "error", "code": "invalid_request", "message": "Send an object containing a nonempty task string."})
            continue

        broker = InputBroker(send)
        identifier = uuid.uuid4().hex

        async def process():
            token = run_id.set(identifier)
            logger.info("run_started")
            try:
                await send({"type": "run_started", "run_id": identifier})

                async def ask(message, schema, history=None):
                    return await broker.ask(message, schema)

                task_plan = await prepare_plan(request, ask)
                await send({"type": "plan", "plan": task_plan})
                execution_log = await execute(task_plan, on_event=send, tools=_tools, request_input=ask)
                success = bool(execution_log) and all(s["status"] == "done" for s in execution_log)
                logger.info("run_finished", extra={"status": "done" if success else "error"})
                await send({"type": "done", "run_id": identifier, "success": success, "execution": execution_log})
            except asyncio.CancelledError:
                logger.info("run_cancelled")
                with suppress(Exception):
                    await send({"type": "cancelled", "run_id": identifier,
                                "message": "Execution cancelled; completed actions are retained."})
                raise
            except Exception as exc:
                error = classify_error(exc)
                logger.error("run_failed", extra={"code": error.code, **error_details(exc)})
                await send({"type": "error", "run_id": identifier, **error.as_dict()})
            finally:
                broker.close()
                run_id.reset(token)

        worker = asyncio.create_task(process())
        receiver = None
        try:
            while not worker.done():
                receiver = asyncio.create_task(websocket.receive_json())
                done, _ = await asyncio.wait({worker, receiver}, return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    try:
                        message = receiver.result()
                    except (ValueError, TypeError):
                        await send({"type": "input_error", "code": "invalid_request", "message": "Expected a JSON object."})
                        continue
                    if not isinstance(message, dict):
                        await send({"type": "input_error", "code": "invalid_request", "message": "Expected a JSON object."})
                    elif message.get("type") == "elicitation_response":
                        await broker.respond(message)
                    elif message.get("type") == "cancel":
                        worker.cancel()
                        break
                    else:
                        await send({"type": "input_error", "code": "busy", "message": "A task is already running."})
                else:
                    receiver.cancel()
                    with suppress(asyncio.CancelledError):
                        await receiver
            await asyncio.gather(worker, return_exceptions=True)
        except WebSocketDisconnect:
            logger.info("client_disconnected")
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            return
        finally:
            broker.close()
            if not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            if receiver and not receiver.done():
                receiver.cancel()
                with suppress(asyncio.CancelledError):
                    await receiver


@app.post("/run")
async def run_task(request: TaskRequest):
    identifier = uuid.uuid4().hex
    token = run_id.set(identifier)
    logger.info("run_started")
    try:
        async def ask(message, schema, history=None):
            error = NeedsInput(message, schema)
            error.history = history or []
            raise error

        task_plan = await prepare_plan(request, ask)
        execution_log = await execute(task_plan, tools=_tools)
        success = bool(execution_log) and all(s["status"] == "done" for s in execution_log)
        logger.info("run_finished", extra={"status": "done" if success else "error"})
        return {"task": request.task, "run_id": identifier, "plan": task_plan,
                "execution": execution_log, "status": "done" if success else "error", "success": success}
    except NeedsInput as exc:
        logger.info("run_needs_input")
        return {"status": "needs_input", "success": False, "run_id": identifier, "execution": [],
                "elicitation": {"message": str(exc), "requested_schema": exc.schema}, "clarifications": exc.history}
    except Exception as exc:
        error = classify_error(exc)
        logger.error("run_failed", extra={"code": error.code, **error_details(exc)})
        status = 422 if error.code in ("invalid_plan", "invalid_arguments", "clarification_limit") else 503
        return JSONResponse(status_code=status, content={"status": "error", "success": False,
                            "run_id": identifier, "error": error.as_dict(), "execution": []})
    finally:
        run_id.reset(token)


@app.get("/tools")
async def list_tools():
    return {"tools": _tools, "description": _tools_description}


@app.get("/health")
async def health():
    return {"status": "degraded" if any("error" in t for t in _tools) or not _tools else "ok",
            "servers": sorted({t["server"] for t in _tools if "error" not in t}),
            "unavailable_servers": [t["server"] for t in _tools if "error" in t]}
