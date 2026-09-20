#!/usr/bin/env python3
"""Standalone SMTP smoke test — completely separate from the main app.

Sends exactly one email using the same .env credentials the queue-sending
feature will use, so you can debug SMTP/credential issues in isolation
before trusting the app's sending pipeline. No database or queue logic here.

Usage:
    python test_send.py --to someone@example.com --subject "Test" --body "Hello"
    python test_send.py                      # prompts for the fields interactively
"""

import argparse
import os
import smtplib
import sys
from email.mime.text import MIMEText
from email.utils import formataddr

from dotenv import load_dotenv

load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def prompt(label: str) -> str:
    try:
        return input(f"{label}: ").strip()
    except EOFError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a single test email via SMTP.")
    parser.add_argument("--to", help="Recipient email address")
    parser.add_argument("--subject", help="Email subject")
    parser.add_argument("--body", help="Email body (plain text)")
    args = parser.parse_args()

    to_addr = args.to or prompt("Recipient email")
    subject = args.subject if args.subject is not None else prompt("Subject")
    body = args.body if args.body is not None else prompt("Body")

    if not to_addr:
        print("ERROR: no recipient address given.", file=sys.stderr)
        return 1

    smtp_host = env("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(env("SMTP_PORT", "587") or "587")
    gmail_address = env("GMAIL_ADDRESS")
    gmail_app_password = env("GMAIL_APP_PASSWORD")
    sender_name = env("SENDER_NAME") or gmail_address
    reply_to = env("REPLY_TO_ADDRESS")

    if not gmail_address or not gmail_app_password:
        print(
            "ERROR: GMAIL_ADDRESS and/or GMAIL_APP_PASSWORD are not set in .env.\n"
            "Copy .env.example to .env and fill them in (see the comment there about "
            "generating a Gmail App Password).",
            file=sys.stderr,
        )
        return 1

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, gmail_address))
    msg["To"] = to_addr
    if reply_to:
        msg["Reply-To"] = reply_to

    print(f"Connecting to {smtp_host}:{smtp_port} as {gmail_address} ...")
    try:
        # Note: deliberately no server.set_debuglevel(1) — smtplib's debug
        # log includes the raw AUTH PLAIN command, which is your app password
        # base64-encoded (trivially reversible, not encryption). The
        # exception handlers below print everything needed to debug a
        # failure without that risk.
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            login_resp = server.login(gmail_address, gmail_app_password)
            print(f"Login response: {login_resp}")
            send_errs = server.sendmail(gmail_address, [to_addr], msg.as_string())
    except smtplib.SMTPAuthenticationError as e:
        print(f"\nFAILED: authentication error — {e.smtp_code} {e.smtp_error!r}", file=sys.stderr)
        print(
            "Double-check GMAIL_ADDRESS/GMAIL_APP_PASSWORD, that 2-Step Verification is "
            "enabled, and that the App Password wasn't revoked.",
            file=sys.stderr,
        )
        return 1
    except smtplib.SMTPException as e:
        print(f"\nFAILED: SMTP error — {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"\nFAILED: connection error — {e}", file=sys.stderr)
        return 1

    if send_errs:
        print(f"\nPARTIAL FAILURE: server rejected some recipients: {send_errs}", file=sys.stderr)
        return 1

    print(f"\nSUCCESS: email sent to {to_addr}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
