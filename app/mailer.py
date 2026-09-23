import mimetypes
import smtplib
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

from app.config import (
    DAILY_SEND_CAP,
    GMAIL_ADDRESS,
    GMAIL_APP_PASSWORD,
    REPLY_TO_ADDRESS,
    SENDER_NAME,
    SMTP_HOST,
    SMTP_PORT,
)
from app.db import get_conn


class MailerNotConfigured(Exception):
    pass


def is_configured() -> bool:
    return bool(GMAIL_ADDRESS and GMAIL_APP_PASSWORD)


def _attach_resume(msg: MIMEMultipart, resume_pdf_path: str | None, resume_filename: str | None = None) -> None:
    if not resume_pdf_path:
        return
    path = Path(resume_pdf_path)
    if not path.is_file():
        raise MailerNotConfigured(f"This CV profile's resume PDF is missing on disk: {path}")

    attach_name = resume_filename or path.name
    content_type = mimetypes.guess_type(attach_name)[0] or "application/pdf"
    subtype = content_type.split("/", 1)[1]
    part = MIMEApplication(path.read_bytes(), _subtype=subtype)
    part.add_header("Content-Disposition", "attachment", filename=attach_name)
    msg.attach(part)


def send_email(
    to_addr: str,
    subject: str,
    body: str,
    resume_pdf_path: str | None = None,
    resume_filename: str | None = None,
) -> None:
    """Sends one email via SMTP, attaching resume_pdf_path (the sending
    queue's own CV profile's resume, if it has one) when given, presented to
    the recipient under resume_filename (the candidate's original CV
    filename) rather than the hash-named path it's stored under on disk.
    Raises on any failure — callers decide how to record that as a failed
    queue item."""
    if not is_configured():
        raise MailerNotConfigured(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD are not set in .env — see .env.example."
        )

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = formataddr((SENDER_NAME, GMAIL_ADDRESS))
    msg["To"] = to_addr
    if REPLY_TO_ADDRESS:
        msg["Reply-To"] = REPLY_TO_ADDRESS
    msg.attach(MIMEText(body, "plain", "utf-8"))
    _attach_resume(msg, resume_pdf_path, resume_filename)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, [to_addr], msg.as_string())


def get_sent_today_count() -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM queue_items WHERE send_status = 'sent' "
            "AND substr(sent_at, 1, 10) = ?",
            (today,),
        ).fetchone()
    return row["n"]


def daily_cap_reached() -> bool:
    return get_sent_today_count() >= DAILY_SEND_CAP
