import threading
from datetime import datetime, timezone

from app.cv import get_active_profile
from app.db import get_conn
from app.llm import OllamaError, generate, parse_json_response

PROMPT_TEMPLATE = """You are drafting a short, personalized cold outreach email from a job-seeking \
candidate to a contact at a company, for the candidate to review and send themselves.

Candidate background:
- Experience level: {experience_level}
- Skills: {skills}
- Target roles: {target_roles}
- Why this candidate's background fits companies like this one: {target_sector_profile}

Recipient:
- Name: {contact_name}
- Title: {contact_title}
- Company: {company_name}

What the company does: {activity_summary}
{fit_rationale_line}

Write a concise, specific, non-generic cold email (under 150 words) that:
- Is addressed to the recipient by first name
- Shows the candidate has actually looked at what the company does (reference their activity specifically)
- Briefly connects the candidate's relevant background to that company's work
- Ends with a light, low-pressure call to action (e.g. a short call, or just "open to sending my resume")
- Signs off with "[Your Name]" as a placeholder for the candidate to fill in themselves
- Avoids generic filler phrases like "I hope this email finds you well" or "I am writing to express my interest"

Respond with ONLY a JSON object of this exact shape:
{{"subject": "<email subject line>", "body": "<full email body, plain text, newlines as \\n>"}}
"""

_state_lock = threading.Lock()
_state = {
    "running": False,
    "total": 0,
    "done": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}


def get_status() -> dict:
    with _state_lock:
        return dict(_state)


def _set_state(**kwargs):
    with _state_lock:
        _state.update(kwargs)


def generate_draft_content(profile: dict, contact: dict, company: dict) -> tuple[str, str]:
    """Builds the prompt, calls the local LLM, and returns (subject, body).

    Pure content generation — no DB access — so both the Drafts tab
    (email_drafts table) and the Queues feature (queue_items table) can
    share the exact same generation logic.

    Raises OllamaError, ValueError, or KeyError on failure.
    """
    contact_name = f"{contact['first_name']} {contact['last_name']}".strip() or "there"

    fit_rationale_line = ""
    if company.get("fit_rationale"):
        fit_rationale_line = f"Why this candidate is a good fit for this company: {company['fit_rationale']}"

    prompt = PROMPT_TEMPLATE.format(
        experience_level=profile.get("experience_level", "unknown"),
        skills=", ".join(profile.get("skills", [])),
        target_roles=", ".join(profile.get("target_roles", [])),
        target_sector_profile=profile.get("target_sector_profile", ""),
        contact_name=contact_name,
        contact_title=contact.get("title") or "unknown title",
        company_name=company["name"],
        activity_summary=company.get("activity_summary") or "unknown",
        fit_rationale_line=fit_rationale_line,
    )

    raw = generate(prompt)
    parsed = parse_json_response(raw)
    return parsed["subject"], parsed["body"]


def fetch_companies_and_contacts(company_ids: list[int], cv_profile_id: int) -> tuple[dict, list[dict]]:
    """Shared lookup: companies (with their fit rationale for the given CV) and
    their contacts, for a set of company ids. Used by both drafting and queue creation."""
    with get_conn() as conn:
        placeholders = ",".join("?" * len(company_ids))
        companies = {
            row["id"]: dict(row)
            for row in conn.execute(
                f"SELECT co.id, co.name, co.activity_summary, fs.fit_rationale FROM companies co "
                f"LEFT JOIN fit_scores fs ON fs.company_id = co.id AND fs.cv_profile_id = ? "
                f"WHERE co.id IN ({placeholders})",
                [cv_profile_id, *company_ids],
            ).fetchall()
        }
        contacts = [
            dict(row)
            for row in conn.execute(
                f"SELECT id, company_id, first_name, last_name, title, email FROM contacts "
                f"WHERE company_id IN ({placeholders})",
                company_ids,
            ).fetchall()
        ]
    return companies, contacts


def _draft_one(conn, cv_profile_id: int, profile: dict, contact: dict, company: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        subject, body = generate_draft_content(profile, contact, company)
    except (OllamaError, ValueError, KeyError) as e:
        conn.execute(
            "INSERT INTO email_drafts (contact_id, company_id, cv_profile_id, status, error, "
            "created_at, updated_at) VALUES (?, ?, ?, 'failed', ?, ?, ?) "
            "ON CONFLICT(contact_id) DO UPDATE SET "
            "cv_profile_id=excluded.cv_profile_id, status='failed', error=excluded.error, "
            "updated_at=excluded.updated_at",
            (contact["id"], company["id"], cv_profile_id, str(e), now, now),
        )
        return

    conn.execute(
        "INSERT INTO email_drafts (contact_id, company_id, cv_profile_id, subject, body, status, "
        "error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'drafted', NULL, ?, ?) "
        "ON CONFLICT(contact_id) DO UPDATE SET "
        "cv_profile_id=excluded.cv_profile_id, subject=excluded.subject, body=excluded.body, "
        "status='drafted', error=NULL, updated_at=excluded.updated_at",
        (contact["id"], company["id"], cv_profile_id, subject, body, now, now),
    )


def _run_drafting(company_ids: list[int], cv_profile_id: int, profile: dict):
    companies, contacts = fetch_companies_and_contacts(company_ids, cv_profile_id)

    _set_state(
        total=len(contacts),
        done=0,
        error=None,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
    )

    for contact in contacts:
        company = companies.get(contact["company_id"])
        if not company:
            continue
        with get_conn() as conn:
            try:
                _draft_one(conn, cv_profile_id, profile, contact, company)
            except Exception as e:  # noqa: BLE001 - keep the pipeline alive across bad contacts
                conn.execute(
                    "INSERT INTO email_drafts (contact_id, company_id, cv_profile_id, status, "
                    "error, created_at, updated_at) VALUES (?, ?, ?, 'failed', ?, ?, ?) "
                    "ON CONFLICT(contact_id) DO UPDATE SET cv_profile_id=excluded.cv_profile_id, "
                    "status='failed', error=excluded.error, updated_at=excluded.updated_at",
                    (
                        contact["id"],
                        company["id"],
                        cv_profile_id,
                        f"Unexpected error: {e}",
                        datetime.now(timezone.utc).isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        with _state_lock:
            _state["done"] += 1

    _set_state(running=False, finished_at=datetime.now(timezone.utc).isoformat())


def start_drafting(company_ids: list[int]) -> tuple[bool, str | None]:
    if not company_ids:
        return False, "No companies selected."

    with _state_lock:
        if _state["running"]:
            return False, "Draft generation is already running."

    profile = get_active_profile()
    if not profile:
        return False, "No CV uploaded yet. Upload a CV first."

    with _state_lock:
        if _state["running"]:
            return False, "Draft generation is already running."
        _state["running"] = True

    thread = threading.Thread(
        target=_run_drafting, args=(company_ids, profile["id"], profile), daemon=True
    )
    thread.start()
    return True, None


def list_drafts(ids: list[int] | None = None) -> list[dict]:
    query = (
        "SELECT d.id, d.subject, d.body, d.status, d.error, d.updated_at, "
        "c.id AS contact_id, c.first_name, c.last_name, c.title, c.email, "
        "co.id AS company_id, co.name AS company_name "
        "FROM email_drafts d "
        "JOIN contacts c ON c.id = d.contact_id "
        "JOIN companies co ON co.id = d.company_id "
    )
    params: list = []
    if ids:
        query += f"WHERE d.id IN ({','.join('?' * len(ids))}) "
        params = ids
    query += "ORDER BY co.name COLLATE NOCASE, c.first_name COLLATE NOCASE"

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def update_draft(draft_id: int, subject: str, body: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE email_drafts SET subject = ?, body = ?, updated_at = ? WHERE id = ?",
            (subject, body, datetime.now(timezone.utc).isoformat(), draft_id),
        )
    return cur.rowcount > 0
