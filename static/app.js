const uploadForm = document.getElementById("upload-form");
const uploadResult = document.getElementById("upload-result");
const startEnrichBtn = document.getElementById("start-enrich-btn");
const enrichProgress = document.getElementById("enrich-progress");
const refreshBtn = document.getElementById("refresh-companies-btn");
const companiesTbody = document.querySelector("#companies-table tbody");
const cvForm = document.getElementById("cv-form");
const cvStatus = document.getElementById("cv-status");
const cvProfileDiv = document.getElementById("cv-profile");
const startMatchBtn = document.getElementById("start-match-btn");
const matchProgress = document.getElementById("match-progress");
const rankedTbody = document.querySelector("#ranked-table tbody");
const generateDraftsBtn = document.getElementById("generate-drafts-btn");
const draftsProgress = document.getElementById("drafts-progress");
const draftsListDiv = document.getElementById("drafts-list");

let pollHandle = null;
let matchPollHandle = null;
let draftsPollHandle = null;

uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById("csv-file");
  if (!fileInput.files.length) return;

  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  uploadResult.textContent = "Uploading...";
  try {
    const resp = await fetch("/api/upload-csv", { method: "POST", body: formData });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Upload failed");
    uploadResult.textContent =
      `Ingested: ${data.companies_created} new companies, ${data.contacts_created} contacts` +
      (data.rows_skipped ? `, ${data.rows_skipped} rows skipped` : "");
    loadCompanies();
  } catch (err) {
    uploadResult.textContent = `Error: ${err.message}`;
  }
});

startEnrichBtn.addEventListener("click", async () => {
  try {
    const resp = await fetch("/api/enrich/start", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Could not start enrichment");
    beginPolling();
  } catch (err) {
    enrichProgress.textContent = `Error: ${err.message}`;
  }
});

refreshBtn.addEventListener("click", loadCompanies);

function beginPolling() {
  if (pollHandle) clearInterval(pollHandle);
  pollHandle = setInterval(pollStatus, 2000);
  pollStatus();
}

async function pollStatus() {
  const resp = await fetch("/api/enrich/status");
  const status = await resp.json();
  if (status.total > 0) {
    enrichProgress.textContent =
      `${status.done}/${status.total} enriched` +
      (status.current_company ? ` — currently: ${status.current_company}` : "") +
      (status.running ? "" : " — done");
  } else if (!status.running) {
    enrichProgress.textContent = "Nothing to enrich.";
  }

  loadCompanies();

  if (!status.running && pollHandle) {
    clearInterval(pollHandle);
    pollHandle = null;
  }
}

async function loadCompanies() {
  const resp = await fetch("/api/companies");
  const companies = await resp.json();
  companiesTbody.innerHTML = "";
  for (const c of companies) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(c.name)}</td>
      <td>${escapeHtml(c.domain || "")}</td>
      <td>${c.contact_count}</td>
      <td class="status-${c.enrichment_status}">${c.enrichment_status}${c.enrichment_error ? " (" + escapeHtml(c.enrichment_error) + ")" : ""}</td>
      <td>${escapeHtml(c.sector || "")}</td>
      <td>${escapeHtml(c.activity_summary || "")}</td>
      <td>${escapeHtml(c.size_signal || "")}</td>
      <td>${c.fit_score !== null && c.fit_score !== undefined ? c.fit_score : ""}</td>
      <td>${c.website_url ? `<a href="${escapeHtml(c.website_url)}" target="_blank" rel="noopener">link</a>` : ""}</td>
    `;
    companiesTbody.appendChild(tr);
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

cvForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById("cv-file");
  if (!fileInput.files.length) return;

  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  cvStatus.textContent = "Analyzing CV with local LLM (this can take a bit)...";
  cvProfileDiv.innerHTML = "";
  try {
    const resp = await fetch("/api/cv/upload", { method: "POST", body: formData });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "CV analysis failed");
    cvStatus.textContent = `Profile built from ${data.filename}`;
    renderCvProfile(data);
  } catch (err) {
    cvStatus.textContent = `Error: ${err.message}`;
  }
});

function renderCvProfile(profile) {
  cvProfileDiv.innerHTML = `
    <p><strong>Experience level:</strong> ${escapeHtml(profile.experience_level || "")}</p>
    <p><strong>Skills:</strong> ${(profile.skills || []).map(escapeHtml).join(", ")}</p>
    <p><strong>Domains worked in:</strong> ${(profile.domains_worked_in || []).map(escapeHtml).join(", ")}</p>
    <p><strong>Target roles:</strong> ${(profile.target_roles || []).map(escapeHtml).join(", ")}</p>
    <p><strong>Target sector profile:</strong> ${escapeHtml(profile.target_sector_profile || "")}</p>
  `;
}

async function loadCvProfile() {
  const resp = await fetch("/api/cv/profile");
  if (!resp.ok) return;
  const profile = await resp.json();
  cvStatus.textContent = `Profile built from ${profile.filename}`;
  renderCvProfile(profile);
}

startMatchBtn.addEventListener("click", async () => {
  try {
    const resp = await fetch("/api/match/start", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Could not start matching");
    beginMatchPolling();
  } catch (err) {
    matchProgress.textContent = `Error: ${err.message}`;
  }
});

function beginMatchPolling() {
  if (matchPollHandle) clearInterval(matchPollHandle);
  matchPollHandle = setInterval(pollMatchStatus, 2000);
  pollMatchStatus();
}

async function pollMatchStatus() {
  const resp = await fetch("/api/match/status");
  const status = await resp.json();
  if (status.total > 0) {
    matchProgress.textContent =
      `${status.done}/${status.total} scored` + (status.running ? "" : " — done");
  } else if (!status.running) {
    matchProgress.textContent = "Nothing to score — enrich companies and upload a CV first.";
  }

  loadRankedCompanies();
  loadCompanies();

  if (!status.running && matchPollHandle) {
    clearInterval(matchPollHandle);
    matchPollHandle = null;
  }
}

async function loadRankedCompanies() {
  const resp = await fetch("/api/companies/ranked");
  const companies = await resp.json();
  rankedTbody.innerHTML = "";
  for (const c of companies) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" class="ranked-select" value="${c.id}" /></td>
      <td>${escapeHtml(c.name)}</td>
      <td>${c.fit_score}</td>
      <td>${escapeHtml(c.sector || "")}</td>
      <td>${escapeHtml(c.activity_summary || "")}</td>
      <td>${escapeHtml(c.fit_rationale || "")}</td>
      <td>${c.contact_count}</td>
    `;
    rankedTbody.appendChild(tr);
  }
}

generateDraftsBtn.addEventListener("click", async () => {
  const companyIds = Array.from(document.querySelectorAll(".ranked-select:checked")).map((el) =>
    parseInt(el.value, 10)
  );
  if (!companyIds.length) {
    draftsProgress.textContent = "Select at least one company from the ranked list first.";
    return;
  }
  try {
    const resp = await fetch("/api/drafts/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company_ids: companyIds }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Could not start draft generation");
    beginDraftsPolling();
  } catch (err) {
    draftsProgress.textContent = `Error: ${err.message}`;
  }
});

function beginDraftsPolling() {
  if (draftsPollHandle) clearInterval(draftsPollHandle);
  draftsPollHandle = setInterval(pollDraftsStatus, 2000);
  pollDraftsStatus();
}

async function pollDraftsStatus() {
  const resp = await fetch("/api/drafts/status");
  const status = await resp.json();
  if (status.total > 0) {
    draftsProgress.textContent =
      `${status.done}/${status.total} drafted` + (status.running ? "" : " — done");
  }

  loadDrafts();

  if (!status.running && draftsPollHandle) {
    clearInterval(draftsPollHandle);
    draftsPollHandle = null;
  }
}

async function loadDrafts() {
  const resp = await fetch("/api/drafts");
  const drafts = await resp.json();
  draftsListDiv.innerHTML = "";
  for (const d of drafts) {
    const card = document.createElement("div");
    card.className = "draft-card";
    const contactName = `${d.first_name || ""} ${d.last_name || ""}`.trim();
    if (d.status === "failed") {
      card.innerHTML = `
        <h3>${escapeHtml(contactName)} — ${escapeHtml(d.company_name)}</h3>
        <div class="meta">${escapeHtml(d.email || "")} · ${escapeHtml(d.title || "")}</div>
        <p class="status-failed">Draft failed: ${escapeHtml(d.error || "unknown error")}</p>
      `;
      draftsListDiv.appendChild(card);
      continue;
    }
    card.innerHTML = `
      <h3>${escapeHtml(contactName)} — ${escapeHtml(d.company_name)}</h3>
      <div class="meta">${escapeHtml(d.email || "")} · ${escapeHtml(d.title || "")}</div>
      <input type="text" class="draft-subject" value="${escapeHtml(d.subject || "")}" />
      <textarea class="draft-body">${escapeHtml(d.body || "")}</textarea>
      <button class="save-draft-btn" data-id="${d.id}">Save</button>
      <span class="save-status"></span>
    `;
    const saveBtn = card.querySelector(".save-draft-btn");
    saveBtn.addEventListener("click", async () => {
      const subject = card.querySelector(".draft-subject").value;
      const body = card.querySelector(".draft-body").value;
      const statusSpan = card.querySelector(".save-status");
      statusSpan.textContent = "Saving...";
      try {
        const resp = await fetch(`/api/drafts/${d.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ subject, body }),
        });
        if (!resp.ok) throw new Error((await resp.json()).detail || "Save failed");
        statusSpan.textContent = "Saved.";
      } catch (err) {
        statusSpan.textContent = `Error: ${err.message}`;
      }
    });
    draftsListDiv.appendChild(card);
  }
}

loadCompanies();
loadCvProfile();
loadRankedCompanies();
loadDrafts();
