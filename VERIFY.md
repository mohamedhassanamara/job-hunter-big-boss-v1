# How to run the pre-send review (simple steps)

Do this before clicking "Start Sending" on a queue, so you catch fabricated
signals or generic-sounding drafts before they go out.

## 1. Find the queue's id

In the app's Queues tab, click the queue you want to check — the URL bar
inside the app doesn't show it, so instead just note the queue as listed
(e.g. "Queue #3 - Sep 20"), or ask: `curl -s http://127.0.0.1:8000/api/queues`
and read the `"id"` field for the queue with that name.

## 2. Generate the review package

In your terminal, from the project folder:

```bash
python review_queue.py <queue_id>
```

Example: `python review_queue.py 3`

This writes `reviews/queue_3_review.md` — no LLM calls, it just gathers the
data (raw scraped text, extracted signals, the current draft, everything).

## 3. Open a Claude Code session in this repo and ask it to review the file

Paste this (swap in your file name):

> Read `reviews/queue_3_review.md`. For each company, give me two verdicts:
> 1. **Signal verification** — is the extracted signal actually supported by
>    the raw source text, or fabricated/stretched? Flag anything unsupported.
> 2. **Draft quality verdict** — good / needs work / weak, with a one-line
>    reason. Check specifically for: generic template language (e.g. "I've
>    been looking into X and the Y focus of your platform," "I believe my
>    background could be a strong asset"), whether the opening hook is
>    genuinely specific to that company (not something that could be
>    copy-pasted to any other company), whether the close is a clear,
>    low-friction ask rather than a vague "are you open to a chat," and
>    whether this would actually get a reply or reads as outreach spam.
>
> Give me the output as a table: company name, signal verdict, draft
> verdict, one-line reason. Flag anything "weak" or "fabricated" clearly.

## 4. Decide what to do with each flagged item

For anything Claude Code flags:
- **Fabricated signal** → go edit that draft in the Queues tab (or just mark
  it `signal_flagged` and fix the body yourself before sending).
- **Weak/generic draft** → edit the subject/body directly in the Queues tab
  item card, then set its review status to `approved` once you're happy.
- **Good** → mark it `approved`.

## 5. Apply verdicts in bulk (optional — skip this for just 1-2 items)

If you have many items to update at once, write a small JSON file mapping
item id → verdict, e.g. `verdicts.json`:

```json
{
  "12": "approved",
  "13": "signal_flagged",
  "14": "draft_flagged"
}
```

(Item ids are shown in the Queues tab on each draft card, and in the review
markdown file next to "Item id:".)

Then run:

```bash
python apply_review.py <queue_id> verdicts.json
```

This updates all of them in one shot — no need to click through each item's
dropdown individually.

## 6. Check the Queues tab, then send

Back in the app's Queues tab, the "N of M items not reviewed" banner should
now be gone (or smaller). This is a reminder, not a lock — "Start Sending"
still works even with unreviewed items, for when you want to skip review on
a small trusted batch.
