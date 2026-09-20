import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")
DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "data" / "app.db"))

FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "live.com", "msn.com", "protonmail.com", "gmx.com",
    "mail.com", "yandex.com", "zoho.com",
}

# --- Email queues / sending ---
QUEUE_ITEM_CAP = int(os.environ.get("QUEUE_ITEM_CAP", "20"))
QUEUE_SEND_INTERVAL_SECONDS = int(os.environ.get("QUEUE_SEND_INTERVAL_SECONDS", str(15 * 60)))
SENDER_LOOP_POLL_SECONDS = int(os.environ.get("SENDER_LOOP_POLL_SECONDS", "20"))

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
SENDER_NAME = os.environ.get("SENDER_NAME", "").strip() or GMAIL_ADDRESS
REPLY_TO_ADDRESS = os.environ.get("REPLY_TO_ADDRESS", "").strip()
# Practical safety net only — not a guarantee of Google's actual policy, which can change.
DAILY_SEND_CAP = int(os.environ.get("DAILY_SEND_CAP", "500"))
