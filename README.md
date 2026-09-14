# Local Lead-Matching & Outreach Tool

Runs entirely locally: FastAPI + SQLite + Ollama. No mandatory cloud dependency.

## Quick start (already set up once before)

```bash
source .venv/bin/activate
ollama serve &          # skip if already running
python app.py
```

Open http://127.0.0.1:8000

## First-time setup vs. every run

"Setup" below only needs to be done once per machine. "Run" is what you do
every time you want to prep a new outreach batch (new CSV export / updated CV).

## Status

- **Phase 1 — Ingest & Data Model**: done. Upload an Apollo CSV, it's parsed into
  `companies` + `contacts` tables in SQLite, with a domain guessed from each
  contact's email (skipped for free providers like gmail.com).
- **Phase 2 — Company Enrichment**: done. For each company, tries the guessed
  domain first, falls back to a DuckDuckGo HTML search for the homepage, scrapes
  the homepage (+ an About page if found), and asks a local Ollama model to
  extract sector / activity summary / size signal as JSON. Cached in SQLite —
  re-running only processes companies still `pending` or `failed`.
- **Phase 3 — CV Intake & Fit Profile**: done. Upload a CV (PDF or .txt), text
  is extracted (`pdfplumber` for PDFs) and sent to the local LLM, which returns
  skills, experience level, domains worked in, target roles, and a "target
  sector profile" paragraph describing what kinds of companies would value
  this background. Stored as a single latest-CV row in SQLite; re-uploading
  overwrites it.
- **Phase 4 — Matching & Ranking**: done. Scores every enriched company against
  the CV profile using the local LLM, batching several companies per call
  (`BATCH_SIZE = 8` in `app/matching.py`) instead of one call per company —
  the enrichment summaries are short, so a batch fits comfortably in context
  while cutting the number of LLM round-trips roughly 8x. Each company gets a
  0-100 fit score plus a one-sentence rationale, stored in SQLite. If a batch
  comes back incomplete or malformed (more likely with very small/weak local
  models), only the companies missing a score are marked `failed` — the rest
  of the batch's scores are kept, and `failed`/`unscored` companies are picked
  up again on the next "Start Matching" run. Re-uploading a CV resets all
  scores to `unscored` since the fit basis changed.
- **Phase 5 — Email Drafting**: done. Select companies from the ranked list and
  click "Generate Drafts for Selected" — one personalized outreach email is
  drafted per contact (via the local LLM) at each selected company, referencing
  that company's activity summary and the CV's fit rationale. Drafts are
  editable in the UI (subject + body, saved back to SQLite) before export.
  Export all drafts as a single CSV, or as a .zip of one .txt file per
  contact — both meant to be sent manually. Unlike Phase 4's batched scoring,
  drafting is one LLM call per contact (not batched): outreach batches are
  small by nature (you're drafting for your top picks, not your whole list),
  and email quality/personalization matters more here than call-count
  efficiency — a batched prompt asking for several full email bodies at once
  is also more prone to models bleeding details between recipients.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # adjust OLLAMA_MODEL if needed

# Make sure Ollama is running and has a model pulled, e.g.:
ollama pull llama3.1
ollama serve   # if not already running as a background service
```

## Run

```bash
python app.py
```

Then open http://127.0.0.1:8000

1. Upload your Apollo CSV export (needs at least `Company` and `Email`
   columns; `First Name` / `Last Name` / `Title` are used if present).
2. Click "Start Enrichment" — this can take a while (one LLM call + a couple
   of HTTP fetches per company). Progress is polled in the UI.
3. Review the companies table: sector, activity summary, size signal, and
   enrichment status (`pending` / `enriching` / `done` / `failed`, with an
   error message on failures — usually "couldn't find/fetch a website").
4. Upload your CV (PDF or .txt) to get a fit profile: skills, experience
   level, domains worked in, target roles, and a target-sector-profile
   paragraph. Re-uploading replaces the stored profile (and resets any
   existing fit scores, since they're no longer valid against the old CV).
5. Click "Start Matching" to score every enriched company against your CV
   profile (0-100 fit score + one-line rationale), processed in batches of 8.
   The ranked table sorts by fit score descending. Re-running only rescopes
   companies that are still `unscored` or `failed`.
6. Check the box next to the companies you want to reach out to in the ranked
   table, then click "Generate Drafts for Selected". One draft per contact at
   each selected company appears in the Outreach Drafts section — edit the
   subject/body inline and hit Save, then use "Export all as CSV" or
   "Export all as .txt (zip)" to get files you can send manually.

Re-uploading a CSV or re-running enrichment is safe: companies are matched by
name, and only `pending`/`failed` companies are (re-)enriched.

## Troubleshooting

- **Enrichment status stuck on `failed` with "Could not reach Ollama..."**:
  make sure `ollama serve` is running and `ollama list` shows the model named
  in `OLLAMA_MODEL`.
- **Most companies come back `failed` with "Could not find or fetch a usable
  website"**: the DuckDuckGo HTML scrape can occasionally get rate-limited or
  its markup can shift — re-running later usually picks up where it left off
  since only `failed`/`pending` companies are retried.
- **Port 8000 already in use**: another instance is probably still running —
  find and stop it with `lsof -t -i:8000 | xargs kill`, or edit the port in
  `app.py`.

## Config

Set in `.env` (see `.env.example`):

- `OLLAMA_URL` — default `http://localhost:11434`
- `OLLAMA_MODEL` — default `llama3.1`
- `DB_PATH` — default `./data/app.db`

## Notes on the search/scraping approach

Company website discovery uses a plain DuckDuckGo HTML scrape
(`duckduckgo.com/html/`) rather than a paid search API — no API key or account
needed, keeping the tool zero-cost and dependency-free beyond the two outbound
HTTP calls (search + the company's own site). It's a little more fragile to
DuckDuckGo markup changes than a JSON API, but simplest for a local personal
tool. Known directory/social sites (LinkedIn, Crunchbase, Wikipedia, etc.) are
filtered out of search results so they don't get scraped as if they were the
company's own site.
