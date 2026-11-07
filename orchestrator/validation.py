"""Validate the whole DAG before any tool can produce side effects."""
import re

from jsonschema import Draft202012Validator
from orchestrator.errors import AppError

REFERENCE = re.compile(r"\{\{step_(\w+)\.result\}\}")


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def validate_plan(plan: dict, tools: list[dict] | None = None, *, allow_missing_required=False) -> None:
    def invalid(message):
        raise AppError("invalid_plan", message)

    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list) or not plan["steps"]:
        invalid("The plan must contain a nonempty list of steps.")
    steps = plan["steps"]
    if len(steps) > 100:
        invalid("The plan exceeds the limit of 100 steps.")
    if len(steps) > 1 and any(isinstance(s, dict) and s.get("server") == "error" for s in steps):
        invalid("An unavailable task must not be mixed with executable steps.")
    ids = set()
    for step in steps:
        if not isinstance(step, dict):
            invalid("Each step must be an object.")
        sid = step.get("id")
        if not isinstance(sid, str) or not re.fullmatch(r"\w{1,64}", sid) or sid in ids:
            invalid("Step IDs must be unique nonempty strings containing letters, digits or underscores.")
        ids.add(sid)
        for field in ("server", "tool"):
            if not isinstance(step.get(field), str) or not re.fullmatch(r"[a-zA-Z_]\w*(?:\.\w+)?", step[field]):
                invalid(f"Step '{sid}' has an invalid {field}.")
        if not isinstance(step.get("params", {}), dict):
            invalid(f"Step '{sid}' parameters must be an object.")
        if "description" in step and not isinstance(step["description"], str):
            invalid(f"Step '{sid}' description must be a string.")
        deps = step.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            invalid(f"Step '{sid}' dependencies must be a list of IDs.")
        if len(deps) != len(set(deps)):
            invalid(f"Step '{sid}' has duplicate dependencies.")
    inventory = {(t["server"], t["name"]): t for t in (tools or []) if "error" not in t}
    for step in steps:
        sid, deps = step["id"], step.get("depends_on", [])
        if any(d not in ids or d == sid for d in deps):
            invalid(f"Step '{sid}' has an unknown or self dependency.")
        for text in strings(step.get("params", {})):
            refs = REFERENCE.findall(text)
            if any(ref not in deps for ref in refs):
                invalid(f"Step '{sid}' references a result without declaring its dependency.")
            if "{{" in REFERENCE.sub("", text) or "}}" in REFERENCE.sub("", text):
                invalid(f"Step '{sid}' contains an unresolved or malformed template.")
        server, tool = step["server"], step["tool"]
        if "." in tool:
            prefix, tool = tool.split(".", 1)
            if prefix != server:
                invalid(f"Step '{sid}' tool prefix does not match its server.")
        if tools is not None and server != "error":
            if (server, tool) not in inventory:
                invalid(f"Step '{sid}' selects an unavailable tool.")
            schema = inventory[server, tool].get("input_schema", {})
            # Types involving result references are checked again after resolution.
            params = step.get("params", {})
            for error in Draft202012Validator(schema).iter_errors(params):
                if allow_missing_required and error.validator == "required":
                    continue
                if error.validator == "required" or not any(REFERENCE.search(s) for s in strings(error.instance)):
                    invalid(f"Step '{sid}' has invalid arguments ({error.validator}).")
            if set(params) - set(schema.get("properties", {})):
                invalid(f"Step '{sid}' contains unknown arguments.")
    remaining = {s["id"]: set(s.get("depends_on", [])) for s in steps}
    completed = set()
    while remaining:
        ready = {sid for sid, deps in remaining.items() if deps <= completed}
        if not ready:
            invalid("The plan contains a dependency cycle.")
        completed.update(ready)
        for sid in ready:
            del remaining[sid]
