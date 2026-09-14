const uploadForm = document.getElementById("upload-form");
const uploadResult = document.getElementById("upload-result");
const startEnrichBtn = document.getElementById("start-enrich-btn");
const enrichProgress = document.getElementById("enrich-progress");
const refreshBtn = document.getElementById("refresh-companies-btn");
const statusFilter = document.getElementById("status-filter");
const companiesTbody = document.querySelector("#companies-table tbody");
const companiesPagination = document.getElementById("companies-pagination");
const cvForm = document.getElementById("cv-form");
const cvStatus = document.getElementById("cv-status");
const cvProfileDiv = document.getElementById("cv-profile");
const cvSelect = document.getElementById("cv-select");
const startMatchBtn = document.getElementById("start-match-btn");
const matchProgress = document.getElementById("match-progress");
const rankedTbody = document.querySelector("#ranked-table tbody");
const rankedPagination = document.getElementById("ranked-pagination");
const generateDraftsBtn = document.getElementById("generate-drafts-btn");
const draftsProgress = document.getElementById("drafts-progress");
const draftsListDiv = document.getElementById("drafts-list");
const activeCvLabelEnrich = document.getElementById("active-cv-label-enrich");
const activeCvLabelMatch = document.getElementById("active-cv-label-match");

const PAGE_SIZE = 25;
let companiesPage = 1;
let rankedPage = 1;
let selectedCompanyIds = new Set();

let pollHandle = null;
let matchPollHandle = null;
let draftsPollHandle = null;

/* ---------- Tabs ---------- */

function initTabs() {
  const buttons = Array.from(document.querySelectorAll(".tab-btn"));
  const panels = Array.from(document.querySelectorAll(".tab-panel"));

  function activate(tabName) {
    for (const btn of buttons) btn.classList.toggle("active", btn.dataset.tab === tabName);
    for (const panel of panels) panel.classList.toggle("hidden", panel.id !== `tab-${tabName}`);
  }

  for (const btn of buttons) {
    btn.addEventListener("click", () => activate(btn.dataset.tab));
  }

  activate(buttons[0]?.dataset.tab || "upload");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : str;
  return div.innerHTML;
}

function statusBadge(status) {
  if (!status) return "";
  return `<span class="badge badge-${status}">${escapeHtml(status)}</span>`;
}

function fitScoreClass(score) {
  if (score >= 80) return "high";
  if (score >= 50) return "mid";
  return "low";
}

function renderPagination(container, total, page, pageSize, onChange) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  container.innerHTML = `
    <button ${page <= 1 ? "disabled" : ""} data-dir="prev">&larr; Prev</button>
    <span>Page ${page} of ${totalPages} (${total} total)</span>
    <button ${page >= totalPages ? "disabled" : ""} data-dir="next">Next &rarr;</button>
  `;
  container.querySelector('[data-dir="prev"]').addEventListener("click", () => onChange(page - 1));
  container.querySelector('[data-dir="next"]').addEventListener("click", () => onChange(page + 1));
}

/* ---------- CSV upload / enrichment ---------- */

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
      (data.rows_skipped ? `, ${data.rows_skipped} rows skipped` : "") +
      ". Already-enriched companies are left untouched — only new/failed ones need enrichment.";
    companiesPage = 1;
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

refreshBtn.addEventListener("click", () => loadCompanies());
statusFilter.addEventListener("change", () => {
  companiesPage = 1;
  loadCompanies();
});

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
    enrichProgress.textContent = "Nothing to enrich — all companies already processed.";
  }

  loadCompanies();

  if (!status.running && pollHandle) {
    clearInterval(pollHandle);
    pollHandle = null;
  }
}

async function loadCompanies() {
  const params = new URLSearchParams({ page: companiesPage, page_size: PAGE_SIZE });
  if (statusFilter.value) params.set("status", statusFilter.value);
  const resp = await fetch(`/api/companies?${params}`);
  const data = await resp.json();
  companiesPage = data.page;
  companiesTbody.innerHTML = "";
  for (const c of data.items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(c.name)}</td>
      <td>${escapeHtml(c.domain || "")}</td>
      <td>${c.contact_count}</td>
      <td>${statusBadge(c.enrichment_status)}${c.enrichment_error ? `<div class="hint">${escapeHtml(c.enrichment_error)}</div>` : ""}</td>
      <td>${escapeHtml(c.sector || "")}</td>
      <td>${escapeHtml(c.activity_summary || "")}</td>
      <td>${escapeHtml(c.size_signal || "")}</td>
      <td>${c.fit_score !== null && c.fit_score !== undefined ? `<span class="fit-score ${fitScoreClass(c.fit_score)}">${c.fit_score}</span>` : ""}</td>
      <td>${c.website_url ? `<a href="${escapeHtml(c.website_url)}" target="_blank" rel="noopener">link</a>` : ""}</td>
    `;
    companiesTbody.appendChild(tr);
  }
  renderPagination(companiesPagination, data.total, data.page, data.page_size, (p) => {
    companiesPage = p;
    loadCompanies();
  });
}

/* ---------- CV ---------- */

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
    cvStatus.textContent = data.reused
      ? `This CV was already uploaded before — reactivated existing profile for ${data.filename} (not re-analyzed).`
      : `New profile built from ${data.filename}.`;
    renderCvProfile(data);
    loadCvProfiles();
    companiesPage = 1;
    rankedPage = 1;
    loadCompanies();
    loadRankedCompanies();
  } catch (err) {
    cvStatus.textContent = `Error: ${err.message}`;
  }
});

cvSelect.addEventListener("change", async () => {
  const id = cvSelect.value;
  if (!id) return;
  await fetch(`/api/cv/profiles/${id}/activate`, { method: "POST" });
  loadCvProfile();
  companiesPage = 1;
  rankedPage = 1;
  loadCompanies();
  loadRankedCompanies();
});

function renderCvProfile(profile) {
  cvProfileDiv.innerHTML = `
    <div class="profile-field"><h4>Experience level</h4><p>${escapeHtml(profile.experience_level || "")}</p></div>
    <div class="profile-field"><h4>Skills</h4><p>${(profile.skills || []).map(escapeHtml).join(", ")}</p></div>
    <div class="profile-field"><h4>Domains worked in</h4><p>${(profile.domains_worked_in || []).map(escapeHtml).join(", ")}</p></div>
    <div class="profile-field"><h4>Target roles</h4><p>${(profile.target_roles || []).map(escapeHtml).join(", ")}</p></div>
    <div class="profile-field wide"><h4>Target sector profile</h4><p>${escapeHtml(profile.target_sector_profile || "")}</p></div>
  `;
}

async function loadCvProfile() {
  const resp = await fetch("/api/cv/profile");
  if (!resp.ok) {
    activeCvLabelEnrich.textContent = "no CV uploaded yet";
    activeCvLabelMatch.textContent = "no CV uploaded yet";
    return;
  }
  const profile = await resp.json();
  cvStatus.textContent = `Active CV: ${profile.filename}`;
  activeCvLabelEnrich.textContent = profile.filename;
  activeCvLabelMatch.textContent = profile.filename;
  renderCvProfile(profile);
}

async function loadCvProfiles() {
  const resp = await fetch("/api/cv/profiles");
  const profiles = await resp.json();
  cvSelect.innerHTML = "";
  for (const p of profiles) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.filename + (p.is_active ? " (active)" : "");
    opt.selected = !!p.is_active;
    cvSelect.appendChild(opt);
  }
}

/* ---------- Matching ---------- */

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
  const params = new URLSearchParams({ page: rankedPage, page_size: PAGE_SIZE });
  const resp = await fetch(`/api/companies/ranked?${params}`);
  const data = await resp.json();
  rankedPage = data.page;
  rankedTbody.innerHTML = "";
  for (const c of data.items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" class="ranked-select" value="${c.id}" ${selectedCompanyIds.has(c.id) ? "checked" : ""} /></td>
      <td>${escapeHtml(c.name)}</td>
      <td><span class="fit-score ${fitScoreClass(c.fit_score)}">${c.fit_score}</span></td>
      <td>${escapeHtml(c.sector || "")}</td>
      <td>${escapeHtml(c.activity_summary || "")}</td>
      <td>${escapeHtml(c.fit_rationale || "")}</td>
      <td>${c.contact_count}</td>
    `;
    const checkbox = tr.querySelector(".ranked-select");
    checkbox.addEventListener("change", () => {
      const id = parseInt(checkbox.value, 10);
      if (checkbox.checked) selectedCompanyIds.add(id);
      else selectedCompanyIds.delete(id);
    });
    rankedTbody.appendChild(tr);
  }
  renderPagination(rankedPagination, data.total, data.page, data.page_size, (p) => {
    rankedPage = p;
    loadRankedCompanies();
  });
}

generateDraftsBtn.addEventListener("click", async () => {
  const companyIds = Array.from(selectedCompanyIds);
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

/* ---------- Drafts ---------- */

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
        <p>${statusBadge("failed")} ${escapeHtml(d.error || "unknown error")}</p>
      `;
      draftsListDiv.appendChild(card);
      continue;
    }
    card.innerHTML = `
      <h3>${escapeHtml(contactName)} — ${escapeHtml(d.company_name)}</h3>
      <div class="meta">${escapeHtml(d.email || "")} · ${escapeHtml(d.title || "")}</div>
      <input type="text" class="draft-subject" value="${escapeHtml(d.subject || "")}" />
      <textarea class="draft-body">${escapeHtml(d.body || "")}</textarea>
      <button class="btn btn-primary save-draft-btn" data-id="${d.id}">Save</button>
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

initTabs();
loadCompanies();
loadCvProfile();
loadCvProfiles();
loadRankedCompanies();
loadDrafts();
