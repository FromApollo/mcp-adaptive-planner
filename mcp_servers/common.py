"""Preserve machine-readable failure information through MCP's isError response."""
from functools import wraps
import json
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile

from mcp.server.fastmcp.exceptions import ToolError
from orchestrator.errors import classify_error


def tool_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            error = classify_error(exc)
            raise ToolError("PLANNER_ERROR:" + json.dumps(error.as_dict())) from exc
    return wrapped


@contextmanager
def atomic_output(path: str):
    """Replace the destination only after successfully producing the complete file."""
    if not path.strip():
        raise ValueError("Output path must not be empty")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".planner_", suffix=target.suffix)
    os.close(fd)
    try:
        yield temporary
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
