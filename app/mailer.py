import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import formataddr

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


def send_email(to_addr: str, subject: str, body: str) -> None:
    """Sends one plain-text email via SMTP. Raises on any failure — callers
    decide how to record that as a failed queue item."""
    if not is_configured():
        raise MailerNotConfigured(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD are not set in .env — see .env.example."
        )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((SENDER_NAME, GMAIL_ADDRESS))
    msg["To"] = to_addr
    if REPLY_TO_ADDRESS:
        msg["Reply-To"] = REPLY_TO_ADDRESS

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
