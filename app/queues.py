import threading
import time
from datetime import datetime, timedelta, timezone

from app.config import DAILY_SEND_CAP, QUEUE_ITEM_CAP, QUEUE_SEND_INTERVAL_SECONDS, SENDER_LOOP_POLL_SECONDS
from app.cv import get_active_profile, get_profile_by_id
from app.db import get_conn
from app.drafts import fetch_companies_and_contacts, generate_draft_content
from app.llm import OllamaError
from app.mailer import daily_cap_reached, get_sent_today_count, is_configured, send_email

# ---------- Draft-content generation (runs once per queue, right after creation) ----------

_gen_lock = threading.Lock()
_gen_state: dict[int, dict] = {}


def get_generation_status(queue_id: int) -> dict:
    with _gen_lock:
        return dict(_gen_state.get(queue_id, {"running": False, "total": 0, "done": 0}))


def _set_gen_state(queue_id: int, **kwargs):
    with _gen_lock:
        _gen_state.setdefault(queue_id, {"running": False, "total": 0, "done": 0})
        _gen_state[queue_id].update(kwargs)


def _run_generation(queue_id: int, profile: dict, companies: dict, contacts: list[dict]):
    _set_gen_state(queue_id, running=True, total=len(contacts), done=0, started_at=datetime.now(timezone.utc).isoformat())

    for contact in contacts:
        company = companies.get(contact["company_id"])
        now = datetime.now(timezone.utc).isoformat()
        with get_conn() as conn:
            conn.execute(
                "UPDATE queue_items SET generation_status = 'generating' WHERE queue_id = ? AND contact_id = ?",
                (queue_id, contact["id"]),
            )
            conn.commit()
            try:
                subject, body = generate_draft_content(profile, contact, company)
                conn.execute(
                    "UPDATE queue_items SET subject = ?, body = ?, generation_status = 'drafted', "
                    "send_status = 'pending', error_message = NULL, updated_at = ? "
                    "WHERE queue_id = ? AND contact_id = ?",
                    (subject, body, now, queue_id, contact["id"]),
                )
            except (OllamaError, ValueError, KeyError) as e:
                conn.execute(
                    "UPDATE queue_items SET generation_status = 'failed', send_status = 'failed', "
                    "error_message = ?, updated_at = ? WHERE queue_id = ? AND contact_id = ?",
                    (f"Draft generation error: {e}", now, queue_id, contact["id"]),
                )
        with _gen_lock:
            _gen_state[queue_id]["done"] += 1

    _set_gen_state(queue_id, running=False, finished_at=datetime.now(timezone.utc).isoformat())


# ---------- Queue creation ----------

def create_queue(company_ids: list[int]) -> dict:
    if not company_ids:
        raise ValueError("No companies selected.")

    profile = get_active_profile()
    if not profile:
        raise ValueError("No CV uploaded yet. Upload a CV first.")

    companies, contacts = fetch_companies_and_contacts(company_ids, profile["id"])
    if not contacts:
        raise ValueError("Selected companies have no contacts.")
    if len(contacts) > QUEUE_ITEM_CAP:
        raise ValueError(
            f"Selected companies have {len(contacts)} contacts, exceeding the "
            f"{QUEUE_ITEM_CAP}-item queue cap. Select fewer companies."
        )

    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM queues").fetchone()["n"]
        name = f"Queue #{count + 1} - {datetime.now(timezone.utc).strftime('%b %d')}"
        cur = conn.execute(
            "INSERT INTO queues (name, status, cv_profile_id, created_at, updated_at) "
            "VALUES (?, 'draft', ?, ?, ?)",
            (name, profile["id"], now, now),
        )
        queue_id = cur.lastrowid
        for position, contact in enumerate(contacts):
            conn.execute(
                "INSERT INTO queue_items (queue_id, contact_id, company_id, position, "
                "generation_status, send_status, updated_at) VALUES (?, ?, ?, ?, 'pending', 'pending', ?)",
                (queue_id, contact["id"], contact["company_id"], position, now),
            )

    thread = threading.Thread(target=_run_generation, args=(queue_id, profile, companies, contacts), daemon=True)
    thread.start()

    return get_queue(queue_id)


# ---------- Queue lifecycle ----------

def start_queue(queue_id: int) -> tuple[bool, str | None]:
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM queues WHERE id = ?", (queue_id,)).fetchone()
        if not row:
            return False, "Queue not found."
        if row["status"] != "draft":
            return False, f"Queue is already {row['status']}."
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE queues SET status = 'sending', next_send_at = ?, updated_at = ? WHERE id = ?",
            (now, now, queue_id),
        )
    return True, None


def pause_queue(queue_id: int) -> tuple[bool, str | None]:
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM queues WHERE id = ?", (queue_id,)).fetchone()
        if not row:
            return False, "Queue not found."
        if row["status"] != "sending":
            return False, "Queue is not currently sending."
        conn.execute(
            "UPDATE queues SET status = 'paused', updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), queue_id),
        )
    return True, None


def resume_queue(queue_id: int) -> tuple[bool, str | None]:
    """Resuming restarts the full send interval countdown (simplest, most
    predictable behavior — see README) rather than remembering exactly how
    much of the countdown was left when paused."""
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM queues WHERE id = ?", (queue_id,)).fetchone()
        if not row:
            return False, "Queue not found."
        if row["status"] != "paused":
            return False, "Queue is not paused."
        now = datetime.now(timezone.utc)
        next_at = (now + timedelta(seconds=QUEUE_SEND_INTERVAL_SECONDS)).isoformat()
        conn.execute(
            "UPDATE queues SET status = 'sending', next_send_at = ?, updated_at = ? WHERE id = ?",
            (next_at, now.isoformat(), queue_id),
        )
    return True, None


def retry_item(queue_id: int, item_id: int) -> tuple[bool, str | None]:
    with get_conn() as conn:
        item = conn.execute(
            "SELECT qi.*, c.first_name, c.last_name, c.title FROM queue_items qi "
            "JOIN contacts c ON c.id = qi.contact_id WHERE qi.id = ? AND qi.queue_id = ?",
            (item_id, queue_id),
        ).fetchone()
        if not item:
            return False, "Item not found."
        if item["send_status"] != "failed":
            return False, "Only failed items can be retried."

        queue = conn.execute("SELECT * FROM queues WHERE id = ?", (queue_id,)).fetchone()
        now = datetime.now(timezone.utc).isoformat()

        if item["generation_status"] == "failed":
            company = conn.execute("SELECT * FROM companies WHERE id = ?", (item["company_id"],)).fetchone()
            fit = conn.execute(
                "SELECT fit_rationale FROM fit_scores WHERE company_id = ? AND cv_profile_id = ?",
                (item["company_id"], queue["cv_profile_id"]),
            ).fetchone()
            profile = get_profile_by_id(queue["cv_profile_id"])
            company_dict = dict(company)
            company_dict["fit_rationale"] = fit["fit_rationale"] if fit else None
            contact_dict = {
                "id": item["contact_id"],
                "first_name": item["first_name"],
                "last_name": item["last_name"],
                "title": item["title"],
            }
            try:
                subject, body = generate_draft_content(profile, contact_dict, company_dict)
                conn.execute(
                    "UPDATE queue_items SET subject = ?, body = ?, generation_status = 'drafted', "
                    "send_status = 'pending', error_message = NULL, updated_at = ? WHERE id = ?",
                    (subject, body, now, item_id),
                )
            except (OllamaError, ValueError, KeyError) as e:
                conn.execute(
                    "UPDATE queue_items SET error_message = ?, updated_at = ? WHERE id = ?",
                    (f"Draft generation error: {e}", now, item_id),
                )
                return False, str(e)
        else:
            conn.execute(
                "UPDATE queue_items SET send_status = 'pending', error_message = NULL, updated_at = ? WHERE id = ?",
                (now, item_id),
            )

        if queue["status"] == "completed":
            conn.execute(
                "UPDATE queues SET status = 'sending', next_send_at = ?, updated_at = ? WHERE id = ?",
                (now, now, queue_id),
            )
    return True, None


def update_item(queue_id: int, item_id: int, subject: str, body: str) -> tuple[bool, str | None]:
    with get_conn() as conn:
        item = conn.execute(
            "SELECT send_status FROM queue_items WHERE id = ? AND queue_id = ?", (item_id, queue_id)
        ).fetchone()
        if not item:
            return False, "Item not found."
        if item["send_status"] == "sent":
            return False, "This item has already been sent and can no longer be edited."
        conn.execute(
            "UPDATE queue_items SET subject = ?, body = ?, updated_at = ? WHERE id = ?",
            (subject, body, datetime.now(timezone.utc).isoformat(), item_id),
        )
    return True, None


def rename_queue(queue_id: int, name: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE queues SET name = ?, updated_at = ? WHERE id = ?",
            (name, datetime.now(timezone.utc).isoformat(), queue_id),
        )
    return cur.rowcount > 0


# ---------- Pre-send review (see review_queue.py / apply_review.py) ----------

VALID_REVIEW_STATUSES = {"not_reviewed", "signal_flagged", "draft_flagged", "approved"}


def set_review_status(queue_id: int, item_id: int, review_status: str) -> tuple[bool, str | None]:
    if review_status not in VALID_REVIEW_STATUSES:
        return False, f"Invalid review_status: {review_status!r}"
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE queue_items SET review_status = ?, updated_at = ? WHERE id = ? AND queue_id = ?",
            (review_status, datetime.now(timezone.utc).isoformat(), item_id, queue_id),
        )
    if cur.rowcount == 0:
        return False, "Item not found."
    return True, None


def bulk_set_review_status(queue_id: int, updates: dict[int, str]) -> tuple[int, list[str]]:
    """Applies {item_id: review_status} in one go — used by apply_review.py
    (and the Queues tab's bulk-paste action) after a Claude Code review
    session, so verdicts don't have to be applied one item at a time."""
    updated = 0
    warnings = []
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for item_id, status in updates.items():
            if status not in VALID_REVIEW_STATUSES:
                warnings.append(f"item {item_id}: invalid status {status!r}, skipped")
                continue
            cur = conn.execute(
                "UPDATE queue_items SET review_status = ?, updated_at = ? WHERE id = ? AND queue_id = ?",
                (status, now, item_id, queue_id),
            )
            if cur.rowcount:
                updated += 1
            else:
                warnings.append(f"item {item_id}: not found in queue {queue_id}")
    return updated, warnings


# ---------- Reading ----------

def list_queues() -> list[dict]:
    with get_conn() as conn:
        queues = [
            dict(r)
            for r in conn.execute(
                "SELECT q.*, cv.filename AS cv_filename FROM queues q "
                "LEFT JOIN cv_profiles cv ON cv.id = q.cv_profile_id "
                "ORDER BY q.created_at DESC"
            ).fetchall()
        ]
        for q in queues:
            counts = conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN send_status = 'sent' THEN 1 ELSE 0 END) AS sent, "
                "SUM(CASE WHEN send_status = 'failed' THEN 1 ELSE 0 END) AS failed, "
                "SUM(CASE WHEN send_status = 'pending' THEN 1 ELSE 0 END) AS pending, "
                "SUM(CASE WHEN review_status = 'not_reviewed' THEN 1 ELSE 0 END) AS not_reviewed "
                "FROM queue_items WHERE queue_id = ?",
                (q["id"],),
            ).fetchone()
            q.update({k: (v or 0) for k, v in dict(counts).items()})
            q["seconds_until_next_send"] = _seconds_until(q)
    return queues


def get_queue(queue_id: int) -> dict | None:
    with get_conn() as conn:
        queue = conn.execute(
            "SELECT q.*, cv.filename AS cv_filename FROM queues q "
            "LEFT JOIN cv_profiles cv ON cv.id = q.cv_profile_id WHERE q.id = ?",
            (queue_id,),
        ).fetchone()
        if not queue:
            return None
        items = conn.execute(
            "SELECT qi.*, c.first_name, c.last_name, c.email, c.title, co.name AS company_name "
            "FROM queue_items qi "
            "JOIN contacts c ON c.id = qi.contact_id "
            "JOIN companies co ON co.id = qi.company_id "
            "WHERE qi.queue_id = ? ORDER BY qi.position",
            (queue_id,),
        ).fetchall()
    result = dict(queue)
    result["items"] = [dict(i) for i in items]
    result["seconds_until_next_send"] = _seconds_until(result)
    result["not_reviewed_count"] = sum(1 for i in items if i["review_status"] == "not_reviewed")
    return result


def _seconds_until(queue: dict) -> int | None:
    if queue.get("status") != "sending" or not queue.get("next_send_at"):
        return None
    remaining = (datetime.fromisoformat(queue["next_send_at"]) - datetime.now(timezone.utc)).total_seconds()
    return max(0, round(remaining))


def get_send_config() -> dict:
    return {
        "queue_item_cap": QUEUE_ITEM_CAP,
        "send_interval_seconds": QUEUE_SEND_INTERVAL_SECONDS,
        "daily_send_cap": DAILY_SEND_CAP,
        "sent_today": get_sent_today_count(),
        "mailer_configured": is_configured(),
    }


# ---------- Background sender loop ----------
#
# A single long-running thread, started once at app startup, polls every
# SENDER_LOOP_POLL_SECONDS for queues in status='sending' whose next_send_at
# has passed, and sends exactly one item per tick per due queue. All state
# (queue status, next_send_at, each item's send_status) lives in SQLite, so
# restarting the app just resumes this loop — already-'sent' items are never
# re-selected because the "next item to send" query only looks at
# send_status='pending', which is the key duplicate-send safeguard.

_sender_thread_started = False
_sender_thread_lock = threading.Lock()


def ensure_sender_loop_started():
    global _sender_thread_started
    with _sender_thread_lock:
        if _sender_thread_started:
            return
        _sender_thread_started = True
    thread = threading.Thread(target=_sender_loop, daemon=True)
    thread.start()


def _sender_loop():
    while True:
        try:
            _process_tick()
        except Exception:  # noqa: BLE001 - the loop must never die
            pass
        time.sleep(SENDER_LOOP_POLL_SECONDS)


def _process_tick():
    with get_conn() as conn:
        due_ids = [
            r["id"]
            for r in conn.execute("SELECT id, next_send_at FROM queues WHERE status = 'sending'").fetchall()
            if not r["next_send_at"] or datetime.fromisoformat(r["next_send_at"]) <= datetime.now(timezone.utc)
        ]
    for queue_id in due_ids:
        _send_next_item(queue_id)


def _send_next_item(queue_id: int):
    with get_conn() as conn:
        item = conn.execute(
            "SELECT qi.*, c.email FROM queue_items qi JOIN contacts c ON c.id = qi.contact_id "
            "WHERE qi.queue_id = ? AND qi.send_status = 'pending' AND qi.generation_status = 'drafted' "
            "ORDER BY qi.position LIMIT 1",
            (queue_id,),
        ).fetchone()

        if not item:
            remaining = conn.execute(
                "SELECT COUNT(*) AS n FROM queue_items WHERE queue_id = ? AND send_status = 'pending'",
                (queue_id,),
            ).fetchone()["n"]
            if remaining == 0:
                conn.execute(
                    "UPDATE queues SET status = 'completed', updated_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), queue_id),
                )
            return  # nothing drafted-and-pending yet (still generating, or genuinely done)

        if daily_cap_reached():
            return  # try again next tick; stays capped until the day rolls over

        now = datetime.now(timezone.utc).isoformat()
        next_at = (datetime.now(timezone.utc) + timedelta(seconds=QUEUE_SEND_INTERVAL_SECONDS)).isoformat()
        try:
            send_email(item["email"], item["subject"], item["body"])
        except Exception as e:  # noqa: BLE001 - record and move on, never halt the queue
            conn.execute(
                "UPDATE queue_items SET send_status = 'failed', error_message = ?, updated_at = ? WHERE id = ?",
                (str(e), now, item["id"]),
            )
            conn.execute("UPDATE queues SET next_send_at = ? WHERE id = ?", (next_at, queue_id))
            return

        conn.execute(
            "UPDATE queue_items SET send_status = 'sent', sent_at = ?, error_message = NULL, "
            "updated_at = ? WHERE id = ?",
            (now, now, item["id"]),
        )
        conn.execute("UPDATE queues SET next_send_at = ? WHERE id = ?", (next_at, queue_id))
