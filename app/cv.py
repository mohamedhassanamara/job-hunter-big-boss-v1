import json
from datetime import datetime, timezone

import pdfplumber
import io

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


def build_profile(text: str) -> dict:
    raw = generate(PROMPT_TEMPLATE.format(text=text[:MAX_CV_CHARS]))
    return parse_json_response(raw)


def save_profile(filename: str, raw_text: str, parsed: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cv_profile (id, filename, raw_text, skills, experience_level, "
            "domains_worked_in, target_roles, target_sector_profile, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "filename=excluded.filename, raw_text=excluded.raw_text, skills=excluded.skills, "
            "experience_level=excluded.experience_level, domains_worked_in=excluded.domains_worked_in, "
            "target_roles=excluded.target_roles, target_sector_profile=excluded.target_sector_profile, "
            "updated_at=excluded.updated_at",
            (
                filename,
                raw_text,
                json.dumps(parsed.get("skills", [])),
                parsed.get("experience_level", "unknown"),
                json.dumps(parsed.get("domains_worked_in", [])),
                json.dumps(parsed.get("target_roles", [])),
                parsed.get("target_sector_profile", ""),
                now,
            ),
        )


def get_profile() -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM cv_profile WHERE id = 1").fetchone()
    if not row:
        return None
    profile = dict(row)
    profile["skills"] = json.loads(profile["skills"] or "[]")
    profile["domains_worked_in"] = json.loads(profile["domains_worked_in"] or "[]")
    profile["target_roles"] = json.loads(profile["target_roles"] or "[]")
    return profile
