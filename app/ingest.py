import csv
import io
import re

from app.config import FREE_EMAIL_DOMAINS
from app.db import get_conn

# Apollo exports can vary slightly in header casing/spacing; normalize and match loosely.
COLUMN_ALIASES = {
    "first_name": {"first name", "firstname", "first"},
    "last_name": {"last name", "lastname", "last"},
    "title": {"title", "job title"},
    "company": {"company", "company name", "organization"},
    "email": {"email", "email address"},
}


def _normalize_header(header: str) -> str:
    return re.sub(r"\s+", " ", header.strip().lower())


def _build_column_map(fieldnames: list[str]) -> dict[str, str]:
    normalized = {_normalize_header(f): f for f in fieldnames}
    column_map = {}
    for key, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                column_map[key] = normalized[alias]
                break
    missing = [k for k in ("company", "email") if k not in column_map]
    if missing:
        raise ValueError(
            f"CSV is missing required column(s): {', '.join(missing)}. "
            f"Found columns: {', '.join(fieldnames)}"
        )
    return column_map


def guess_domain(email: str) -> str | None:
    if not email or "@" not in email:
        return None
    domain = email.strip().lower().split("@")[-1]
    if not domain or domain in FREE_EMAIL_DOMAINS:
        return None
    return domain


def _name_key(company_name: str) -> str:
    return re.sub(r"\s+", " ", company_name.strip().lower())


def ingest_csv(file_bytes: bytes) -> dict:
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV appears to be empty or has no header row.")
    column_map = _build_column_map(reader.fieldnames)

    contacts_created = 0
    companies_created = 0
    rows_skipped = 0

    with get_conn() as conn:
        for row in reader:
            company_name = (row.get(column_map["company"]) or "").strip()
            email = (row.get(column_map["email"]) or "").strip()
            if not company_name:
                rows_skipped += 1
                continue

            key = _name_key(company_name)
            existing = conn.execute(
                "SELECT id, domain FROM companies WHERE name_key = ?", (key,)
            ).fetchone()

            if existing:
                company_id = existing["id"]
                if not existing["domain"]:
                    domain = guess_domain(email)
                    if domain:
                        conn.execute(
                            "UPDATE companies SET domain = ? WHERE id = ?",
                            (domain, company_id),
                        )
            else:
                domain = guess_domain(email)
                cur = conn.execute(
                    "INSERT INTO companies (name, name_key, domain, enrichment_status) "
                    "VALUES (?, ?, ?, 'pending')",
                    (company_name, key, domain),
                )
                company_id = cur.lastrowid
                companies_created += 1

            first_name = (row.get(column_map.get("first_name", ""), "") or "").strip()
            last_name = (row.get(column_map.get("last_name", ""), "") or "").strip()
            title = (row.get(column_map.get("title", ""), "") or "").strip()

            conn.execute(
                "INSERT INTO contacts (company_id, first_name, last_name, title, email) "
                "VALUES (?, ?, ?, ?, ?)",
                (company_id, first_name, last_name, title, email),
            )
            contacts_created += 1

    return {
        "contacts_created": contacts_created,
        "companies_created": companies_created,
        "rows_skipped": rows_skipped,
    }
