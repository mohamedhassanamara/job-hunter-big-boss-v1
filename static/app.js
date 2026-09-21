const uploadForm = document.getElementById("upload-form");
const uploadResult = document.getElementById("upload-result");
const startEnrichBtn = document.getElementById("start-enrich-btn");
const enrichProgress = document.getElementById("enrich-progress");
const enrichProgressTrack = document.getElementById("enrich-progress-track");
const enrichProgressFill = document.getElementById("enrich-progress-fill");
const startDeepEnrichBtn = document.getElementById("start-deep-enrich-btn");
const deepEnrichProgress = document.getElementById("deep-enrich-progress");
const deepEnrichProgressTrack = document.getElementById("deep-enrich-progress-track");
const deepEnrichProgressFill = document.getElementById("deep-enrich-progress-fill");
const deepEnrichSummary = document.getElementById("deep-enrich-summary");
const refreshBtn = document.getElementById("refresh-companies-btn");
const statusFilter = document.getElementById("status-filter");
const companiesTbody = document.querySelector("#companies-table tbody");
const companiesPagination = document.getElementById("companies-pagination");
const cvForm = document.getElementById("cv-form");
const cvStatus = document.getElementById("cv-status");
const cvListDiv = document.getElementById("cv-list");
const cvProfileDetailDiv = document.getElementById("cv-profile-detail");
const activeCvChipName = document.getElementById("active-cv-chip-name");
const cvSwitcherBtn = document.getElementById("cv-switcher-btn");
const cvSwitcherPanel = document.getElementById("cv-switcher-panel");
const cvSwitcherList = document.getElementById("cv-switcher-list");
const cvSwitcherUploadForm = document.getElementById("cv-switcher-upload-form");
const cvSwitcherFile = document.getElementById("cv-switcher-file");
const cvSwitcherUploadStatus = document.getElementById("cv-switcher-upload-status");
const startMatchBtn = document.getElementById("start-match-btn");
const matchProgress = document.getElementById("match-progress");
const matchProgressTrack = document.getElementById("match-progress-track");
const matchProgressFill = document.getElementById("match-progress-fill");
const rankedTbody = document.querySelector("#ranked-table tbody");
const rankedPagination = document.getElementById("ranked-pagination");
const generateDraftsBtn = document.getElementById("generate-drafts-btn");
const addToQueueBtn = document.getElementById("add-to-queue-btn");
const selectionCountLabel = document.getElementById("selection-count-label");
const draftsProgress = document.getElementById("drafts-progress");
const draftsProgressTrack = document.getElementById("drafts-progress-track");
const draftsProgressFill = document.getElementById("drafts-progress-fill");
const draftsListDiv = document.getElementById("drafts-list");
const activeCvLabelEnrich = document.getElementById("active-cv-label-enrich");
const activeCvLabelMatch = document.getElementById("active-cv-label-match");
const queuesListDiv = document.getElementById("queues-list");
const queueDetailDiv = document.getElementById("queue-detail");
const queueIntervalLabel = document.getElementById("queue-interval-label");
const queueCapLabel = document.getElementById("queue-cap-label");
const queueDailyCapLabel = document.getElementById("queue-daily-cap-label");
const queueSentTodayLabel = document.getElementById("queue-sent-today-label");
const mailerWarningDiv = document.getElementById("mailer-warning");

const PAGE_SIZE = 10;
let companiesPage = 1;
let rankedPage = 1;
let selectedCompanyIds = new Set();
let queueItemCap = 20; // refreshed from /api/queues/config
let selectedQueueId = null;

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

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  } catch {
    return iso;
  }
}

function setProgressBar(track, fill, done, total, running) {
  if (total > 0 && running) {
    track.hidden = false;
    fill.style.width = `${Math.round((done / total) * 100)}%`;
  } else {
    track.hidden = true;
  }
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

/* ---------- Stats dashboard ---------- */

async function loadStats() {
  try {
    const [all, done, ranked, drafts] = await Promise.all([
      fetch("/api/companies?page=1&page_size=1").then((r) => r.json()),
      fetch("/api/companies?page=1&page_size=1&status=done").then((r) => r.json()),
      fetch("/api/companies/ranked?page=1&page_size=1").then((r) => r.json()),
      fetch("/api/drafts").then((r) => r.json()),
    ]);
    document.getElementById("stat-total").textContent = all.total;
    document.getElementById("stat-enriched").textContent = done.total;
    document.getElementById("stat-scored").textContent = ranked.total;
    document.getElementById("stat-drafts").textContent = drafts.filter((d) => d.status === "drafted").length;
  } catch {
    /* best-effort dashboard; ignore transient errors */
  }
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
    loadStats();
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
  setProgressBar(enrichProgressTrack, enrichProgressFill, status.done, status.total, status.running);

  loadCompanies();
  loadStats();

  if (!status.running && pollHandle) {
    clearInterval(pollHandle);
    pollHandle = null;
  }
}

startDeepEnrichBtn.addEventListener("click", async () => {
  try {
    const resp = await fetch("/api/enrich/deep/start", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Could not start deep enrichment");
    beginDeepEnrichPolling();
  } catch (err) {
    deepEnrichProgress.textContent = `Error: ${err.message}`;
  }
});

let deepEnrichPollHandle = null;

function beginDeepEnrichPolling() {
  if (deepEnrichPollHandle) clearInterval(deepEnrichPollHandle);
  deepEnrichPollHandle = setInterval(pollDeepEnrichStatus, 2000);
  pollDeepEnrichStatus();
}

async function loadDeepEnrichSummary() {
  const resp = await fetch("/api/enrich/deep/summary");
  const s = await resp.json();
  if (s.eligible === 0) {
    deepEnrichSummary.textContent = "No companies are enriched yet — run Start Enrichment above first.";
    return;
  }
  deepEnrichSummary.textContent =
    `${s.done}/${s.eligible} companies deep-enriched so far` +
    (s.with_signals ? ` (${s.with_signals} found a usable hook)` : "") +
    (s.running > 0 ? ` — ${s.running} in progress` : "") +
    (s.failed > 0 ? ` — ${s.failed} failed` : "");
}

async function pollDeepEnrichStatus() {
  const resp = await fetch("/api/enrich/deep/status");
  const status = await resp.json();
  if (status.total > 0) {
    deepEnrichProgress.textContent =
      `${status.done}/${status.total} deep-enriched` +
      (status.current_company ? ` — currently: ${status.current_company}` : "") +
      (status.running ? "" : " — done");
  } else if (!status.running) {
    deepEnrichProgress.textContent = "Nothing to deep-enrich yet — run Start Enrichment first.";
  }
  setProgressBar(deepEnrichProgressTrack, deepEnrichProgressFill, status.done, status.total, status.running);

  loadCompanies();
  loadDeepEnrichSummary();

  if (!status.running && deepEnrichPollHandle) {
    clearInterval(deepEnrichPollHandle);
    deepEnrichPollHandle = null;
  }
}

function renderSignalsCell(company) {
  if (company.deep_enrichment_status === "running") {
    return '<span class="text-xs text-slate-400">searching...</span>';
  }
  if (company.deep_enrichment_status === "failed") {
    return `<span class="badge badge-failed">failed</span><div class="text-xs text-slate-400 mt-1">${escapeHtml(company.deep_enrichment_error || "")}</div>`;
  }
  if (company.deep_enrichment_status !== "done") {
    return '<span class="text-xs text-slate-400">—</span>';
  }
  if (!company.signals || !company.signals.length) {
    return '<span class="badge badge-pending">no hook found</span>';
  }
  const items = company.signals
    .map(
      (s) =>
        `<div class="signal-item"><strong>${escapeHtml(s.type)}</strong>: ${escapeHtml(s.text)}${s.date_if_known ? ` <span class="text-slate-400">(${escapeHtml(s.date_if_known)})</span>` : ""}${s.source_url ? ` — <a class="text-indigo-600 dark:text-indigo-400 hover:underline" href="${escapeHtml(s.source_url)}" target="_blank" rel="noopener">source</a>` : ""}</div>`
    )
    .join("");
  return `
    <details class="signals-details">
      <summary>${company.signals.length} signal${company.signals.length > 1 ? "s" : ""}</summary>
      ${items}
    </details>
  `;
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
    tr.className = "hover:bg-slate-50 dark:hover:bg-slate-700/40";
    tr.innerHTML = `
      <td class="td-cell font-medium text-slate-900 dark:text-white">${escapeHtml(c.name)}</td>
      <td class="td-cell">${escapeHtml(c.domain || "")}</td>
      <td class="td-cell">${c.contact_count}</td>
      <td class="td-cell">${statusBadge(c.enrichment_status)}${c.enrichment_error ? `<div class="text-xs text-slate-400 mt-1">${escapeHtml(c.enrichment_error)}</div>` : ""}</td>
      <td class="td-cell">${escapeHtml(c.sector || "")}</td>
      <td class="td-cell">${escapeHtml(c.activity_summary || "")}</td>
      <td class="td-cell">${escapeHtml(c.size_signal || "")}</td>
      <td class="td-cell">${c.fit_score !== null && c.fit_score !== undefined ? `<span class="fit-score ${fitScoreClass(c.fit_score)}">${c.fit_score}</span>` : ""}</td>
      <td class="td-cell">${renderSignalsCell(c)}</td>
      <td class="td-cell">${c.website_url ? `<a class="text-indigo-600 dark:text-indigo-400 hover:underline" href="${escapeHtml(c.website_url)}" target="_blank" rel="noopener">link</a>` : ""}</td>
    `;
    companiesTbody.appendChild(tr);
  }
  renderPagination(companiesPagination, data.total, data.page, data.page_size, (p) => {
    companiesPage = p;
    loadCompanies();
  });
}

/* ---------- CV / Profile ---------- */

/** Shared upload flow used by both the Profile tab form and the header dropdown's mini-form. */
async function uploadCv(file, statusEl) {
  const formData = new FormData();
  formData.append("file", file);

  statusEl.textContent = "Analyzing CV with local LLM (this can take a bit)...";
  try {
    const resp = await fetch("/api/cv/upload", { method: "POST", body: formData });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "CV analysis failed");
    statusEl.textContent = data.reused
      ? `Already uploaded before — reactivated ${data.filename} (not re-analyzed).`
      : `New profile built from ${data.filename}.`;
    await refreshCvUI();
    companiesPage = 1;
    rankedPage = 1;
    loadCompanies();
    loadRankedCompanies();
    loadStats();
    return true;
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
    return false;
  }
}

/** Shared activation flow used by both the Profile tab card list and the header dropdown. */
async function activateCv(id) {
  await fetch(`/api/cv/profiles/${id}/activate`, { method: "POST" });
  await refreshCvUI();
  companiesPage = 1;
  rankedPage = 1;
  loadCompanies();
  loadRankedCompanies();
  loadStats();
}

cvForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById("cv-file");
  if (!fileInput.files.length) return;
  await uploadCv(fileInput.files[0], cvStatus);
});

cvSwitcherUploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!cvSwitcherFile.files.length) return;
  const ok = await uploadCv(cvSwitcherFile.files[0], cvSwitcherUploadStatus);
  if (ok) {
    cvSwitcherUploadForm.reset();
    setTimeout(closeCvSwitcher, 900);
  }
});

function openCvSwitcher() {
  cvSwitcherPanel.classList.remove("hidden");
}

function closeCvSwitcher() {
  cvSwitcherPanel.classList.add("hidden");
  cvSwitcherUploadStatus.textContent = "";
}

cvSwitcherBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  cvSwitcherPanel.classList.contains("hidden") ? openCvSwitcher() : closeCvSwitcher();
});

document.addEventListener("click", (e) => {
  if (!cvSwitcherPanel.classList.contains("hidden") && !cvSwitcherPanel.contains(e.target)) {
    closeCvSwitcher();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeCvSwitcher();
});

function renderCvProfileDetail(profile) {
  const pills = (items, cls) => (items || []).map((s) => `<span class="${cls}">${escapeHtml(s)}</span>`).join(" ");
  cvProfileDetailDiv.innerHTML = `
    <div class="card p-5">
      <div class="flex items-center justify-between flex-wrap gap-2 mb-4">
        <div>
          <h3 class="text-base font-semibold">${escapeHtml(profile.filename)}</h3>
          <p class="text-xs text-slate-400">Uploaded ${formatDate(profile.created_at)}</p>
        </div>
        <span class="pill-indigo">${escapeHtml(profile.experience_level || "unknown level")}</span>
      </div>
      <div class="profile-field mb-4">
        <h4>Skills</h4>
        <div class="flex flex-wrap gap-1.5">${pills(profile.skills, "pill-slate") || '<span class="text-sm text-slate-400">—</span>'}</div>
      </div>
      <div class="profile-field mb-4">
        <h4>Domains worked in</h4>
        <div class="flex flex-wrap gap-1.5">${pills(profile.domains_worked_in, "pill-slate") || '<span class="text-sm text-slate-400">—</span>'}</div>
      </div>
      <div class="profile-field mb-4">
        <h4>Target roles</h4>
        <div class="flex flex-wrap gap-1.5">${pills(profile.target_roles, "pill-green") || '<span class="text-sm text-slate-400">—</span>'}</div>
      </div>
      <div class="profile-field">
        <h4>Target sector profile</h4>
        <p class="text-sm leading-relaxed border-l-2 border-indigo-400 pl-3 italic text-slate-600 dark:text-slate-300">${escapeHtml(profile.target_sector_profile || "—")}</p>
      </div>
    </div>
  `;
}

async function loadCvProfile() {
  const resp = await fetch("/api/cv/profile");
  if (!resp.ok) {
    activeCvChipName.textContent = "none";
    activeCvLabelEnrich.textContent = "no CV uploaded yet";
    activeCvLabelMatch.textContent = "no CV uploaded yet";
    cvProfileDetailDiv.innerHTML = `<p class="text-sm text-slate-400">Upload a CV to build your profile.</p>`;
    return;
  }
  const profile = await resp.json();
  activeCvChipName.textContent = profile.filename;
  activeCvLabelEnrich.textContent = profile.filename;
  activeCvLabelMatch.textContent = profile.filename;
  renderCvProfileDetail(profile);
}

/** Renders the full-size Profile-tab CV cards. */
function renderCvCards(profiles) {
  cvListDiv.innerHTML = "";
  for (const p of profiles) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `cv-card ${p.is_active ? "active" : ""}`;
    btn.innerHTML = `
      <div class="flex items-center justify-between gap-2">
        <span class="text-sm font-medium truncate">${escapeHtml(p.filename)}</span>
        ${p.is_active ? '<span class="pill-indigo">Active</span>' : ""}
      </div>
      <div class="text-xs text-slate-400 mt-1">${escapeHtml(p.experience_level || "")} · ${formatDate(p.created_at)}</div>
    `;
    btn.addEventListener("click", () => {
      if (!p.is_active) activateCv(p.id);
    });
    cvListDiv.appendChild(btn);
  }
}

/** Renders the compact rows inside the header's CV switcher dropdown. */
function renderCvSwitcherRows(profiles) {
  cvSwitcherList.innerHTML = "";
  if (!profiles.length) {
    cvSwitcherList.innerHTML = `<p class="text-sm text-slate-400 px-1 py-2">No CVs uploaded yet.</p>`;
    return;
  }
  for (const p of profiles) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `cv-row ${p.is_active ? "active" : ""}`;
    row.innerHTML = `
      <span class="min-w-0">
        <span class="block text-sm font-medium truncate">${escapeHtml(p.filename)}</span>
        <span class="block text-xs text-slate-400">${escapeHtml(p.experience_level || "")}</span>
      </span>
      ${p.is_active ? '<span class="pill-indigo flex-shrink-0">Active</span>' : ""}
    `;
    row.addEventListener("click", async () => {
      if (p.is_active) {
        closeCvSwitcher();
        return;
      }
      await activateCv(p.id);
      closeCvSwitcher();
    });
    cvSwitcherList.appendChild(row);
  }
}

/** Fetches the CV list once and refreshes every view that depends on it. */
async function refreshCvUI() {
  const resp = await fetch("/api/cv/profiles");
  const profiles = await resp.json();
  renderCvCards(profiles);
  renderCvSwitcherRows(profiles);
  await loadCvProfile();
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
  setProgressBar(matchProgressTrack, matchProgressFill, status.done, status.total, status.running);

  loadRankedCompanies();
  loadCompanies();
  loadStats();

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
    tr.className = "hover:bg-slate-50 dark:hover:bg-slate-700/40";
    tr.innerHTML = `
      <td class="td-cell"><input type="checkbox" class="ranked-select" value="${c.id}" ${selectedCompanyIds.has(c.id) ? "checked" : ""} /></td>
      <td class="td-cell font-medium text-slate-900 dark:text-white">${escapeHtml(c.name)}</td>
      <td class="td-cell"><span class="fit-score ${fitScoreClass(c.fit_score)}">${c.fit_score}</span></td>
      <td class="td-cell">${escapeHtml(c.sector || "")}</td>
      <td class="td-cell">${escapeHtml(c.activity_summary || "")}</td>
      <td class="td-cell">${escapeHtml(c.fit_rationale || "")}</td>
      <td class="td-cell">${c.contact_count}</td>
    `;
    const checkbox = tr.querySelector(".ranked-select");
    checkbox.addEventListener("change", () => {
      const id = parseInt(checkbox.value, 10);
      if (checkbox.checked) selectedCompanyIds.add(id);
      else selectedCompanyIds.delete(id);
      updateSelectionUI();
    });
    rankedTbody.appendChild(tr);
  }
  renderPagination(rankedPagination, data.total, data.page, data.page_size, (p) => {
    rankedPage = p;
    loadRankedCompanies();
  });
  updateSelectionUI();
}

/** Note: the cap is enforced here by number of SELECTED COMPANIES, as a fast
 * client-side guard — the server enforces the real cap by total CONTACT count
 * (a company can have more than one contact), returning a 400 if exceeded. */
function updateSelectionUI() {
  const n = selectedCompanyIds.size;
  if (n === 0) {
    selectionCountLabel.textContent = "";
    selectionCountLabel.className = "text-sm text-slate-500 dark:text-slate-400";
    addToQueueBtn.disabled = true;
  } else if (n > queueItemCap) {
    selectionCountLabel.textContent = `${n} selected — exceeds the ${queueItemCap}-item queue cap`;
    selectionCountLabel.className = "text-sm text-rose-600 dark:text-rose-400";
    addToQueueBtn.disabled = true;
  } else {
    selectionCountLabel.textContent = `${n} selected`;
    selectionCountLabel.className = "text-sm text-slate-500 dark:text-slate-400";
    addToQueueBtn.disabled = false;
  }
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

addToQueueBtn.addEventListener("click", async () => {
  const companyIds = Array.from(selectedCompanyIds);
  if (!companyIds.length) return;
  addToQueueBtn.disabled = true;
  selectionCountLabel.textContent = "Creating queue...";
  selectionCountLabel.className = "text-sm text-slate-500 dark:text-slate-400";

  let created;
  try {
    const resp = await fetch("/api/queues", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company_ids: companyIds }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Could not create queue");
    created = data;
  } catch (err) {
    // Only a real failure to create the queue lands here.
    selectionCountLabel.textContent = `Error: ${err.message}`;
    selectionCountLabel.className = "text-sm text-rose-600 dark:text-rose-400";
    updateSelectionUI();
    return;
  }

  // Queue creation already succeeded at this point — anything that goes wrong
  // below is just a UI refresh hiccup, not a queue-creation failure, so it's
  // never allowed to overwrite the success message with a scary error.
  selectionCountLabel.textContent = `Queue "${created.name}" created with ${created.items.length} item(s).`;
  selectionCountLabel.className = "text-sm text-emerald-600 dark:text-emerald-400";
  selectedCompanyIds.clear();
  selectedQueueId = created.id;
  updateSelectionUI();
  try {
    document.querySelector('.tab-btn[data-tab="queues"]').click();
    await loadQueuesList();
    await loadQueueDetail(created.id);
  } catch (err) {
    console.error("Queue was created successfully, but refreshing the Queues tab failed:", err);
  }
});

/* ---------- Queues ---------- */

function formatCountdown(seconds) {
  if (seconds == null) return "";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

async function loadQueueConfig() {
  const resp = await fetch("/api/queues/config");
  const cfg = await resp.json();
  queueItemCap = cfg.queue_item_cap;
  queueIntervalLabel.textContent = `${Math.round(cfg.send_interval_seconds / 60)} min`;
  queueCapLabel.textContent = cfg.queue_item_cap;
  document.getElementById("queue-cap-hint").textContent = cfg.queue_item_cap;
  queueDailyCapLabel.textContent = cfg.daily_send_cap;
  queueSentTodayLabel.textContent = cfg.sent_today;
  mailerWarningDiv.classList.toggle("hidden", cfg.mailer_configured);
  updateSelectionUI();
}

async function loadQueuesList() {
  const resp = await fetch("/api/queues");
  const queues = await resp.json();
  queuesListDiv.innerHTML = "";
  if (!queues.length) {
    queuesListDiv.innerHTML = `<p class="text-sm text-slate-400">No queues yet — select companies in the Matches tab and click "Add to Queue".</p>`;
    return;
  }
  for (const q of queues) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `queue-card ${q.id === selectedQueueId ? "selected" : ""}`;
    btn.innerHTML = `
      <div class="flex items-center justify-between gap-2">
        <span class="text-sm font-medium truncate">${escapeHtml(q.name)}</span>
        ${statusBadge(q.status)}
      </div>
      <div class="text-xs text-slate-400 mt-1">
        ${q.sent}/${q.total} sent${q.failed ? ` · ${q.failed} failed` : ""} · created ${formatDate(q.created_at)}
        ${q.status === "sending" && q.seconds_until_next_send != null ? ` · next in ${formatCountdown(q.seconds_until_next_send)}` : ""}
      </div>
    `;
    btn.addEventListener("click", () => {
      selectedQueueId = q.id;
      loadQueuesList();
      loadQueueDetail(q.id);
    });
    queuesListDiv.appendChild(btn);
  }
}

async function loadQueueDetail(queueId) {
  const resp = await fetch(`/api/queues/${queueId}`);
  if (!resp.ok) {
    queueDetailDiv.innerHTML = "";
    return;
  }
  renderQueueDetail(await resp.json());
}

function renderQueueDetail(queue) {
  const controls = [];
  if (queue.status === "draft") controls.push('<button class="btn-primary btn-sm" data-action="start">Start Sending</button>');
  if (queue.status === "sending") controls.push('<button class="btn-ghost btn-sm" data-action="pause">Pause</button>');
  if (queue.status === "paused") controls.push('<button class="btn-primary btn-sm" data-action="resume">Resume</button>');
  if (queue.status !== "sending") {
    controls.push('<button class="btn-ghost btn-sm text-rose-600 dark:text-rose-400" data-action="delete">Delete</button>');
  }

  const countdown =
    queue.status === "sending" && queue.seconds_until_next_send != null
      ? `<span class="text-sm text-slate-500 dark:text-slate-400">Next email in ${formatCountdown(queue.seconds_until_next_send)}</span>`
      : "";

  const notReviewed = queue.not_reviewed_count || 0;
  const reviewNotice =
    notReviewed > 0
      ? `<div class="mt-3 text-sm bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-300 rounded-lg p-3">
           ${notReviewed} of ${queue.items.length} item${queue.items.length === 1 ? "" : "s"} not reviewed.
           Run <code class="px-1 py-0.5 rounded bg-white/60 dark:bg-black/20 text-xs">python review_queue.py ${queue.id}</code>
           and hand the output to a Claude Code session before sending, or set review status per item below.
           This is a reminder, not a hard gate — Start Sending still works with unreviewed items.
         </div>`
      : "";

  queueDetailDiv.innerHTML = `
    <div class="card p-5 mb-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h3 class="text-base font-semibold">${escapeHtml(queue.name)}</h3>
          <p class="text-xs text-slate-400">CV used: ${escapeHtml(queue.cv_filename || "unknown")} · created ${formatDate(queue.created_at)}</p>
        </div>
        <div class="flex items-center gap-3">
          ${statusBadge(queue.status)}
          ${countdown}
          <div class="flex gap-2">${controls.join("")}</div>
        </div>
      </div>
      ${reviewNotice}
    </div>
    <div id="queue-items" class="space-y-3"></div>
  `;

  for (const action of ["start", "pause", "resume"]) {
    const btn = queueDetailDiv.querySelector(`[data-action="${action}"]`);
    if (btn) btn.addEventListener("click", () => runQueueAction(queue.id, action));
  }

  const deleteBtn = queueDetailDiv.querySelector('[data-action="delete"]');
  if (deleteBtn) {
    deleteBtn.addEventListener("click", () => deleteQueue(queue.id, queue.name));
  }

  const itemsDiv = document.getElementById("queue-items");
  for (const item of queue.items) {
    itemsDiv.appendChild(renderQueueItemCard(queue.id, item));
  }
}

function renderQueueItemCard(queueId, item) {
  const card = document.createElement("div");
  card.className = "draft-card";
  const contactName = `${item.first_name || ""} ${item.last_name || ""}`.trim();
  const locked = item.send_status === "sent";
  const stillGenerating = item.generation_status === "generating" || item.generation_status === "pending";

  if (stillGenerating) {
    card.innerHTML = `
      <h3>${escapeHtml(contactName)} — ${escapeHtml(item.company_name)}</h3>
      <div class="meta">${escapeHtml(item.email || "")} · ${escapeHtml(item.title || "")}</div>
      <p class="text-sm text-slate-400">Generating draft...</p>
    `;
    return card;
  }

  const reviewOptions = ["not_reviewed", "signal_flagged", "draft_flagged", "approved"]
    .map((s) => `<option value="${s}" ${item.review_status === s ? "selected" : ""}>${s.replace("_", " ")}</option>`)
    .join("");

  card.innerHTML = `
    <div class="flex items-center justify-between gap-2 mb-1">
      <h3>${escapeHtml(contactName)} — ${escapeHtml(item.company_name)}</h3>
      <div class="flex items-center gap-2">
        ${statusBadge(item.review_status)}
        ${statusBadge(item.send_status)}
      </div>
    </div>
    <div class="meta">${escapeHtml(item.email || "")} · ${escapeHtml(item.title || "")}${item.sent_at ? ` · sent ${formatDate(item.sent_at)}` : ""}</div>
    ${item.error_message ? `<p class="text-xs text-rose-600 dark:text-rose-400 mb-2">${escapeHtml(item.error_message)}</p>` : ""}
    <input type="text" class="item-subject" value="${escapeHtml(item.subject || "")}" ${locked ? "disabled" : ""} />
    <textarea class="item-body" ${locked ? "disabled" : ""}>${escapeHtml(item.body || "")}</textarea>
    <div class="flex items-center gap-2 flex-wrap">
      ${locked ? "" : '<button class="btn-primary btn-sm save-item-btn">Save</button>'}
      ${item.send_status === "failed" ? '<button class="btn-ghost btn-sm retry-item-btn">Retry</button>' : ""}
      <label class="text-xs text-slate-500 dark:text-slate-400 flex items-center gap-1.5">
        Review:
        <select class="field text-xs py-1 review-status-select">${reviewOptions}</select>
      </label>
      <span class="save-status"></span>
    </div>
  `;

  const reviewSelect = card.querySelector(".review-status-select");
  reviewSelect.addEventListener("change", async () => {
    const statusSpan = card.querySelector(".save-status");
    try {
      const resp = await fetch(`/api/queues/${queueId}/items/${item.id}/review`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ review_status: reviewSelect.value }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || "Update failed");
      statusSpan.textContent = "Saved.";
      loadQueuesList();
    } catch (err) {
      statusSpan.textContent = `Error: ${err.message}`;
    }
  });

  const saveBtn = card.querySelector(".save-item-btn");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const subject = card.querySelector(".item-subject").value;
      const body = card.querySelector(".item-body").value;
      const statusSpan = card.querySelector(".save-status");
      statusSpan.textContent = "Saving...";
      try {
        const resp = await fetch(`/api/queues/${queueId}/items/${item.id}`, {
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
  }

  const retryBtn = card.querySelector(".retry-item-btn");
  if (retryBtn) {
    retryBtn.addEventListener("click", async () => {
      retryBtn.disabled = true;
      try {
        const resp = await fetch(`/api/queues/${queueId}/items/${item.id}/retry`, { method: "POST" });
        if (!resp.ok) throw new Error((await resp.json()).detail || "Retry failed");
        await loadQueueDetail(queueId);
        await loadQueuesList();
      } catch (err) {
        card.querySelector(".save-status").textContent = `Error: ${err.message}`;
        retryBtn.disabled = false;
      }
    });
  }

  return card;
}

async function deleteQueue(queueId, name) {
  if (!confirm(`Delete queue "${name}"? This removes it and all its items and cannot be undone.`)) return;
  try {
    const resp = await fetch(`/api/queues/${queueId}`, { method: "DELETE" });
    if (!resp.ok) throw new Error((await resp.json()).detail || "Could not delete queue");
    if (selectedQueueId === queueId) {
      selectedQueueId = null;
      queueDetailDiv.innerHTML = "";
    }
    await loadQueuesList();
  } catch (err) {
    queueDetailDiv.insertAdjacentHTML(
      "afterbegin",
      `<p class="text-sm text-rose-600 dark:text-rose-400 mb-2">Error: ${escapeHtml(err.message)}</p>`
    );
  }
}

async function runQueueAction(queueId, action) {
  try {
    const resp = await fetch(`/api/queues/${queueId}/${action}`, { method: "POST" });
    if (!resp.ok) throw new Error((await resp.json()).detail || `Could not ${action} queue`);
    await loadQueueDetail(queueId);
    await loadQueuesList();
  } catch (err) {
    queueDetailDiv.insertAdjacentHTML(
      "afterbegin",
      `<p class="text-sm text-rose-600 dark:text-rose-400 mb-2">Error: ${escapeHtml(err.message)}</p>`
    );
  }
}

// Keeps queue list/detail (countdowns, statuses, generation progress) live without manual refresh.
setInterval(() => {
  loadQueuesList();
  if (selectedQueueId) loadQueueDetail(selectedQueueId);
  loadQueueConfig();
  loadDeepEnrichSummary();
}, 5000);

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
  setProgressBar(draftsProgressTrack, draftsProgressFill, status.done, status.total, status.running);

  loadDrafts();
  loadStats();

  if (!status.running && draftsPollHandle) {
    clearInterval(draftsPollHandle);
    draftsPollHandle = null;
  }
}

async function loadDrafts() {
  const resp = await fetch("/api/drafts");
  const drafts = await resp.json();
  draftsListDiv.innerHTML = "";
  if (!drafts.length) {
    draftsListDiv.innerHTML = `<p class="text-sm text-slate-400">No drafts yet — select companies in the Matches tab and click "Generate Drafts for Selected".</p>`;
    return;
  }
  for (const d of drafts) {
    const card = document.createElement("div");
    card.className = "draft-card";
    const contactName = `${d.first_name || ""} ${d.last_name || ""}`.trim();
    if (d.status === "failed") {
      card.innerHTML = `
        <h3>${escapeHtml(contactName)} — ${escapeHtml(d.company_name)}</h3>
        <div class="meta">${escapeHtml(d.email || "")} · ${escapeHtml(d.title || "")}</div>
        <p class="text-sm">${statusBadge("failed")} <span class="text-slate-500 dark:text-slate-400">${escapeHtml(d.error || "unknown error")}</span></p>
      `;
      draftsListDiv.appendChild(card);
      continue;
    }
    card.innerHTML = `
      <h3>${escapeHtml(contactName)} — ${escapeHtml(d.company_name)}</h3>
      <div class="meta">${escapeHtml(d.email || "")} · ${escapeHtml(d.title || "")}</div>
      <input type="text" class="draft-subject" value="${escapeHtml(d.subject || "")}" />
      <textarea class="draft-body">${escapeHtml(d.body || "")}</textarea>
      <div class="flex items-center">
        <button class="btn-primary btn-sm save-draft-btn" data-id="${d.id}">Save</button>
        <span class="save-status"></span>
      </div>
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
refreshCvUI();
loadRankedCompanies();
loadDrafts();
loadStats();
loadQueueConfig();
loadQueuesList();
loadDeepEnrichSummary();
