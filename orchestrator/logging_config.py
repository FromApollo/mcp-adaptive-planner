"""Application logs deliberately exclude task text, tool payloads and answers."""
import contextvars
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from datetime import datetime, timezone
import traceback

run_id = contextvars.ContextVar("run_id", default="-")
step_id = contextvars.ContextVar("step_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "run_id": run_id.get(),
            "step_id": step_id.get(),
            "event": record.getMessage(),
        }
        for key in ("server", "tool", "code", "attempt", "duration_ms", "count", "status", "exception_type", "trace"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        return json.dumps(data, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"planner.{name}")


def error_details(exc: Exception) -> dict:
    """Keep diagnostic locations without exception messages, source text or locals."""
    return {"exception_type": type(exc).__name__, "trace": [
        {"file": frame.filename, "line": frame.lineno, "function": frame.name}
        for frame in traceback.extract_tb(exc.__traceback__)
    ]}


def configure_logging() -> None:
    logger = logging.getLogger("planner")
    if logger.handlers:
        return
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    formatter = JsonFormatter()
    console = logging.StreamHandler()  # stderr; never corrupt MCP's stdout transport
    console.setFormatter(formatter)
    logger.addHandler(console)
    path = Path(os.getenv("LOG_FILE", "logs/planner.log"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except OSError:
        logger.error("log_file_unavailable", extra={"code": "permission_denied"})
