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
  this background. **Multiple CVs are supported side by side** (`cv_profiles`
  table) — each CV is identified by a hash of its extracted text, so
  re-uploading the exact same file just reactivates its existing profile
  instantly, with no LLM call. Different CVs get their own profile row and
  their own independent set of fit scores; switch which one is "active" with
  the CV selector in the top bar.
- **Phase 4 — Matching & Ranking**: done. Fit is a property of a
  **(company, CV) pair** (`fit_scores` table), not of the company alone, so
  scores never overwrite each other across CVs — matching against a second CV
  doesn't touch the first CV's scores. Scores every enriched company against
  the *active* CV profile using the local LLM, batching several companies per
  call (`BATCH_SIZE = 8` in `app/matching.py`) instead of one call per
  company — the enrichment summaries are short, so a batch fits comfortably
  in context while cutting the number of LLM round-trips roughly 8x. Each
  company gets a 0-100 fit score plus a one-sentence rationale. If a batch
  comes back incomplete or malformed (more likely with very small/weak local
  models), only the companies missing a score are marked `failed` — the rest
  of the batch's scores are kept, and `failed`/`unscored` (for the active CV)
  companies are picked up again on the next "Start Matching" run.
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

Then open http://127.0.0.1:8000. Each numbered step below is its own tab in
the UI — click a tab to work through that step.

1. Upload your Apollo CSV export (needs at least `Company` and `Email`
   columns; `First Name` / `Last Name` / `Title` are used if present).
2. Click "Start Enrichment" — this can take a while (one LLM call + a couple
   of HTTP fetches per company). Progress is polled in the UI.
3. Review the companies table: sector, activity summary, size signal, and
   enrichment status (`pending` / `enriching` / `done` / `failed`, with an
   error message on failures — usually "couldn't find/fetch a website").
4. On the Profile tab, upload your CV (PDF or .txt) to get a fit profile:
   skills, experience level, domains worked in, target roles, and a
   target-sector-profile paragraph. Uploading a CV you've already uploaded
   before just reactivates it (no re-analysis); uploading a genuinely
   different CV creates a new, independent profile, shown alongside the
   others in the CV card list on the left — click any card to make it
   active. Each CV keeps its own fit scores and drafts.
5. Click "Start Matching" to score every enriched company against the
   *active* CV profile (0-100 fit score + one-line rationale), processed in
   batches of 8. The ranked table sorts by fit score descending. Re-running
   only rescopes companies that are still `unscored`/`failed` for that CV.
6. Check the box next to the companies you want to reach out to in the ranked
   table, then click "Generate Drafts for Selected". One draft per contact at
   each selected company appears in the Outreach Drafts section — edit the
   subject/body inline and hit Save, then use "Export all as CSV" or
   "Export all as .txt (zip)" to get files you can send manually.

Re-uploading a CSV or re-running enrichment is safe: companies are matched by
name, so re-uploading a CSV that includes already-enriched companies leaves
them untouched — only genuinely new companies (or ones that previously
`failed`) are (re-)enriched.

## Data safety

Everything lives in a single SQLite file at `DB_PATH` (`./data/app.db` by
default) — it's written to disk on every change, so quitting the app or your
terminal never loses data; it's simply there again next time you run
`python app.py`. It's excluded from git (see `.gitignore`) so it's never
committed, but that doesn't delete it from disk. Before any schema-changing
update to this app, back up the file yourself if you want extra safety:

```bash
cp data/app.db data/backups/app.db.backup-$(date +%Y%m%d-%H%M%S)
```

## UI overview

- Built with [Tailwind](https://tailwindcss.com) (the browser "Play CDN"
  build, self-hosted at `static/vendor/tailwind.js` so no internet access is
  needed at runtime after the initial one-time download). All component
  styling (cards, pills, badges, buttons, progress bars) is defined once via
  `@apply` in a `<style type="text/tailwindcss">` block at the top of
  `static/index.html` — there's no separate CSS file.
- **Tabbed layout**: each of the 5 steps (Companies, Enrich, Profile, Matches,
  Drafts) is its own tab — only one is visible at a time, switched via the
  pill-style nav bar under the header. Tab state lives in `static/app.js`'s
  `initTabs()`.
- **Stats dashboard**: four live counters above the tabs (Total Companies,
  Enriched, Scored for the active CV, Drafts Ready) so you always have an
  at-a-glance read on where a batch stands.
- **Profile tab** (step 3): a real profile view instead of a plain form — a
  card list of every CV you've uploaded on the left (click one to make it
  active), and the active CV's full profile on the right as a proper resume
  summary: experience level as a badge, skills/domains/target roles as pill
  tags, and the target sector profile as a highlighted quote block. The top
  bar shows the active CV's name at a glance from any tab.
- **Which CV a score belongs to**: fit scores are only ever shown for the
  *currently active* CV — both the Enrich tab's companies table and the
  Matches tab's ranked table display a line reading "Fit scores shown are for
  CV: `<filename>`" right above the table, so it's always explicit. Switch
  the active CV from the Profile tab's card list and both tables' fit scores
  update to that CV's own independent scores.
- **Companies table** (step 2) and **ranked matches table** (step 4) are
  paginated (25 per page) since a real lead list can run into the hundreds —
  use the status filter on the companies table to jump straight to `failed`
  ones worth retrying.
- Status/fit values are shown as colored pill badges (green = done/scored,
  amber = in progress, red = failed, grey = pending/unscored) and fit scores
  are color-coded (green ≥ 80, amber ≥ 50, grey below). Long-running steps
  (enrich/match/draft) show a slim animated progress bar in addition to the
  X/Y text.

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
- `OLLAMA_MODEL` — default `llama3.1`. Use a model you've pulled locally
  (`ollama pull llama3.1`, then `ollama list` to confirm). **Don't use a
  `-cloud`-suffixed model name** (e.g. `gemma4:31b-cloud`) unless you've run
  `ollama signin` — those route through Ollama's cloud service and return
  `401 Unauthorized` otherwise, breaking the "runs entirely locally" goal of
  this app anyway.
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
