import json
import re
import threading
from datetime import datetime, timezone

from app.config import SENDER_NAME
from app.cv import get_active_profile
from app.db import get_conn
from app.llm import OllamaError, generate, parse_json_response

# ═══════════════════════════════════════════════════════════════════════
# Two-step generation pipeline:
#   Step 1 (ANGLE_MATCHER_PROMPT) — pure reasoning, JSON out. Finds ONE real
#     technical angle: Tier 1 (a recent signal) if there is one, else Tier 2
#     (the company's core product/domain, still a real technical detail, not
#     a launch). Only aborts if there is truly zero data to work from.
#   Step 2 (HUMAN_DRAFTER_PROMPT) — direct candidate-intent outreach, plain
#     text out, using only Step 1's JSON as input. Introduces the candidate
#     by name/role, grounds one paragraph in the real signal/product, and
#     asks a direct, humble question about engineering opportunities — never
#     a rhetorical tech-stack interrogation.
#   Step 3 — deterministic Python post-processing: a fully deterministic
#     subject line (never LLM-generated), dash/quote stripping, hardcoded
#     "Best regards," sign-off, a completeness sanity check, and lint_draft
#     as the final automated guardrail. No prose step is trusted un-checked.
# ═══════════════════════════════════════════════════════════════════════

ANGLE_MATCHER_PROMPT = """You are a technical analyst identifying a conversation bridge between a \
company and an engineer's background.

INPUTS:
Company Domain / Summary: {company_summary}
Recent Signals / News: {signals}
Candidate Skills / Projects: {candidate_profile}

TASK:
Identify ONE specific technical angle to reach out on:
- Tier 1 (Preferred): A recent release, post, or technical milestone from the signals.
- Tier 2 (Fallback if no recent news): A specific engineering challenge inherent to their core
  product/sector (e.g. IoT edge telemetry, streaming APIs, low-latency client architecture). Still a
  real, specific technical detail about THIS company's actual domain — never a vague category like
  "bringing AI-driven wellbeing tools" or "supporting digital transformation".

Only respond with {{"insufficient_signal": true}} if the company summary AND the signals are BOTH
completely empty or contain zero technical detail to work from — do not use it just because there's no
recent launch. A Tier 2 angle from the core domain is always preferable to aborting.

Output ONLY valid JSON, no markdown wrapping, either the schema below or the insufficient_signal object:
{{
  "signal_or_product_name": "<the specific product/feature/release name or core technical domain to reference, a few words>",
  "candidate_matching_experience": "<1 sentence on the specific past project, pipeline, or stack the candidate built that directly mirrors this>"
}}
"""

HUMAN_DRAFTER_PROMPT = """You are writing a concise, professional cold outreach email from Mohamed \
Hassen Amara (a software engineer) to a founder, CTO, or engineering lead ({first_name}) at \
{company_name}.

INPUTS:
- Recipient Name: {first_name}
- Company Name: {company_name}
- Relevant Product / Feature: {signal_or_product_name}
- Candidate Relevance: {candidate_matching_experience}

OBJECTIVE:
Introduce yourself, show that you understand what they build, highlight your directly relevant
engineering background, and ask if they are open to an introductory conversation or exploring
engineering additions to their team.

RULES:
1. GREETING: "Hi {first_name}," or "Hello {first_name},"
2. PARAGRAPH 1 (Identity & Intent): Introduce yourself as a software engineer and state directly that
   you're reaching out regarding potential engineering opportunities at {company_name}.
3. PARAGRAPH 2 (Grounded Connection): Mention their specific product or recent development
   ({signal_or_product_name}) and tie it directly to your hands-on experience
   ({candidate_matching_experience}), mentioning specific frameworks or technologies (e.g. Flutter,
   FastAPI, Docker, local LLMs/RAG, IoT backends).
4. PARAGRAPH 3 (Clear Call to Action): Mention that your resume is attached, and ask if they would be
   open to a brief chat or keeping in touch if they are considering growing their technical team.
5. NO RHETORICAL QUIZZES: Do not ask them how they build their product or whether they plan to adopt
   new tech.
6. NO ROBOTIC OPENINGS: Do NOT start with "The release of X shows a clear focus on..." or "I hope this
   email finds you well."
7. Keep paragraphs short (1-2 sentences each). Total length: 75 to 110 words. Plain straight quotes
   and apostrophes only, no em dashes.
8. Do NOT write a sign-off or your name at the end. The system attaches that automatically.

FEW-SHOT EXAMPLES:

Example 1 (AI / NLP Company):
Subject: Software Engineering / LenguajeNatural.AI - Mohamed Hassen Amara
Body:
Hi Alejandro,

I'm a software engineer reaching out to see if you have any upcoming engineering needs at
LenguajeNatural.AI.

I've been following your work around LeNIA-Chat and localized language models. My recent focus has
been on building and serving specialized LLM endpoints using FastAPI and Mistral, particularly
optimizing local inference pipelines and API latency for real-time applications.

I've attached my CV for your review. If you're open to a brief introductory chat or looking to expand
your engineering team, I'd love to connect.

Example 2 (IoT / Hardware-Cloud Platform):
Subject: Software Engineering / advanticsys - Mohamed Hassen Amara
Body:
Hi Jose,

I'm a software engineer reaching out to explore potential technical opportunities with the advanticsys
team.

I took a close look at your Concordia platform and its edge-to-cloud telemetry infrastructure. I have
hands-on experience building backend pipelines with FastAPI, Spring Boot, and MQTT brokers for
real-time sensor and event ingestion, ensuring high reliability across distributed systems.

I've attached my resume with details on my recent projects. Would you have a few minutes for a brief
introductory conversation this week or next?

---

Output strictly in this format:
Subject: <subject line>
Body:
<email body>
"""

_SMART_CHAR_MAP = {
    "—": ", ",
    "–": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
}


def _sanitize_text(text: str) -> str:
    """Deterministic post-processing step: strip AI typographical artifacts
    the LLM steps are told to avoid but sometimes emit anyway."""
    for bad, good in _SMART_CHAR_MAP.items():
        text = text.replace(bad, good)
    return text


def _build_subject(company_name: str) -> str:
    """Subject line is never LLM-generated — a fixed, recognizable recruiting
    format that reliably contains the company name and candidate name, per
    spec: 'Software Engineering / {Company} - {candidate name}'."""
    return _sanitize_text(f"Software Engineering / {company_name} - {SENDER_NAME or 'the candidate'}")


_ANY_BRACKET_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{1,40}\]|<[A-Za-z][^>\n]{0,40}>")

CONSULTANT_PHRASES = [
    r"seamless transition",
    r"balance between",
    r"strong asset",
    r"production-ready software",
    r"support your production roadmap",
    r"bringing [^.]{0,40} to users",
    r"testament to",
    r"\bdelve\b",
    r"\bspearhead\b",
    r"shows a clear focus on",
    r"handles complex end-to-end",
    r"i hope this email finds you well",
]
_CONSULTANT_PHRASE_RES = [re.compile(p, re.IGNORECASE) for p in CONSULTANT_PHRASES]

# Rhetorical tech-stack interrogation ("Are you currently leveraging RAG to...") —
# the exact pattern this pipeline is meant to have moved away from.
RHETORICAL_QUESTION_PATTERNS = [
    r"are you (currently |guys )?(leveraging|handling|using|rolling|planning|running|utilizing)\b[^?]*\?",
    r"(curious|wondering) (if|whether) you\b[^?]*\?",
]
_RHETORICAL_QUESTION_RES = [re.compile(p, re.IGNORECASE) for p in RHETORICAL_QUESTION_PATTERNS]

MIN_BODY_WORDS = 60
MAX_BODY_WORDS = 130


def lint_draft(subject: str, body: str, company_name: str | None = None) -> list[str]:
    """Automated guardrail run right before a draft is stored — independent
    of whatever the prompts currently say, so a future prompt tweak can't
    silently let a known regression back in. Returns a list of violations;
    a non-empty list means the draft should be flagged as needs_revision
    rather than saved as ready-to-send."""
    errors = []

    if re.match(r"^\s*(re|fwd)\s*:", subject, re.IGNORECASE):
        errors.append("Subject starts with Re:/Fwd:.")
    if SENDER_NAME and SENDER_NAME not in subject:
        errors.append("Subject does not contain the candidate's name.")
    if company_name and company_name not in subject:
        errors.append("Subject does not contain the company name.")

    for pattern in _CONSULTANT_PHRASE_RES:
        match = pattern.search(body)
        if match:
            errors.append(f"Body contains a consultant-speak/robotic-opening phrase: {match.group(0)!r}.")

    for pattern in _RHETORICAL_QUESTION_RES:
        match = pattern.search(body)
        if match:
            errors.append(f"Body contains a rhetorical tech-stack question: {match.group(0)!r}.")

    if _ANY_BRACKET_PLACEHOLDER_RE.search(body):
        errors.append("Body contains an unfilled placeholder (e.g. [Name] or <Company>).")

    word_count = len(body.split())
    if word_count < MIN_BODY_WORDS:
        errors.append(f"Body is too short ({word_count} words, minimum {MIN_BODY_WORDS}).")
    if word_count > MAX_BODY_WORDS:
        errors.append(f"Body is too long ({word_count} words, maximum {MAX_BODY_WORDS}).")

    return errors


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


def _extract_signal_texts(company: dict) -> list[str]:
    signals = company.get("signals")
    if isinstance(signals, str):
        try:
            signals = json.loads(signals)
        except (json.JSONDecodeError, TypeError):
            signals = None
    if not signals:
        return []
    return [s.get("text") for s in signals if isinstance(s, dict) and s.get("text")]


def _run_angle_matcher(profile: dict, company: dict) -> dict | None:
    """Step 1: pure reasoning, JSON out. Prefers a Tier-1 recent signal, falls
    back to a Tier-2 core-domain angle when there's no recent news, and only
    returns None (insufficient_signal) when there is truly zero data —
    company summary AND signals both empty."""
    signal_texts = _extract_signal_texts(company)
    signals_block = "\n".join(f"- {t}" for t in signal_texts) if signal_texts else "(none found)"
    company_summary = company.get("activity_summary") or ""

    if not company_summary.strip() and not signal_texts:
        return None

    candidate_profile = (
        f"Experience level: {profile.get('experience_level', 'unknown')}. "
        f"Skills: {', '.join(profile.get('skills', []))}. "
        f"Target roles: {', '.join(profile.get('target_roles', []))}."
    )

    prompt = ANGLE_MATCHER_PROMPT.format(
        company_summary=f"{company['name']}: {company_summary or 'unknown'}",
        signals=signals_block,
        candidate_profile=candidate_profile,
    )

    raw = generate(prompt)
    parsed = parse_json_response(raw)
    if parsed.get("insufficient_signal"):
        return None
    if not parsed.get("signal_or_product_name") or not parsed.get("candidate_matching_experience"):
        return None
    return parsed


_SUBJECT_BODY_RE = re.compile(r"subject:\s*(.+?)\s*\n+body:\s*\n?(.*)", re.IGNORECASE | re.DOTALL)

# Enough headroom for a full 3-paragraph email so the model's own generation
# limit isn't what's cutting the closing question off mid-sentence.
HUMAN_DRAFTER_NUM_PREDICT = 500


def _parse_subject_body(raw: str) -> tuple[str, str]:
    match = _SUBJECT_BODY_RE.search(raw)
    if not match:
        raise ValueError(f"Could not parse Subject/Body from model output: {raw[:200]!r}")
    subject, body = match.group(1).strip(), match.group(2).strip()
    if not subject or not body:
        raise ValueError("Model output had an empty subject or body.")
    return subject, body


def _run_human_drafter(angle: dict, contact_first_name: str, company_name: str) -> tuple[str, str]:
    """Step 2: prose writing, plain text out. Only sees Step 1's JSON — never
    the raw skills/signals lists — so it can't fall back to a skills dump."""
    prompt = HUMAN_DRAFTER_PROMPT.format(
        first_name=contact_first_name,
        company_name=company_name,
        signal_or_product_name=angle["signal_or_product_name"],
        candidate_matching_experience=angle["candidate_matching_experience"],
    )
    raw = generate(prompt, json_format=False, num_predict=HUMAN_DRAFTER_NUM_PREDICT)
    return _parse_subject_body(raw)


def _is_incomplete(full_body: str) -> bool:
    """Sanity check on the fully assembled body (with sign-off): too few
    paragraph breaks or too few words means the draft got cut off mid-way,
    so it should never be saved as a ready-to-send draft."""
    return full_body.count("\n\n") < 2 or len(full_body.split()) < 35


def generate_draft_content(profile: dict, contact: dict, company: dict) -> dict:
    """Runs the 3-step pipeline (angle matcher -> human drafter -> deterministic
    post-processing + lint) and returns:
        {"status": "drafted" | "needs_revision", "subject": str | None,
         "body": str | None, "notes": list[str]}

    Pure content generation — no DB access — so both the Drafts tab
    (email_drafts table) and the Queues feature (queue_items table) can
    share the exact same generation logic.

    Raises OllamaError, ValueError, or KeyError on hard failures (LLM
    unreachable, unparseable output) — status/notes only cover *soft*
    failures (insufficient signal, lint violations) which still get a
    reviewable row instead of an opaque error.
    """
    contact_first_name = contact.get("first_name") or "there"

    angle = _run_angle_matcher(profile, company)
    if angle is None:
        return {
            "status": "needs_revision",
            "subject": None,
            "body": None,
            "notes": [
                "Step 1 (angle matcher) found zero usable data (no company summary and no signals) "
                "to build a real email around — skipped generation instead of writing generic filler."
            ],
        }

    company_name = company["name"]
    _model_subject, body = _run_human_drafter(angle, contact_first_name, company_name)

    # Step 3 — deterministic post-processing (code, not LLM). Subject is never
    # taken from the model — a fixed recruiting-format template guarantees it
    # cleanly contains the company name and candidate name every time.
    subject = _build_subject(company_name)
    body = _sanitize_text(body)
    cleaned_body = body.strip()
    full_body = f"{cleaned_body}\n\nBest regards,\n{SENDER_NAME or 'the candidate'}"

    if _is_incomplete(full_body):
        return {
            "status": "needs_revision",
            "subject": subject,
            "body": full_body,
            "notes": [
                "Draft looks incomplete (too few paragraphs or too short) — likely cut off before "
                "the closing call to action. Regenerate rather than send as-is."
            ],
        }

    errors = lint_draft(subject, full_body, company_name)
    if errors:
        return {"status": "needs_revision", "subject": subject, "body": full_body, "notes": errors}
    return {"status": "drafted", "subject": subject, "body": full_body, "notes": []}


def fetch_companies_and_contacts(company_ids: list[int], cv_profile_id: int) -> tuple[dict, list[dict]]:
    """Shared lookup: companies (with their fit rationale for the given CV) and
    their contacts, for a set of company ids. Used by both drafting and queue creation."""
    with get_conn() as conn:
        placeholders = ",".join("?" * len(company_ids))
        companies = {
            row["id"]: dict(row)
            for row in conn.execute(
                f"SELECT co.id, co.name, co.activity_summary, co.signals, fs.fit_rationale FROM companies co "
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
        result = generate_draft_content(profile, contact, company)
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

    error = "; ".join(result["notes"]) if result["notes"] else None
    conn.execute(
        "INSERT INTO email_drafts (contact_id, company_id, cv_profile_id, subject, body, status, "
        "error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(contact_id) DO UPDATE SET "
        "cv_profile_id=excluded.cv_profile_id, subject=excluded.subject, body=excluded.body, "
        "status=excluded.status, error=excluded.error, updated_at=excluded.updated_at",
        (
            contact["id"],
            company["id"],
            cv_profile_id,
            result["subject"],
            result["body"],
            result["status"],
            error,
            now,
            now,
        ),
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
