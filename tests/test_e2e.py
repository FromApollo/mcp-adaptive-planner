"""
End-to-end tests: each test calls the real MCP servers through the executor.
Run with:  pytest tests/ -v
The server does NOT need to be running — the executor spawns MCP subprocesses directly.
"""
import pytest
from orchestrator.executor import execute


def _plan(*steps):
    """Build a minimal plan dict from step dicts."""
    return {"steps": list(steps)}


def _step(sid, server, tool, params=None, depends_on=None, description=""):
    return {
        "id": str(sid),
        "server": server,
        "tool": tool,
        "params": params or {},
        "depends_on": depends_on or [],
        "description": description,
    }


# ─── 1. Filesystem ────────────────────────────────────────────────────────────
async def test_filesystem_write_and_read():
    log = await execute(_plan(
        _step(1, "filesystem", "write_file",
              {"path": "data/e2e_test.txt", "content": "hello from E2E"},
              description="Write test file"),
        _step(2, "filesystem", "read_file",
              {"path": "data/e2e_test.txt"},
              depends_on=["1"], description="Read test file"),
    ))

    assert len(log) == 2
    assert log[0]["status"] == "done", log[0].get("error")
    assert log[1]["status"] == "done", log[1].get("error")
    assert "hello from E2E" in log[1]["result"]


# ─── 2. Notes (SQLite) ────────────────────────────────────────────────────────
async def test_notes_create_and_get():
    log = await execute(_plan(
        _step(1, "notes", "create_note",
              {"title": "E2E Note", "content": "pytest was here", "tags": "test,e2e"},
              description="Create note"),
        _step(2, "notes", "get_note",
              {"title": "E2E Note"},
              depends_on=["1"], description="Read note"),
    ))

    assert log[0]["status"] == "done", log[0].get("error")
    assert log[1]["status"] == "done", log[1].get("error")
    assert "pytest was here" in log[1]["result"]


# ─── 3. Sysinfo ───────────────────────────────────────────────────────────────
async def test_sysinfo_memory():
    log = await execute(_plan(
        _step(1, "sysinfo", "get_memory", description="Get RAM usage"),
    ))

    assert log[0]["status"] == "done", log[0].get("error")
    result = log[0]["result"]
    assert "RAM" in result
    assert "MB" in result


# ─── 4. Reports — DOCX ────────────────────────────────────────────────────────
async def test_reports_create_docx():
    log = await execute(_plan(
        _step(1, "reports", "create_docx",
              {"title": "E2E Report",
               "content": "Line one.\nLine two.",
               "output_path": "data/e2e_report.docx"},
              description="Create DOCX"),
    ))

    assert log[0]["status"] == "done", log[0].get("error")
    assert "DOCX created" in log[0]["result"]


# ─── 5. Composite DAG — parallel sysinfo → write file ────────────────────────
async def test_dag_parallel_branches():
    log = await execute(_plan(
        _step(1, "sysinfo", "get_cpu",    description="Get CPU"),
        _step(2, "sysinfo", "get_memory", description="Get memory"),
        _step(3, "filesystem", "write_file",
              {"path": "data/e2e_dag.txt",
               "content": "CPU:\n{{step_1.result}}\n\nRAM:\n{{step_2.result}}"},
              depends_on=["1", "2"], description="Write combined"),
    ))

    assert len(log) == 3
    assert all(e["status"] == "done" for e in log), \
        [(e["id"], e.get("error")) for e in log if e["status"] != "done"]

    # Steps 1 and 2 ran in the same parallel wave
    groups = {e["id"]: e.get("parallel_group") for e in log}
    assert groups["1"] == groups["2"], "CPU and memory should run in parallel"
    assert groups["3"] != groups["1"], "write_file should run after"
