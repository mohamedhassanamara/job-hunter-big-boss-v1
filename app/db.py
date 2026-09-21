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
    signals TEXT,
    signals_source_text TEXT,
    deep_enrichment_status TEXT NOT NULL DEFAULT 'not_started',
    deep_enrichment_error TEXT,
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

-- One row per uploaded CV, identified by a hash of its extracted text so
-- re-uploading the same CV is recognized instead of re-analyzed.
CREATE TABLE IF NOT EXISTS cv_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    raw_text TEXT,
    skills TEXT,
    experience_level TEXT,
    domains_worked_in TEXT,
    target_roles TEXT,
    target_sector_profile TEXT,
    resume_pdf_path TEXT,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

-- Fit score is a property of a (company, CV) pair, not of the company alone,
-- since the same company list can be matched against different CVs.
CREATE TABLE IF NOT EXISTS fit_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    cv_profile_id INTEGER NOT NULL REFERENCES cv_profiles(id),
    fit_status TEXT NOT NULL DEFAULT 'unscored',
    fit_score INTEGER,
    fit_rationale TEXT,
    updated_at TEXT,
    UNIQUE(company_id, cv_profile_id)
);

CREATE TABLE IF NOT EXISTS email_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL UNIQUE REFERENCES contacts(id),
    company_id INTEGER NOT NULL REFERENCES companies(id),
    cv_profile_id INTEGER REFERENCES cv_profiles(id),
    subject TEXT,
    body TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at TEXT,
    updated_at TEXT
);

-- A queue is a fixed batch of at most QUEUE_ITEM_CAP outreach emails sent
-- one at a time, QUEUE_SEND_INTERVAL_SECONDS apart, by the background
-- sender loop (app/queues.py). Status: draft -> sending -> completed, with
-- paused as a side-state of sending.
CREATE TABLE IF NOT EXISTS queues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    cv_profile_id INTEGER REFERENCES cv_profiles(id),
    next_send_at TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS queue_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES queues(id),
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    company_id INTEGER NOT NULL REFERENCES companies(id),
    position INTEGER NOT NULL,
    subject TEXT,
    body TEXT,
    generation_status TEXT NOT NULL DEFAULT 'pending',
    send_status TEXT NOT NULL DEFAULT 'pending',
    review_status TEXT NOT NULL DEFAULT 'not_reviewed',
    sent_at TEXT,
    error_message TEXT,
    updated_at TEXT,
    UNIQUE(queue_id, contact_id)
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


def _table_exists(conn, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def _columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_legacy_single_cv(conn):
    """Migrate a pre-multi-CV database: a single `cv_profile` row and
    fit_status/fit_score/fit_rationale columns directly on `companies`."""
    import hashlib

    if "cv_profile_id" not in _columns(conn, "email_drafts"):
        conn.execute("ALTER TABLE email_drafts ADD COLUMN cv_profile_id INTEGER REFERENCES cv_profiles(id)")

    if not _table_exists(conn, "cv_profile"):
        return

    old_cv = conn.execute("SELECT * FROM cv_profile WHERE id = 1").fetchone()
    company_cols = _columns(conn, "companies")
    has_old_fit_cols = {"fit_status", "fit_score", "fit_rationale"} <= company_cols

    if old_cv:
        content_hash = hashlib.sha256((old_cv["raw_text"] or "").encode("utf-8")).hexdigest()
        existing = conn.execute(
            "SELECT id FROM cv_profiles WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if existing:
            cv_profile_id = existing["id"]
        else:
            conn.execute("UPDATE cv_profiles SET is_active = 0")
            cur = conn.execute(
                "INSERT INTO cv_profiles (filename, content_hash, raw_text, skills, "
                "experience_level, domains_worked_in, target_roles, target_sector_profile, "
                "is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    old_cv["filename"],
                    content_hash,
                    old_cv["raw_text"],
                    old_cv["skills"],
                    old_cv["experience_level"],
                    old_cv["domains_worked_in"],
                    old_cv["target_roles"],
                    old_cv["target_sector_profile"],
                    old_cv["updated_at"],
                    old_cv["updated_at"],
                ),
            )
            cv_profile_id = cur.lastrowid

        if has_old_fit_cols:
            rows = conn.execute(
                "SELECT id, fit_status, fit_score, fit_rationale, updated_at FROM companies "
                "WHERE fit_status IS NOT NULL AND fit_status != 'unscored'"
            ).fetchall()
            for r in rows:
                conn.execute(
                    "INSERT INTO fit_scores (company_id, cv_profile_id, fit_status, fit_score, "
                    "fit_rationale, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(company_id, cv_profile_id) DO NOTHING",
                    (r["id"], cv_profile_id, r["fit_status"], r["fit_score"], r["fit_rationale"], r["updated_at"]),
                )

        conn.execute(
            "UPDATE email_drafts SET cv_profile_id = ? WHERE cv_profile_id IS NULL",
            (cv_profile_id,),
        )

    conn.execute("DROP TABLE cv_profile")

    if has_old_fit_cols:
        for col in ("fit_status", "fit_score", "fit_rationale"):
            try:
                conn.execute(f"ALTER TABLE companies DROP COLUMN {col}")
            except sqlite3.OperationalError:
                pass  # sqlite < 3.35: leave the now-unused column in place


def _migrate_add_signals_columns(conn):
    existing = _columns(conn, "companies")
    for col, ddl in (
        ("signals", "TEXT"),
        ("signals_source_text", "TEXT"),
        ("deep_enrichment_status", "TEXT NOT NULL DEFAULT 'not_started'"),
        ("deep_enrichment_error", "TEXT"),
    ):
        if col not in existing:
            conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {ddl}")


def _migrate_add_review_status_column(conn):
    if "review_status" not in _columns(conn, "queue_items"):
        conn.execute("ALTER TABLE queue_items ADD COLUMN review_status TEXT NOT NULL DEFAULT 'not_reviewed'")


def _migrate_add_resume_pdf_path_column(conn):
    if "resume_pdf_path" not in _columns(conn, "cv_profiles"):
        conn.execute("ALTER TABLE cv_profiles ADD COLUMN resume_pdf_path TEXT")


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate_legacy_single_cv(conn)
        _migrate_add_signals_columns(conn)
        _migrate_add_review_status_column(conn)
        _migrate_add_resume_pdf_path_column(conn)
