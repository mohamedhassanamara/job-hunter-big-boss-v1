import csv
import io
import json
import re
import zipfile

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR
from app.cv import (
    activate_profile,
    build_profile,
    extract_text,
    find_by_hash,
    get_active_profile,
    hash_text,
    list_profiles,
    save_profile,
    save_resume_pdf,
)
from app.db import get_conn, init_db
from app.drafts import get_status as get_draft_status
from app.drafts import list_drafts, start_drafting, update_draft
from app.enrichment import get_status as get_enrich_status
from app.enrichment import start_enrichment
from app.ingest import ingest_csv
from app.llm import OllamaError
from app.matching import get_status as get_match_status
from app.matching import start_matching
from app.queues import (
    bulk_set_review_status,
    create_queue,
    delete_queue,
    ensure_sender_loop_started,
    get_generation_status,
    get_queue,
    get_send_config,
    list_queues,
    pause_queue,
    rename_queue,
    resume_queue,
    retry_item,
    set_review_status,
    start_queue,
)
from app.queues import update_item as update_queue_item
from app.signals import get_status as get_deep_enrich_status
from app.signals import start_deep_enrichment

app = FastAPI(title="Local Lead-Matching & Outreach Tool")


@app.middleware("http")
async def no_store_static(request, call_next):
    """This app is actively edited and reloaded locally — a browser silently
    serving a stale cached static/app.js or index.html (with none of the
    latest fixes) is a much worse failure mode than the tiny perf cost of
    always revalidating a handful of small local files."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.on_event("startup")
def on_startup():
    init_db()
    ensure_sender_loop_started()


@app.get("/")
def index():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.post("/api/upload-csv")
async def upload_csv(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")
    contents = await file.read()
    try:
        result = ingest_csv(contents)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return result


def _active_cv_id() -> int | None:
    profile = get_active_profile()
    return profile["id"] if profile else None


@app.get("/api/companies")
def list_companies(page: int = 1, page_size: int = 50, status: str | None = None):
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    offset = (page - 1) * page_size
    cv_id = _active_cv_id()

    where = ""
    params: list = [cv_id]
    if status:
        where = "WHERE c.enrichment_status = ?"
        params.append(status)

    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM companies c {where}", params[1:] if where else []
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT c.id, c.name, c.domain, c.website_url, c.sector, "
            f"c.activity_summary, c.size_signal, c.enrichment_status, c.enrichment_error, "
            f"c.signals, c.deep_enrichment_status, c.deep_enrichment_error, "
            f"fs.fit_status, fs.fit_score, fs.fit_rationale, "
            f"(SELECT COUNT(*) FROM contacts WHERE contacts.company_id = c.id) AS contact_count "
            f"FROM companies c "
            f"LEFT JOIN fit_scores fs ON fs.company_id = c.id AND fs.cv_profile_id = ? "
            f"{where} "
            f"ORDER BY c.name COLLATE NOCASE LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()

    items = []
    for r in rows:
        item = dict(r)
        item["signals"] = json.loads(item["signals"]) if item["signals"] else []
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.get("/api/companies/ranked")
def list_ranked_companies(page: int = 1, page_size: int = 50):
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    offset = (page - 1) * page_size
    cv_id = _active_cv_id()
    if cv_id is None:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM fit_scores WHERE cv_profile_id = ? AND fit_status = 'scored'",
            (cv_id,),
        ).fetchone()["n"]
        rows = conn.execute(
            "SELECT c.id, c.name, c.domain, c.website_url, c.sector, "
            "c.activity_summary, c.size_signal, fs.fit_score, fs.fit_rationale, "
            "(SELECT COUNT(*) FROM contacts WHERE contacts.company_id = c.id) AS contact_count "
            "FROM fit_scores fs JOIN companies c ON c.id = fs.company_id "
            "WHERE fs.cv_profile_id = ? AND fs.fit_status = 'scored' "
            "ORDER BY fs.fit_score DESC LIMIT ? OFFSET ?",
            (cv_id, page_size, offset),
        ).fetchall()

    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.get("/api/companies/{company_id}/contacts")
def list_company_contacts(company_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, first_name, last_name, title, email FROM contacts WHERE company_id = ?",
            (company_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/enrich/start")
def enrich_start():
    started = start_enrichment()
    if not started:
        raise HTTPException(status_code=409, detail="Enrichment is already running.")
    return {"started": True}


@app.get("/api/enrich/status")
def enrich_status():
    return get_enrich_status()


@app.post("/api/enrich/deep/start")
def deep_enrich_start():
    started = start_deep_enrichment()
    if not started:
        raise HTTPException(status_code=409, detail="Deep enrichment is already running.")
    return {"started": True}


@app.get("/api/enrich/deep/status")
def deep_enrich_status():
    return get_deep_enrich_status()


@app.get("/api/enrich/deep/summary")
def deep_enrich_summary():
    """Aggregate counts across companies eligible for deep enrichment
    (enrichment_status='done'), independent of any run in progress — this is
    what makes deep-enrichment progress visible even after a page refresh or
    between runs, not just while it's actively running."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT deep_enrichment_status, COUNT(*) AS n FROM companies "
            "WHERE enrichment_status = 'done' GROUP BY deep_enrichment_status"
        ).fetchall()
        eligible = conn.execute(
            "SELECT COUNT(*) AS n FROM companies WHERE enrichment_status = 'done'"
        ).fetchone()["n"]
        with_signals = conn.execute(
            "SELECT COUNT(*) AS n FROM companies WHERE enrichment_status = 'done' "
            "AND signals IS NOT NULL AND signals != '[]'"
        ).fetchone()["n"]
    counts = {r["deep_enrichment_status"]: r["n"] for r in rows}
    return {
        "eligible": eligible,  # companies with enrichment_status='done', i.e. deep-enrichable
        "not_started": counts.get("not_started", 0),
        "running": counts.get("running", 0),
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
        "with_signals": with_signals,  # subset of 'done' that actually found a hook
    }


@app.post("/api/cv/upload")
async def upload_cv(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".pdf", ".txt")):
        raise HTTPException(status_code=400, detail="Please upload a .pdf or .txt CV file.")
    contents = await file.read()
    try:
        text = extract_text(contents, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    content_hash = hash_text(text)
    existing = find_by_hash(content_hash)
    if existing:
        activate_profile(existing["id"])
        return {**existing, "reused": True}

    try:
        parsed = build_profile(text)
    except OllamaError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except (ValueError, KeyError) as e:
        raise HTTPException(
            status_code=502, detail=f"Model returned an unparseable profile: {e}"
        ) from e

    resume_pdf_path = save_resume_pdf(contents, content_hash) if file.filename.lower().endswith(".pdf") else None
    profile = save_profile(file.filename, text, content_hash, parsed, resume_pdf_path)
    return {**profile, "reused": False}


@app.get("/api/cv/profile")
def cv_profile():
    profile = get_active_profile()
    if not profile:
        raise HTTPException(status_code=404, detail="No CV uploaded yet.")
    return profile


@app.get("/api/cv/profiles")
def cv_profiles():
    return list_profiles()


@app.post("/api/cv/profiles/{profile_id}/activate")
def cv_activate(profile_id: int):
    ok = activate_profile(profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail="CV profile not found.")
    return get_active_profile()


@app.post("/api/match/start")
def match_start():
    started, error = start_matching()
    if not started:
        raise HTTPException(status_code=409, detail=error)
    return {"started": True}


@app.get("/api/match/status")
def match_status():
    return get_match_status()


@app.post("/api/drafts/generate")
def drafts_generate(payload: dict = Body(...)):
    company_ids = payload.get("company_ids", [])
    started, error = start_drafting(company_ids)
    if not started:
        raise HTTPException(status_code=409, detail=error)
    return {"started": True}


@app.get("/api/drafts/status")
def drafts_status():
    return get_draft_status()


@app.get("/api/drafts")
def drafts_list():
    return list_drafts()


@app.put("/api/drafts/{draft_id}")
def drafts_update(draft_id: int, payload: dict = Body(...)):
    ok = update_draft(draft_id, payload.get("subject", ""), payload.get("body", ""))
    if not ok:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return {"updated": True}


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "contact"


def _parse_ids(ids: str | None) -> list[int] | None:
    if not ids:
        return None
    return [int(x) for x in ids.split(",") if x.strip().isdigit()]


@app.get("/api/drafts/export.csv")
def drafts_export_csv(ids: str | None = None):
    drafts = list_drafts(_parse_ids(ids))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["first_name", "last_name", "email", "company", "subject", "body", "status"])
    for d in drafts:
        writer.writerow(
            [
                d["first_name"],
                d["last_name"],
                d["email"],
                d["company_name"],
                d["subject"] or "",
                d["body"] or "",
                d["status"],
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=outreach_drafts.csv"},
    )


@app.get("/api/drafts/export.zip")
def drafts_export_zip(ids: str | None = None):
    drafts = [d for d in list_drafts(_parse_ids(ids)) if d["status"] == "drafted"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in drafts:
            name = _safe_filename(f"{d['first_name']}_{d['last_name']}_{d['company_name']}") + ".txt"
            content = (
                f"To: {d['email']}\n"
                f"Subject: {d['subject']}\n\n"
                f"{d['body']}\n"
            )
            zf.writestr(name, content)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=outreach_drafts.zip"},
    )


@app.get("/api/queues/config")
def queues_config():
    return get_send_config()


@app.post("/api/queues")
def queues_create(payload: dict = Body(...)):
    company_ids = payload.get("company_ids", [])
    try:
        queue = create_queue(company_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return queue


@app.get("/api/queues")
def queues_list():
    return list_queues()


@app.get("/api/queues/{queue_id}")
def queues_get(queue_id: int):
    queue = get_queue(queue_id)
    if not queue:
        raise HTTPException(status_code=404, detail="Queue not found.")
    return queue


@app.put("/api/queues/{queue_id}")
def queues_rename(queue_id: int, payload: dict = Body(...)):
    ok = rename_queue(queue_id, payload.get("name", ""))
    if not ok:
        raise HTTPException(status_code=404, detail="Queue not found.")
    return get_queue(queue_id)


@app.delete("/api/queues/{queue_id}")
def queues_delete(queue_id: int):
    ok, error = delete_queue(queue_id)
    if not ok:
        status_code = 404 if error == "Queue not found." else 409
        raise HTTPException(status_code=status_code, detail=error)
    return {"deleted": True}


@app.get("/api/queues/{queue_id}/generation-status")
def queues_generation_status(queue_id: int):
    return get_generation_status(queue_id)


@app.post("/api/queues/{queue_id}/start")
def queues_start(queue_id: int):
    ok, error = start_queue(queue_id)
    if not ok:
        raise HTTPException(status_code=409, detail=error)
    return get_queue(queue_id)


@app.post("/api/queues/{queue_id}/pause")
def queues_pause(queue_id: int):
    ok, error = pause_queue(queue_id)
    if not ok:
        raise HTTPException(status_code=409, detail=error)
    return get_queue(queue_id)


@app.post("/api/queues/{queue_id}/resume")
def queues_resume(queue_id: int):
    ok, error = resume_queue(queue_id)
    if not ok:
        raise HTTPException(status_code=409, detail=error)
    return get_queue(queue_id)


@app.put("/api/queues/{queue_id}/items/{item_id}")
def queues_update_item(queue_id: int, item_id: int, payload: dict = Body(...)):
    ok, error = update_queue_item(queue_id, item_id, payload.get("subject", ""), payload.get("body", ""))
    if not ok:
        raise HTTPException(status_code=409, detail=error)
    return get_queue(queue_id)


@app.post("/api/queues/{queue_id}/items/{item_id}/retry")
def queues_retry_item(queue_id: int, item_id: int):
    ok, error = retry_item(queue_id, item_id)
    if not ok:
        raise HTTPException(status_code=409, detail=error)
    return get_queue(queue_id)


@app.put("/api/queues/{queue_id}/items/{item_id}/review")
def queues_set_review_status(queue_id: int, item_id: int, payload: dict = Body(...)):
    ok, error = set_review_status(queue_id, item_id, payload.get("review_status", ""))
    if not ok:
        raise HTTPException(status_code=400, detail=error)
    return get_queue(queue_id)


@app.post("/api/queues/{queue_id}/review/bulk")
def queues_bulk_review(queue_id: int, payload: dict = Body(...)):
    updates_raw = payload.get("updates", {})
    updates = {int(k): v for k, v in updates_raw.items()}
    updated, warnings = bulk_set_review_status(queue_id, updates)
    return {"updated": updated, "warnings": warnings, "queue": get_queue(queue_id)}


app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
