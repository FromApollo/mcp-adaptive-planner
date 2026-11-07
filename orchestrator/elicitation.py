"""Correlated, validated input requests. Only the WebSocket reader receives frames."""
import asyncio
import os
import uuid
from contextlib import suppress

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from orchestrator.errors import AppError
from orchestrator.logging_config import get_logger

logger = get_logger("elicitation")


class NeedsInput(AppError):
    def __init__(self, message, schema):
        super().__init__("elicitation_required", message)
        self.schema = schema


def validate_input(content, schema):
    if not isinstance(content, dict) or not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(content):
        raise AppError("invalid_input", "The answer does not match the requested fields or types.")
    if any(isinstance(v, str) and not v.strip() for k, v in content.items() if k in schema.get("required", [])):
        raise AppError("invalid_input", "Required answers must not be blank.")


class InputBroker:
    def __init__(self, send):
        self.send = send
        self.pending = {}

    async def ask(self, message, schema):
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            raise AppError("invalid_elicitation", "The tool requested an invalid input form.") from None
        # The UI supports the MCP form subset; reject other forms explicitly.
        properties = schema.get("properties", {})
        if schema.get("type") != "object" or not properties or any(
            p.get("type") not in ("string", "number", "integer", "boolean") for p in properties.values()
        ):
            raise AppError("unsupported_elicitation", "The requested form contains unsupported field types.")
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = (future, schema)
        logger.info("input_requested", extra={"count": len(properties)})
        try:
            await self.send({"type": "elicitation", "request_id": request_id,
                             "message": message, "requested_schema": schema})
            try:
                return await asyncio.wait_for(future, float(os.getenv("ELICITATION_TIMEOUT_SEC", "300")))
            except TimeoutError:
                raise AppError("input_timeout", "Timed out waiting for the user's answer.") from None
        finally:
            self.pending.pop(request_id, None)
            with suppress(Exception):
                await self.send({"type": "input_closed", "request_id": request_id})

    async def respond(self, data):
        request_id = data.get("request_id")
        pending = self.pending.get(request_id) if isinstance(request_id, str) else None
        if pending is None or pending[0].done():
            await self.send({"type": "input_error", "code": "unknown_request", "request_id": request_id,
                             "message": "This input request is no longer active."})
            return
        future, schema = pending
        action = data.get("action")
        if action in ("cancel", "decline"):
            future.set_exception(AppError("input_declined", "The user declined to provide the required information."))
            return
        try:
            if action != "accept":
                raise AppError("invalid_input", "Expected action accept, decline or cancel.")
            validate_input(data.get("content"), schema)
        except AppError as exc:
            await self.send({"type": "input_error", "request_id": request_id, **exc.as_dict()})
            return
        logger.info("input_received")
        # A response can race a timeout while the WebSocket is sending the ack.
        await self.send({"type": "input_accepted", "request_id": request_id})
        if not future.done():
            future.set_result(data["content"])

    def close(self):
        for future, _ in self.pending.values():
            if not future.done():
                future.cancel()
