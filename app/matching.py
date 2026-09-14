import threading
from datetime import datetime, timezone

from app.cv import get_profile
from app.db import get_conn
from app.llm import OllamaError, generate, parse_json_response

# How many companies to score per LLM call. Enrichment summaries are short
# (a sentence or two each), so a batch this size stays well within context
# for local models while cutting the number of LLM round-trips substantially
# versus one call per company.
BATCH_SIZE = 8

PROMPT_TEMPLATE = """You are scoring how well a list of companies fit a candidate's background, \
for cold-outreach prioritization.

Candidate profile:
- Experience level: {experience_level}
- Skills: {skills}
- Domains worked in: {domains}
- Target roles: {target_roles}
- Target sector profile: {target_sector_profile}

For each company listed below, score fit from 0-100 (100 = extremely strong fit worth \
prioritizing for outreach, 0 = no realistic fit) based on how well the company's sector and \
activity align with the candidate's target sector profile, domains, and skills. Give a \
one-sentence rationale for each score, written as if explaining to the candidate why it's a fit \
(or not).

Companies:
{companies_block}

Respond with ONLY a JSON object of this exact shape, with exactly one entry per company listed \
above (match company_id exactly):
{{"scores": [{{"company_id": <id>, "score": <integer 0-100>, "rationale": "<one sentence>"}}, ...]}}
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


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _format_companies_block(companies: list[dict]) -> str:
    lines = []
    for c in companies:
        lines.append(
            f"- company_id={c['id']} | name={c['name']} | sector={c['sector'] or 'unknown'} | "
            f"size={c['size_signal'] or 'unknown'} | activity={c['activity_summary'] or 'unknown'}"
        )
    return "\n".join(lines)


def _score_batch(conn, profile: dict, batch: list[dict]) -> None:
    for c in batch:
        conn.execute("UPDATE companies SET fit_status = 'scoring' WHERE id = ?", (c["id"],))
    conn.commit()

    prompt = PROMPT_TEMPLATE.format(
        experience_level=profile.get("experience_level", "unknown"),
        skills=", ".join(profile.get("skills", [])),
        domains=", ".join(profile.get("domains_worked_in", [])),
        target_roles=", ".join(profile.get("target_roles", [])),
        target_sector_profile=profile.get("target_sector_profile", ""),
        companies_block=_format_companies_block(batch),
    )

    try:
        raw = generate(prompt)
        parsed = parse_json_response(raw)
    except (OllamaError, ValueError) as e:
        for c in batch:
            conn.execute(
                "UPDATE companies SET fit_status = 'failed', fit_rationale = ? WHERE id = ?",
                (f"Batch scoring error: {e}", c["id"]),
            )
        return

    scores_by_id = {}
    for entry in parsed.get("scores", []):
        try:
            scores_by_id[int(entry["company_id"])] = entry
        except (KeyError, TypeError, ValueError):
            continue

    for c in batch:
        result = scores_by_id.get(c["id"])
        if not result:
            conn.execute(
                "UPDATE companies SET fit_status = 'failed', "
                "fit_rationale = 'Model did not return a score for this company.' WHERE id = ?",
                (c["id"],),
            )
            continue
        try:
            score = max(0, min(100, int(result.get("score", 0))))
        except (TypeError, ValueError):
            score = 0
        conn.execute(
            "UPDATE companies SET fit_status = 'scored', fit_score = ?, fit_rationale = ? "
            "WHERE id = ?",
            (score, result.get("rationale", ""), c["id"]),
        )


def _run_matching(profile: dict):
    with get_conn() as conn:
        companies = [
            dict(row)
            for row in conn.execute(
                "SELECT id, name, sector, activity_summary, size_signal FROM companies "
                "WHERE enrichment_status = 'done' AND fit_status IN ('unscored', 'failed') "
                "ORDER BY id"
            ).fetchall()
        ]

    _set_state(
        total=len(companies),
        done=0,
        error=None,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
    )

    for batch in _chunk(companies, BATCH_SIZE):
        with get_conn() as conn:
            try:
                _score_batch(conn, profile, batch)
            except Exception as e:  # noqa: BLE001 - keep the pipeline alive across bad batches
                for c in batch:
                    conn.execute(
                        "UPDATE companies SET fit_status = 'failed', fit_rationale = ? WHERE id = ?",
                        (f"Unexpected error: {e}", c["id"]),
                    )
        with _state_lock:
            _state["done"] += len(batch)

    _set_state(running=False, finished_at=datetime.now(timezone.utc).isoformat())


def start_matching() -> tuple[bool, str | None]:
    with _state_lock:
        if _state["running"]:
            return False, "Matching is already running."

    profile = get_profile()
    if not profile:
        return False, "No CV uploaded yet. Upload a CV first."

    with _state_lock:
        if _state["running"]:
            return False, "Matching is already running."
        _state["running"] = True

    thread = threading.Thread(target=_run_matching, args=(profile,), daemon=True)
    thread.start()
    return True, None


def reset_scores() -> None:
    """Called when the CV profile changes, so stale scores don't linger."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET fit_status = 'unscored', fit_score = NULL, fit_rationale = NULL "
            "WHERE fit_status != 'unscored'"
        )
