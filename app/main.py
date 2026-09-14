import csv
import io
import re
import zipfile

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR
from app.cv import build_profile, extract_text, get_profile, save_profile
from app.db import get_conn, init_db
from app.drafts import get_status as get_draft_status
from app.drafts import list_drafts, start_drafting, update_draft
from app.enrichment import get_status as get_enrich_status
from app.enrichment import start_enrichment
from app.ingest import ingest_csv
from app.llm import OllamaError
from app.matching import get_status as get_match_status
from app.matching import reset_scores, start_matching

app = FastAPI(title="Local Lead-Matching & Outreach Tool")


@app.on_event("startup")
def on_startup():
    init_db()


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


@app.get("/api/companies")
def list_companies():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.id, c.name, c.domain, c.website_url, c.sector, "
            "c.activity_summary, c.size_signal, c.enrichment_status, c.enrichment_error, "
            "c.fit_status, c.fit_score, c.fit_rationale, "
            "(SELECT COUNT(*) FROM contacts WHERE contacts.company_id = c.id) AS contact_count "
            "FROM companies c ORDER BY c.name COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/companies/ranked")
def list_ranked_companies():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.id, c.name, c.domain, c.website_url, c.sector, "
            "c.activity_summary, c.size_signal, c.fit_score, c.fit_rationale, "
            "(SELECT COUNT(*) FROM contacts WHERE contacts.company_id = c.id) AS contact_count "
            "FROM companies c WHERE c.fit_status = 'scored' "
            "ORDER BY c.fit_score DESC"
        ).fetchall()
    return [dict(r) for r in rows]


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


@app.post("/api/cv/upload")
async def upload_cv(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".pdf", ".txt")):
        raise HTTPException(status_code=400, detail="Please upload a .pdf or .txt CV file.")
    contents = await file.read()
    try:
        text = extract_text(contents, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        parsed = build_profile(text)
    except OllamaError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except (ValueError, KeyError) as e:
        raise HTTPException(
            status_code=502, detail=f"Model returned an unparseable profile: {e}"
        ) from e

    save_profile(file.filename, text, parsed)
    reset_scores()  # a new CV invalidates any previous fit scoring
    return get_profile()


@app.get("/api/cv/profile")
def cv_profile():
    profile = get_profile()
    if not profile:
        raise HTTPException(status_code=404, detail="No CV uploaded yet.")
    return profile


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


app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
