#!/usr/bin/env python3
"""Bulk-apply pre-send review verdicts to queue items — no LLM calls, just a
direct database update. Meant to run after a Claude Code review session
(see review_queue.py) has produced verdicts for each item.

Usage:
    python apply_review.py <queue_id> <mapping.json>

mapping.json shape: {"<item_id>": "<review_status>", ...}
review_status must be one of: not_reviewed, signal_flagged, draft_flagged, approved
"""

import json
import sys

from app.queues import bulk_set_review_status


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python apply_review.py <queue_id> <mapping.json>", file=sys.stderr)
        return 1

    try:
        queue_id = int(sys.argv[1])
    except ValueError:
        print("queue_id must be an integer.", file=sys.stderr)
        return 1

    with open(sys.argv[2], encoding="utf-8") as f:
        raw_mapping = json.load(f)

    try:
        mapping = {int(item_id): status for item_id, status in raw_mapping.items()}
    except (ValueError, AttributeError):
        print("mapping.json must be an object of {item_id: review_status}.", file=sys.stderr)
        return 1

    updated, warnings = bulk_set_review_status(queue_id, mapping)

    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    print(f"Updated {updated}/{len(mapping)} item(s) in queue {queue_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
