import json
import threading
from datetime import datetime, timezone

from app.db import get_conn
from app.llm import OllamaError, generate, parse_json_response
from app.scraping import find_link_by_keywords, ddg_search_results, fetch_page_text_only, fetch_soup

# "Deep enrichment": a slower, optional second pass on top of the fast
# sector/activity enrichment (app/enrichment.py), gathering concrete, recent,
# specific facts ("signals") an outreach email can open with instead of a
# paraphrase of the homepage. Caps signals per company to keep later
# drafting prompts small — quality over quantity.
MAX_SIGNALS = 5
NEWS_MAX_RESULTS = 5
BLOG_KEYWORDS = ["blog", "news", "press", "insights", "resources"]
CAREERS_KEYWORDS = ["career", "job", "hiring", "join us", "join-us", "we're hiring"]

PROMPT_TEMPLATE = """You are extracting concrete, specific, recent facts about a company that could \
be used to open a personalized cold outreach email — the kind of detail that proves someone actually \
researched the company, not generic facts about what they do in general.

Company: {company_name}

Below is raw text gathered from three sources: their careers/jobs page (if found), their blog/news \
page (if found), and a web search for recent news mentions. From ALL of this, extract up to 5 signals \
— concrete, specific, and ideally recent: a funding round, a product launch, a partnership, an award, \
press coverage, a specific open job role, or a specific recent blog post topic. Skip anything vague, \
generic, or that reads like boilerplate "about us" marketing copy.

Careers/jobs page text:
\"\"\"
{careers_text}
\"\"\"

Blog/news page text:
\"\"\"
{blog_text}
\"\"\"

Web search results for recent news:
\"\"\"
{news_text}
\"\"\"

Respond with ONLY a JSON object of this exact shape:
{{"signals": [{{"type": "news"|"blog"|"hiring"|"other", "text": "<one specific, concrete detail, one \
sentence, phrased as a fact not a summary>", "source_url": "<url if known, else empty string>", \
"date_if_known": "<date or period if known, else empty string>"}}, ...]}}
If nothing specific or concrete is found in any of the three sources, respond with {{"signals": []}} \
— do not invent or guess.
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


def _find_page(website_url: str, keywords: list[str]) -> tuple[str | None, str | None]:
    soup = fetch_soup(website_url)
    if not soup:
        return None, None
    link = find_link_by_keywords(soup, website_url, keywords)
    if not link:
        return None, None
    return link, fetch_page_text_only(link)


def _gather_news_text(company_name: str) -> str:
    results = ddg_search_results(f"{company_name} funding launch news announcement", max_results=NEWS_MAX_RESULTS)
    if not results:
        return ""
    return "\n".join(f"- {r['title']} ({r['url']}): {r['snippet']}" for r in results)


def _synthesize_signals(company_name: str, careers_text: str | None, blog_text: str | None, news_text: str) -> list[dict]:
    if not (careers_text or blog_text or news_text):
        return []

    raw = generate(
        PROMPT_TEMPLATE.format(
            company_name=company_name,
            careers_text=(careers_text or "none found")[:3000],
            blog_text=(blog_text or "none found")[:3000],
            news_text=(news_text or "none found")[:3000],
        )
    )
    parsed = parse_json_response(raw)
    signals = []
    for s in parsed.get("signals", [])[:MAX_SIGNALS]:
        if not isinstance(s, dict) or not s.get("text"):
            continue
        signals.append(
            {
                "type": s.get("type", "other"),
                "text": s.get("text", ""),
                "source_url": s.get("source_url", ""),
                "date_if_known": s.get("date_if_known", ""),
            }
        )
    return signals


def _deep_enrich_one(conn, company_row) -> None:
    company_id = company_row["id"]
    name = company_row["name"]
    website = company_row["website_url"]
    now = datetime.now(timezone.utc).isoformat()

    conn.execute("UPDATE companies SET deep_enrichment_status = 'running' WHERE id = ?", (company_id,))
    conn.commit()

    careers_text = blog_text = None
    if website:
        _, careers_text = _find_page(website, CAREERS_KEYWORDS)
        _, blog_text = _find_page(website, BLOG_KEYWORDS)

    news_text = _gather_news_text(name)

    try:
        signals = _synthesize_signals(name, careers_text, blog_text, news_text)
    except (OllamaError, ValueError) as e:
        conn.execute(
            "UPDATE companies SET deep_enrichment_status = 'failed', deep_enrichment_error = ?, "
            "updated_at = ? WHERE id = ?",
            (str(e), now, company_id),
        )
        return

    conn.execute(
        "UPDATE companies SET signals = ?, deep_enrichment_status = 'done', deep_enrichment_error = NULL, "
        "updated_at = ? WHERE id = ?",
        (json.dumps(signals), now, company_id),
    )


def _run_deep_enrichment():
    with get_conn() as conn:
        companies = conn.execute(
            "SELECT id, name, website_url FROM companies WHERE enrichment_status = 'done' "
            "AND deep_enrichment_status IN ('not_started', 'failed') ORDER BY id"
        ).fetchall()

    _set_state(
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
                _deep_enrich_one(conn, company)
            except Exception as e:  # noqa: BLE001 - keep the pipeline alive across bad companies
                conn.execute(
                    "UPDATE companies SET deep_enrichment_status = 'failed', deep_enrichment_error = ? "
                    "WHERE id = ?",
                    (f"Unexpected error: {e}", company["id"]),
                )
        with _state_lock:
            _state["done"] += 1

    _set_state(running=False, current_company=None, finished_at=datetime.now(timezone.utc).isoformat())


def start_deep_enrichment() -> bool:
    """Starts the deep-enrichment loop in a background thread. Returns False if already running."""
    with _state_lock:
        if _state["running"]:
            return False
        _state["running"] = True

    thread = threading.Thread(target=_run_deep_enrichment, daemon=True)
    thread.start()
    return True
