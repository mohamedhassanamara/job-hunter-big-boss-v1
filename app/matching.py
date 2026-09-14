import threading
from datetime import datetime, timezone

from app.cv import get_active_profile
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
    "cv_profile_id": None,
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


def _upsert_fit(conn, company_id: int, cv_profile_id: int, status: str, score=None, rationale=None):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO fit_scores (company_id, cv_profile_id, fit_status, fit_score, fit_rationale, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(company_id, cv_profile_id) DO UPDATE SET "
        "fit_status = excluded.fit_status, fit_score = excluded.fit_score, "
        "fit_rationale = excluded.fit_rationale, updated_at = excluded.updated_at",
        (company_id, cv_profile_id, status, score, rationale, now),
    )


def _score_batch(conn, cv_profile_id: int, profile: dict, batch: list[dict]) -> None:
    for c in batch:
        _upsert_fit(conn, c["id"], cv_profile_id, "scoring")
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
            _upsert_fit(conn, c["id"], cv_profile_id, "failed", rationale=f"Batch scoring error: {e}")
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
            _upsert_fit(
                conn, c["id"], cv_profile_id, "failed",
                rationale="Model did not return a score for this company.",
            )
            continue
        try:
            score = max(0, min(100, int(result.get("score", 0))))
        except (TypeError, ValueError):
            score = 0
        _upsert_fit(conn, c["id"], cv_profile_id, "scored", score=score, rationale=result.get("rationale", ""))


def _run_matching(cv_profile_id: int, profile: dict):
    with get_conn() as conn:
        companies = [
            dict(row)
            for row in conn.execute(
                "SELECT c.id, c.name, c.sector, c.activity_summary, c.size_signal FROM companies c "
                "LEFT JOIN fit_scores fs ON fs.company_id = c.id AND fs.cv_profile_id = ? "
                "WHERE c.enrichment_status = 'done' "
                "AND (fs.id IS NULL OR fs.fit_status IN ('unscored', 'failed')) "
                "ORDER BY c.id",
                (cv_profile_id,),
            ).fetchall()
        ]

    _set_state(
        cv_profile_id=cv_profile_id,
        total=len(companies),
        done=0,
        error=None,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None,
    )

    for batch in _chunk(companies, BATCH_SIZE):
        with get_conn() as conn:
            try:
                _score_batch(conn, cv_profile_id, profile, batch)
            except Exception as e:  # noqa: BLE001 - keep the pipeline alive across bad batches
                for c in batch:
                    _upsert_fit(conn, c["id"], cv_profile_id, "failed", rationale=f"Unexpected error: {e}")
        with _state_lock:
            _state["done"] += len(batch)

    _set_state(running=False, finished_at=datetime.now(timezone.utc).isoformat())


def start_matching() -> tuple[bool, str | None]:
    with _state_lock:
        if _state["running"]:
            return False, "Matching is already running."

    profile = get_active_profile()
    if not profile:
        return False, "No CV uploaded yet. Upload a CV first."

    with _state_lock:
        if _state["running"]:
            return False, "Matching is already running."
        _state["running"] = True

    thread = threading.Thread(target=_run_matching, args=(profile["id"], profile), daemon=True)
    thread.start()
    return True, None
