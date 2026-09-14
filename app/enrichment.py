import json
import threading
from datetime import datetime, timezone

from app.db import get_conn
from app.llm import OllamaError, generate, parse_json_response
from app.scraping import get_company_text

PROMPT_TEMPLATE = """You are analyzing a company based on text scraped from its website.

Based ONLY on the text below, respond with a single JSON object with exactly these keys:
- "sector": a short label for the company's industry/sector (e.g. "Fintech / Payments", "Healthcare SaaS", "Industrial Manufacturing")
- "activity_summary": one paragraph (2-4 sentences) describing what the company actually does, in plain language
- "size_signal": a brief guess at company size/stage if there's any signal in the text (e.g. "startup, ~10-50 employees", "large enterprise", "unknown"), otherwise "unknown"

If the text doesn't contain enough information to determine something, make a reasonable best guess rather than leaving it blank.

Company name: {company_name}

Website text:
\"\"\"
{text}
\"\"\"

Respond with ONLY the JSON object, no other text.
"""

_state_lock = threading.Lock()
_state = {
    "running": False,
    "total": 0,
    "done": 0,
    "current_company": None,
    "started_at": None,
    "finished_at": None,
}


def get_status() -> dict:
    with _state_lock:
        return dict(_state)


def _set_state(**kwargs):
    with _state_lock:
        _state.update(kwargs)


def _enrich_one(conn, company_row) -> None:
    name = company_row["name"]
    domain = company_row["domain"]
    company_id = company_row["id"]
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "UPDATE companies SET enrichment_status = 'enriching' WHERE id = ?",
        (company_id,),
    )
    conn.commit()

    source_url, text = get_company_text(name, domain)
    if not text:
        conn.execute(
            "UPDATE companies SET enrichment_status = 'failed', "
            "enrichment_error = ?, updated_at = ? WHERE id = ?",
            ("Could not find or fetch a usable company website.", now, company_id),
        )
        return

    try:
        raw = generate(PROMPT_TEMPLATE.format(company_name=name, text=text))
        parsed = parse_json_response(raw)
    except (OllamaError, json.JSONDecodeError, ValueError) as e:
        conn.execute(
            "UPDATE companies SET enrichment_status = 'failed', enrichment_error = ?, "
            "website_url = ?, raw_scrape_text = ?, updated_at = ? WHERE id = ?",
            (str(e), source_url, text, now, company_id),
        )
        return

    conn.execute(
        "UPDATE companies SET enrichment_status = 'done', enrichment_error = NULL, "
        "website_url = ?, sector = ?, activity_summary = ?, size_signal = ?, "
        "raw_scrape_text = ?, updated_at = ? WHERE id = ?",
        (
            source_url,
            parsed.get("sector", "unknown"),
            parsed.get("activity_summary", ""),
            parsed.get("size_signal", "unknown"),
            text,
            now,
            company_id,
        ),
    )


def _run_enrichment():
    with get_conn() as conn:
        companies = conn.execute(
            "SELECT id, name, domain FROM companies "
            "WHERE enrichment_status IN ('pending', 'failed') ORDER BY id"
        ).fetchall()

    _set_state(
        running=True,
        total=len(companies),
        done=0,
        current_company=None,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
    )

    for company in companies:
        _set_state(current_company=company["name"])
        with get_conn() as conn:
            try:
                _enrich_one(conn, company)
            except Exception as e:  # noqa: BLE001 - keep the pipeline alive across bad companies
                conn.execute(
                    "UPDATE companies SET enrichment_status = 'failed', enrichment_error = ? "
                    "WHERE id = ?",
                    (f"Unexpected error: {e}", company["id"]),
                )
        with _state_lock:
            _state["done"] += 1

    _set_state(running=False, current_company=None, finished_at=datetime.now(timezone.utc).isoformat())


def start_enrichment() -> bool:
    """Starts the enrichment loop in a background thread. Returns False if already running."""
    with _state_lock:
        if _state["running"]:
            return False
        _state["running"] = True

    thread = threading.Thread(target=_run_enrichment, daemon=True)
    thread.start()
    return True
