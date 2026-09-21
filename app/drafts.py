import json
import re
import threading
from datetime import datetime, timezone

from app.config import SENDER_NAME
from app.cv import get_active_profile
from app.db import get_conn
from app.llm import OllamaError, generate, parse_json_response

GOOD_SUBJECT_EXAMPLES = [
    "Your LeNIA-Chat-1.5B release",
    "Question about your edge deployment plans",
    "Scaling PILoT's mobile layer",
    "Saw your Series B announcement",
    "Question about the ML infra team's next hire",
]

BAD_SUBJECT_EXAMPLES = [
    "Bridging AI and Mobile for PILoT",
    "Re: LeNIA-Chat-1.5B and your production pipeline",
    "Where AI Meets Real-World Impact",
    "Exploring Synergies",
    "Following Up on Your Growth",
]

# Canonical 5-part cold outreach structure — the fixed quality bar for every
# draft. Length/rhythm still varies naturally per company (how much the
# signal actually supports), but the shape itself (greeting, hook,
# self-intro, connection, ask, sign-off) does not.
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
{signals_line}

═══════════════════════════════════════
EMAIL STRUCTURE (5 parts, in this exact order, every time)
═══════════════════════════════════════

1. GREETING
   "Hi {contact_first_name}," — the contact's real first name, never "there", "Hi team,", or any
   generic greeting.

2. THE HOOK (1-2 sentences)
   Open with the single most specific, real signal available for this company (from the signals
   listed above, if any — a real launch, blog post, hiring signal, news item). This must be
   something only findable by actually looking at this specific company, never a paraphrase of
   their generic activity summary ("you focus on AI development" is NOT a hook).
   Never frame it as a reply ("Re:", "Following up on", "responding to") — this is cold outreach,
   state that plainly through tone, not through a fake-reply premise.
   If no signal is listed above, open instead with one specific, real detail from the activity
   summary — still concrete, never "I've been following your work" or "I've been admiring [Company]'s [thing]".

3. BRIEF SELF-INTRODUCTION (1 sentence)
   One sentence stating who the candidate is in terms relevant to THIS company, not a skills dump
   — e.g. "I'm a software engineer who moves AI prototypes into production systems." Phrase this
   differently each time depending on what's actually relevant to this company. This should read
   as a natural continuation of the hook, not a separate resume paragraph.

4. THE CONNECTION / REAL NEED (2-3 sentences)
   The most important part. Connect the hook to ONE concrete thing the candidate could specifically
   help this company with — not a list of skills, one clear, specific connection between what the
   company is doing/building and what the candidate brings. Frame this around the company's likely
   need (inferred from the signal, company stage, or the role being hired for), not around the
   candidate's resume: given what the signal reveals, what real problem might they have that the
   candidate can speak to? Write toward that problem.
   Never write: bare skills lists ("I specialize in Flutter, FastAPI, and MQTT"), vague
   self-assessment ("my background is built for this" / "is built for exactly this"), or buzzword
   bridging language ("bridge between AI and deployment"). Never write a general claim about a
   CATEGORY of company ("deploying sensitive health-tech tools usually requires...", "companies at
   this stage typically need...") — every claim must reference something specific and real about
   THIS company (the actual signal text), not an inference about companies like them. If there isn't
   enough specific signal to make a real point, reference the signal more directly and briefly
   instead of padding with a generalization.

5. THE ASK (1 sentence)
   One clear, specific, low-friction ask that follows logically from something explicitly stated
   earlier in THIS email (the specific signal, or the specific connection drawn in part 4) — never
   an assumption not established in the email itself (e.g. don't ask about "your deployment
   pipeline" unless the email actually established that a pipeline exists). Never generic ("do you
   have 10 minutes to chat?") and never passive/weak ("I'd love to send my resume" / "Are you open
   to..."). Vary the exact wording per email, do not reuse the same ask verbatim across companies.

SIGN-OFF: "Best," (or similar) then the candidate's actual name on its own line: {candidate_name}
— never "[Your Name]" or any bracketed placeholder. This name must be filled in every time, zero
exceptions.

═══════════════════════════════════════
LENGTH & VARIATION
═══════════════════════════════════════
- Target 4-6 sentences total across parts 2-4 combined (not counting greeting/sign-off) — enough to
  justify the ask, not so much it reads as a cover letter. Run slightly longer or shorter depending
  on how much genuine substance the signal supports; do not pad if there's nothing real to say.
- Vary sentence rhythm and paragraph breaks across companies — do not let every email fall into an
  identical 3-paragraph block shape. Some can be 2 short paragraphs, some 3 — it should read like a
  different person considered each one, not a mail-merge.

═══════════════════════════════════════
SUBJECT LINE RULES
═══════════════════════════════════════
- Must be concrete and specific — reference the actual signal or a direct, plain statement of intent.
- FORBIDDEN: "Re:", "Fwd:", or anything implying prior correspondence.
- FORBIDDEN: "X and Y" / "Bridging X and Y" / "X meets Y" template patterns.
- FORBIDDEN: vague corporate-sounding phrases ("Exploring Synergies", "Following Up on Your Growth").
  Examples of GOOD subject lines:
{good_subjects}
  Examples of BAD subject lines (never write like these):
{bad_subjects}

═══════════════════════════════════════
WRITING STYLE — FORBIDDEN PATTERNS
═══════════════════════════════════════
- No em dashes (—) or en dashes (–) for dramatic pauses, ever. Use a period or comma instead.
- No curly/smart quotes or apostrophes (‘ ’ “ ”). Plain straight ' and " only.
- No "I've been following/admiring [Company]'s [thing]" opener template.
- No "my background is built for exactly this" or similar self-assessment claims — show relevance
  through the specific connection in part 4 instead of asserting it abstractly.
- No bare skills-list sentences ("I specialize in X, Y, and Z").
- No passive, hedgy asks ("I'd love to..." / "Are you open to...").
- No generic filler ("I hope this email finds you well", "I am writing to express my interest").

═══════════════════════════════════════
FEW-SHOT EXAMPLES (good, full structure — 4 different companies, sectors, and tones)
═══════════════════════════════════════
These 4 examples exist to show that the STRUCTURE (greeting, hook, self-intro, connection, ask,
sign-off) stays fixed while the actual content, sentence rhythm, paragraph count, and framing change
per company. Do NOT reuse sentence patterns, phrasing, or rhythm from these examples. Each email you
write must be built from the specific signal and company context given above, not adapted from the
wording below — these are structural references only, not templates to fill in.

--- Example 1: health-tech, product-launch signal, 2 paragraphs ---
Subject: Vitalis's remote monitoring launch

Hi Priya,

Noticed Vitalis just rolled out real-time vitals monitoring for home care patients. I build the
backend systems that keep health data pipelines reliable under real clinical load, not demo
conditions.

A remote monitoring feature like this tends to run into trouble specifically around dropped
connections and delayed readings from patient devices, since that's where continuous vitals data is
most fragile. I've worked on similar resilience layers for streaming sensor data and can walk through
what worked.

Would a quick call make sense to compare notes on how you're handling that?

Best,
Mohamed Hassen Amara

--- Example 2: dev-tools startup, technical blog-post signal, 3 short paragraphs, punchy ---
Subject: Your CLI error-rate breakdown

Hi Marcus,

Read your writeup on the CLI's error log and the retry-storm fix. That's the kind of debugging I end
up doing a lot of.

I write Python tooling and spend most of my time on CLI ergonomics and agent-facing APIs specifically.

If you're hiring for that kind of work, happy to send over a couple of examples.

Best,
Mohamed Hassen Amara

--- Example 3: e-commerce platform, hiring signal (no product launch to reference), longer ---
Subject: Your open backend infra role

Hi Elena,

Saw the opening for a backend engineer on Kesh's infra team. Scaling checkout and inventory sync for
a growing catalog is a different kind of problem than it looks like from the outside.

I'm a backend engineer who has spent most of the last three years on exactly that: high-throughput,
consistency-sensitive systems.

Given the role's on the infra team specifically, I'd guess sync latency as SKU count grows is closer
to the actual pain point than anything user-facing. That's the kind of problem I like working on, and
I'd be glad to talk through specifics if the role's still open.

Best,
Mohamed Hassen Amara

--- Example 4: no strong signal available, activity-summary opener, very short ---
Subject: Question about Northwind's routing engine

Hi Tomas,

Northwind's real-time route optimization for last-mile delivery is the kind of problem I like
solving. I build backend systems for exactly that: high-throughput, latency-sensitive routing and
tracking.

Worth ten minutes to see if there's a fit for what you're building next?

Best,
Mohamed Hassen Amara

Respond with ONLY a JSON object of this exact shape:
{{"subject": "<email subject line>", "body": "<full email body, plain text, newlines as \\n>"}}
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
    """Belt-and-suspenders cleanup: the prompt forbids em dashes and curly
    quotes, but strip any that slip through anyway rather than sending them."""
    for bad, good in _SMART_CHAR_MAP.items():
        text = text.replace(bad, good)
    return text


_PLACEHOLDER_RE = re.compile(r"\[\s*your\s*name\s*\]", re.IGNORECASE)
_ANY_BRACKET_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{1,40}\]")
_REPLY_PREFIX_RE = re.compile(r"^\s*(re|fwd)\s*:", re.IGNORECASE)
_DASH_RE = re.compile("[—–]")


def _validate_draft(subject: str, body: str) -> None:
    """Safety net against specific known regressions, run right before a
    draft is saved — independent of whatever the prompt currently says, so a
    future prompt tweak can't silently let these back in unnoticed."""
    if _ANY_BRACKET_PLACEHOLDER_RE.search(body):
        raise ValueError("Generated draft still contains an unfilled bracketed placeholder in the body.")
    if _REPLY_PREFIX_RE.match(subject):
        raise ValueError(f"Generated subject line uses a reply prefix (Re:/Fwd:): {subject!r}")
    if _DASH_RE.search(subject) or _DASH_RE.search(body):
        raise ValueError("Generated draft contains an em dash or en dash after sanitization.")


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
    contact_first_name = contact.get("first_name") or "there"

    fit_rationale_line = ""
    if company.get("fit_rationale"):
        fit_rationale_line = f"Why this candidate is a good fit for this company: {company['fit_rationale']}"

    signals_line = ""
    signals = company.get("signals")
    if isinstance(signals, str):
        try:
            signals = json.loads(signals)
        except (json.JSONDecodeError, TypeError):
            signals = None
    if signals:
        signal_texts = [s.get("text") for s in signals if isinstance(s, dict) and s.get("text")]
        if signal_texts:
            signals_line = (
                "Specific, concrete, recent signals about this company (prefer these over the "
                "general activity summary as the opening hook):\n"
                + "\n".join(f"- {t}" for t in signal_texts)
            )

    prompt = PROMPT_TEMPLATE.format(
        experience_level=profile.get("experience_level", "unknown"),
        skills=", ".join(profile.get("skills", [])),
        target_roles=", ".join(profile.get("target_roles", [])),
        target_sector_profile=profile.get("target_sector_profile", ""),
        contact_name=contact_name,
        contact_first_name=contact_first_name,
        contact_title=contact.get("title") or "unknown title",
        company_name=company["name"],
        activity_summary=company.get("activity_summary") or "unknown",
        fit_rationale_line=fit_rationale_line,
        signals_line=signals_line,
        good_subjects="\n".join(f'  - "{s}"' for s in GOOD_SUBJECT_EXAMPLES),
        bad_subjects="\n".join(f'  - "{s}"' for s in BAD_SUBJECT_EXAMPLES),
        candidate_name=SENDER_NAME or "the candidate",
    )

    raw = generate(prompt)
    parsed = parse_json_response(raw)
    subject = _sanitize_text(parsed["subject"])
    body = _sanitize_text(parsed["body"])
    body = _PLACEHOLDER_RE.sub(SENDER_NAME or "the candidate", body)
    _validate_draft(subject, body)
    return subject, body


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
