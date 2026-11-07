import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv
from mcp.server.fastmcp.server import FastMCP
from mcp_servers.common import tool_errors
from orchestrator.errors import AppError
from email.utils import parseaddr

load_dotenv()

mcp = FastMCP("email")


def _smtp_client():
    sender = os.getenv("EMAIL_ADDRESS")
    password = os.getenv("EMAIL_PASSWORD")
    if not sender or not password:
        raise AppError("configuration_error", "EMAIL_ADDRESS and EMAIL_PASSWORD must be configured on the server.")
    return sender, password


def _validate_headers(to: str, subject: str):
    address = parseaddr(to)[1]
    if any(c in to + subject for c in "\r\n") or not address or "@" not in address or not subject.strip():
        raise AppError("invalid_arguments", "A valid recipient and nonempty single-line subject are required.")


@mcp.tool()
@tool_errors
def send_email(to: str, subject: str, body: str) -> str:
    """Send a plain text email via Gmail SMTP."""
    _validate_headers(to, subject)
    sender, password = _smtp_client()

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
        smtp.login(sender, password)
        smtp.sendmail(sender, to, msg.as_string())

    return f"Email sent to {to}."


@mcp.tool()
@tool_errors
def send_email_with_attachment(to: str, subject: str, body: str, file_path: str) -> str:
    """Send an email with a file attachment via Gmail SMTP."""
    _validate_headers(to, subject)
    sender, password = _smtp_client()

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with open(file_path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition",
        f"attachment; filename={os.path.basename(file_path)}",
    )
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
        smtp.login(sender, password)
        smtp.sendmail(sender, to, msg.as_string())

    return f"Email with attachment '{os.path.basename(file_path)}' sent to {to}."


if __name__ == "__main__":
    mcp.run()
