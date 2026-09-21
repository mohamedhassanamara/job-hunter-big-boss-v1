#!/usr/bin/env python3
"""Pre-send review package generator — no LLM calls, purely packages data.

For a given queue, writes a single markdown file containing, per item:
company/contact, the raw text signals were extracted from, the extracted
signals, the current draft (subject+body) exactly as it would be sent, and
a best-effort guess at which signal (if any) the draft's opening used.

Meant to be handed to a Claude Code session for review before sending —
this script does not judge anything itself, it only assembles the evidence.

Usage:
    python review_queue.py <queue_id>
"""

import json
import sys
from pathlib import Path

from app.db import get_conn

REVIEWS_DIR = Path(__file__).resolve().parent / "reviews"


def _guess_signal_used(body: str, signals: list[dict]) -> str | None:
    """Rough word-overlap heuristic against the first ~300 chars of the
    draft — the LLM paraphrases when opening the email, so this is a hint
    for the reviewer to verify, not a reliable match on its own."""
    if not body or not signals:
        return None
    opening = body[:300].lower()
    best_text, best_score = None, 0.0
    for s in signals:
        words = [w for w in (s.get("text") or "").lower().split() if len(w) > 4]
        if not words:
            continue
        score = sum(1 for w in words if w in opening) / len(words)
        if score > best_score:
            best_score, best_text = score, s.get("text")
    return best_text if best_score >= 0.25 else None


def build_review_markdown(queue_id: int) -> str:
    with get_conn() as conn:
        queue = conn.execute("SELECT * FROM queues WHERE id = ?", (queue_id,)).fetchone()
        if not queue:
            raise SystemExit(f"No queue with id {queue_id}.")
        items = conn.execute(
            "SELECT qi.*, c.first_name, c.last_name, c.title, c.email, "
            "co.name AS company_name, co.signals, co.signals_source_text "
            "FROM queue_items qi "
            "JOIN contacts c ON c.id = qi.contact_id "
            "JOIN companies co ON co.id = qi.company_id "
            "WHERE qi.queue_id = ? ORDER BY qi.position",
            (queue_id,),
        ).fetchall()

    lines = [
        f"# Pre-send review — {queue['name']} (queue #{queue_id})",
        "",
        f"Status: `{queue['status']}` · {len(items)} item(s)",
        "",
    ]

    for item in items:
        signals = json.loads(item["signals"]) if item["signals"] else []
        source = json.loads(item["signals_source_text"]) if item["signals_source_text"] else {}
        contact_name = f"{item['first_name'] or ''} {item['last_name'] or ''}".strip()

        lines.append(f"## {item['company_name']} — {contact_name} ({item['title'] or 'unknown title'})")
        lines.append(
            f"- Contact: {item['email']} · Item id: {item['id']} · "
            f"send_status: `{item['send_status']}` · review_status: `{item['review_status']}`"
        )
        lines.append("")

        lines.append("### 1-2. Raw source text signals were extracted from")
        for label, url_key, text_key in (
            ("Careers/jobs page", "careers_url", "careers_text"),
            ("Blog/news page", "blog_url", "blog_text"),
        ):
            url = source.get(url_key)
            text = source.get(text_key)
            lines.append(f"**{label}**{f' ({url})' if url else ''}:")
            lines.append("```")
            lines.append((text or "").strip() or "(none found)")
            lines.append("```")
        lines.append("**Web search (news):**")
        lines.append("```")
        lines.append((source.get("news_text") or "").strip() or "(none found)")
        lines.append("```")
        lines.append("")

        lines.append("### 3. Extracted signals")
        if signals:
            for s in signals:
                extra = []
                if s.get("source_url"):
                    extra.append(f"source: {s['source_url']}")
                if s.get("date_if_known"):
                    extra.append(f"date: {s['date_if_known']}")
                suffix = f" ({', '.join(extra)})" if extra else ""
                lines.append(f"- [{s.get('type')}] {s.get('text')}{suffix}")
        else:
            lines.append("_(none — no hook found; draft used activity_summary only)_")
        lines.append("")

        lines.append("### 4. Final draft (exactly as it would be sent)")
        lines.append(f"**Subject:** {item['subject'] or '(none)'}")
        lines.append("")
        lines.append("```")
        lines.append(item["body"] or "(none)")
        lines.append("```")
        lines.append("")

        lines.append("### 5. Signal used in opening (best-effort guess — verify manually)")
        guess = _guess_signal_used(item["body"] or "", signals)
        lines.append(guess or "_(no clear match detected — may be generic, or paraphrased beyond this heuristic)_")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python review_queue.py <queue_id>", file=sys.stderr)
        return 1
    try:
        queue_id = int(sys.argv[1])
    except ValueError:
        print("queue_id must be an integer.", file=sys.stderr)
        return 1

    markdown = build_review_markdown(queue_id)
    REVIEWS_DIR.mkdir(exist_ok=True)
    out_path = REVIEWS_DIR / f"queue_{queue_id}_review.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"Wrote review package to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
