from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel
from orchestrator.planner import plan
from orchestrator.executor import execute
from orchestrator.registry import discover_all_tools, build_tools_description

_tools: list[dict] = []
_tools_description: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tools, _tools_description
    _tools = await discover_all_tools()
    _tools_description = build_tools_description(_tools)
    yield


app = FastAPI(title="Planner Agent", version="0.3.0", lifespan=lifespan)


class TaskRequest(BaseModel):
    task: str


@app.get("/")
async def index():
    return FileResponse("frontend/index.html")


@app.websocket("/ws")
async def ws_run(websocket: WebSocket):
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        task = data.get("task", "").strip()
        if not task:
            await websocket.send_json({"type": "error", "message": "Empty task"})
            return

        try:
            task_plan = plan(task, _tools_description)
        except Exception as exc:
            await websocket.send_json({"type": "error", "message": str(exc)})
            return

        await websocket.send_json({"type": "plan", "plan": task_plan})

        async def on_event(event: dict):
            try:
                await websocket.send_json(event)
            except Exception:
                pass

        execution_log = await execute(task_plan, on_event=on_event)
        success = all(s["status"] == "done" for s in execution_log)
        await websocket.send_json({
            "type": "done",
            "success": success,
            "execution": execution_log,
        })
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass


@app.post("/run")
async def run_task(request: TaskRequest):
    task_plan = plan(request.task, _tools_description)
    execution_log = await execute(task_plan)
    success = all(s["status"] == "done" for s in execution_log)
    return {
        "task": request.task,
        "plan": task_plan,
        "execution": execution_log,
        "success": success,
    }


@app.get("/tools")
async def list_tools():
    return {"tools": _tools, "description": _tools_description}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "servers": sorted({t["server"] for t in _tools if "error" not in t}),
    }
