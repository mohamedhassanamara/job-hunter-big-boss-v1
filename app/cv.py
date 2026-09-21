import hashlib
import io
import json
from datetime import datetime, timezone

import pdfplumber

from app.config import CV_UPLOADS_DIR
from app.db import get_conn
from app.llm import generate, parse_json_response

MAX_CV_CHARS = 12000

PROMPT_TEMPLATE = """You are analyzing a candidate's CV/resume text to build a profile that will
later be used to match them against companies for outreach.

Based ONLY on the text below, respond with a single JSON object with exactly these keys:
- "skills": an array of strings, the candidate's key skills/technologies (concise, 5-20 items)
- "experience_level": a short label, e.g. "Entry-level", "Mid-level (3-5 yrs)", "Senior (8+ yrs)", "Executive/Leadership"
- "domains_worked_in": an array of strings, industries/domains the candidate has worked in (e.g. "fintech", "healthcare", "e-commerce")
- "target_roles": an array of strings, job titles/roles this candidate is well suited for next
- "target_sector_profile": a paragraph (3-6 sentences) describing what kinds of companies, sectors,
  and company stages/sizes would most value this candidate's background, and why. Be specific enough
  to use as a filter when screening a list of companies for outreach fit.

Resume text:
\"\"\"
{text}
\"\"\"

Respond with ONLY the JSON object, no other text.
"""


def extract_text(file_bytes: bytes, filename: str) -> str:
    if filename.lower().endswith(".pdf"):
        text_parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        text = "\n".join(text_parts)
    else:
        text = file_bytes.decode("utf-8", errors="replace")

    text = text.strip()
    if not text:
        raise ValueError("Could not extract any text from the uploaded CV.")
    return text


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_profile(text: str) -> dict:
    raw = generate(PROMPT_TEMPLATE.format(text=text[:MAX_CV_CHARS]))
    return parse_json_response(raw)


def save_resume_pdf(file_bytes: bytes, content_hash: str) -> str | None:
    """Saves the uploaded PDF bytes under CV_UPLOADS_DIR, keyed by the CV's
    content hash, and returns the path to attach to outreach emails for this
    profile. Returns None for non-PDF uploads (e.g. .txt) — nothing to attach."""
    CV_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    path = CV_UPLOADS_DIR / f"{content_hash}.pdf"
    path.write_bytes(file_bytes)
    return str(path)


def find_by_hash(content_hash: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM cv_profiles WHERE content_hash = ?", (content_hash,)
        ).fetchone()
    return _row_to_profile(row)


def save_profile(
    filename: str, raw_text: str, content_hash: str, parsed: dict, resume_pdf_path: str | None = None
) -> dict:
    """Inserts a new CV profile and makes it the active one."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("UPDATE cv_profiles SET is_active = 0")
        cur = conn.execute(
            "INSERT INTO cv_profiles (filename, content_hash, raw_text, skills, experience_level, "
            "domains_worked_in, target_roles, target_sector_profile, resume_pdf_path, is_active, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                filename,
                content_hash,
                raw_text,
                json.dumps(parsed.get("skills", [])),
                parsed.get("experience_level", "unknown"),
                json.dumps(parsed.get("domains_worked_in", [])),
                json.dumps(parsed.get("target_roles", [])),
                parsed.get("target_sector_profile", ""),
                resume_pdf_path,
                now,
                now,
            ),
        )
        profile_id = cur.lastrowid
    return get_profile_by_id(profile_id)


def activate_profile(profile_id: int) -> bool:
    with get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM cv_profiles WHERE id = ?", (profile_id,)).fetchone()
        if not exists:
            return False
        conn.execute("UPDATE cv_profiles SET is_active = 0")
        conn.execute("UPDATE cv_profiles SET is_active = 1 WHERE id = ?", (profile_id,))
    return True


def _row_to_profile(row) -> dict | None:
    if not row:
        return None
    profile = dict(row)
    profile["skills"] = json.loads(profile["skills"] or "[]")
    profile["domains_worked_in"] = json.loads(profile["domains_worked_in"] or "[]")
    profile["target_roles"] = json.loads(profile["target_roles"] or "[]")
    return profile


def get_active_profile() -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM cv_profiles WHERE is_active = 1").fetchone()
    return _row_to_profile(row)


def get_profile_by_id(profile_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM cv_profiles WHERE id = ?", (profile_id,)).fetchone()
    return _row_to_profile(row)


def list_profiles() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, experience_level, is_active, created_at FROM cv_profiles "
            "ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
