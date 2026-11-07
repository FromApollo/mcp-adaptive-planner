"""Stable error codes shared by the API, executor and local MCP tools."""
import json
import smtplib
import sqlite3
import errno

import httpx


class AppError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "retryable": self.retryable}


def classify_error(exc: Exception) -> AppError:
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        errors = [classify_error(e) for e in exc.exceptions if isinstance(e, Exception)]
        # Task-group wrappers must not hide a precise tool or input error.
        return next((e for e in errors if e.code != "internal_error"),
                    AppError("internal_error", "An internal operation failed."))
    if isinstance(exc, FileNotFoundError):
        return AppError("not_found", "File or directory not found" + (f": {exc.filename}" if exc.filename else "."))
    if isinstance(exc, PermissionError):
        return AppError("permission_denied", "Access to the file or directory was denied.")
    if isinstance(exc, (IsADirectoryError, NotADirectoryError)):
        return AppError("invalid_path", "Expected a file or directory of a different type.")
    if isinstance(exc, UnicodeError):
        return AppError("invalid_encoding", "The file is not valid UTF-8 text.")
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return AppError("authentication_failed", "Email authentication failed; check server configuration.")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return AppError("recipient_rejected", "The mail server rejected the recipient.")
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return AppError("sender_rejected", "The mail server rejected the sender.")
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return AppError("connection_failed", "The mail server disconnected; delivery may be unknown.")
    if isinstance(exc, smtplib.SMTPException):
        return AppError("email_failed", "The mail server could not complete the operation.")
    if isinstance(exc, sqlite3.Error):
        return AppError("storage_error", "The notes storage operation failed.")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return AppError("authentication_failed", "The remote service denied access; check configuration.")
        if status == 404:
            return AppError("not_found", "The remote resource was not found.")
        return AppError("rate_limited" if status == 429 else "remote_error",
                        f"The remote service returned HTTP {status}.",
                        retryable=status == 429 or status >= 500)
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return AppError("timeout", "The operation timed out; its outcome may be unknown.")
    if isinstance(exc, (ConnectionError, httpx.NetworkError)):
        return AppError("connection_failed", "Could not connect to the remote service.", retryable=True)
    if isinstance(exc, (ValueError, TypeError)):
        return AppError("invalid_arguments", "Invalid tool arguments or configuration.")
    if isinstance(exc, OSError):
        if exc.errno == errno.ENOSPC:
            return AppError("storage_full", "Insufficient disk space.")
        return AppError("io_error", "An input/output operation failed.")
    return AppError("internal_error", "An unexpected internal error occurred. See the server log.")


def tool_result_error(text: str) -> AppError:
    """Decode errors from our servers, with a safe fallback for third-party tools."""
    marker = "PLANNER_ERROR:"
    if marker in text:
        try:
            data = json.loads(text.split(marker, 1)[1])
            return AppError(data["code"], data["message"], retryable=data.get("retryable") is True)
        except (ValueError, KeyError, TypeError):
            pass
    return AppError("tool_error", "The MCP tool reported an error. Check its arguments and server log.")
