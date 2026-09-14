import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    domain TEXT,
    website_url TEXT,
    sector TEXT,
    activity_summary TEXT,
    size_signal TEXT,
    raw_scrape_text TEXT,
    enrichment_status TEXT NOT NULL DEFAULT 'pending',
    enrichment_error TEXT,
    fit_status TEXT NOT NULL DEFAULT 'unscored',
    fit_score INTEGER,
    fit_rationale TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    first_name TEXT,
    last_name TEXT,
    title TEXT,
    email TEXT
);

CREATE TABLE IF NOT EXISTS email_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL UNIQUE REFERENCES contacts(id),
    company_id INTEGER NOT NULL REFERENCES companies(id),
    subject TEXT,
    body TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at TEXT,
    updated_at TEXT
);

-- Single-row table: only the most recently uploaded CV's profile is kept.
CREATE TABLE IF NOT EXISTS cv_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    filename TEXT,
    raw_text TEXT,
    skills TEXT,
    experience_level TEXT,
    domains_worked_in TEXT,
    target_roles TEXT,
    target_sector_profile TEXT,
    updated_at TEXT
);
"""


def _ensure_data_dir():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_conn():
    _ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn):
    """Add columns introduced after a database file may already exist."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(companies)")}
    for col, ddl in (
        ("fit_status", "TEXT NOT NULL DEFAULT 'unscored'"),
        ("fit_score", "INTEGER"),
        ("fit_rationale", "TEXT"),
    ):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {ddl}")


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
