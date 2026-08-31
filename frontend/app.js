// IMPORTANT: fill this in after you deploy the backend to Render (see
// DEPLOYMENT.md). Frontend (Vercel) and backend (Render) are different
// domains now, so this can no longer be a same-origin empty string.
const API = "https://india-dpdp-act-graphrag-assistant-with.onrender.com";

// ---------------------------------------------------------------------------
// Law metadata (matches banking_config.LAWS on the backend). Kept here so
// the sidebar / welcome screen / header can render without an extra round
// trip before the first /laws response comes back.
// ---------------------------------------------------------------------------
const LAWS = {
  dpdp: {
    label: "DPDP Act, 2023",
    pillar: "Privacy",
    heading: "Ask about the DPDP Act, 2023",
    subtitle: "Grounded answers with citations, backed by a knowledge graph + vector index.",
    footer: "Source: DPDP Act, 2023 · Ministry of Electronics &amp; IT (MeitY)",
    suggestions: [
      { q: "What are the grounds for processing personal data?", label: "Grounds for processing personal data" },
      { q: "What rights does a Data Principal have?", label: "Rights of a Data Principal" },
      { q: "What penalty applies for a data breach?", label: "Penalty for a data breach" },
    ],
  },
  kyc_aml: {
    label: "KYC / AML Master Directions",
    pillar: "RBI / KYC",
    heading: "Ask about KYC / AML requirements",
    subtitle: "Grounded in RBI's KYC Master Direction, including CDD and beneficial-owner rules.",
    footer: "Source: RBI KYC Master Direction",
    suggestions: [
      { q: "What is required for Customer Due Diligence?", label: "Customer Due Diligence" },
      { q: "How is a beneficial owner identified?", label: "Identifying a beneficial owner" },
      { q: "What are the periodic KYC update requirements?", label: "Periodic KYC updates" },
    ],
  },
  pmla: {
    label: "PMLA",
    pillar: "RBI / KYC",
    heading: "Ask about the Prevention of Money Laundering Act",
    subtitle: "Grounded in curated key sections of PMLA, sourced from FIU-IND.",
    footer: "Source: FIU-IND (fiuindia.gov.in)",
    suggestions: [
      { q: "What is a reporting entity's obligation under PMLA?", label: "Reporting entity obligations" },
      { q: "What penalty applies for money laundering?", label: "Penalty for money laundering" },
      { q: "What records must be maintained under PMLA?", label: "Record-keeping requirements" },
    ],
  },
  rbi_cyber: {
    label: "RBI Cyber Security Framework",
    pillar: "Cybersecurity",
    heading: "Ask about RBI cyber security & incident reporting rules",
    subtitle: "Grounded in RBI's Cyber Security Framework and incident-reporting requirements.",
    footer: "Source: RBI Cyber Security Framework",
    suggestions: [
      { q: "What must be reported as a cyber security incident?", label: "Reportable cyber incidents" },
      { q: "What encryption standards are required?", label: "Encryption requirements" },
      { q: "What is the timeline for incident reporting?", label: "Incident reporting timeline" },
    ],
  },
  gdpr: {
    label: "GDPR (EU) 2016/679",
    pillar: "Privacy",
    heading: "Ask about the EU GDPR",
    subtitle: "Grounded in the full text of Regulation (EU) 2016/679 - all 99 Articles.",
    footer: "Source: EUR-Lex, Regulation (EU) 2016/679",
    suggestions: [
      { q: "What is required for valid consent under GDPR?", label: "Conditions for consent" },
      { q: "What is the deadline for reporting a personal data breach?", label: "Data breach notification deadline" },
      { q: "What fines can be imposed for non-compliance?", label: "Administrative fines" },
    ],
  },
  irdai: {
    label: "IRDAI Policyholders' Interests Regulations",
    pillar: "Insurance",
    heading: "Ask about IRDAI policyholder protection rules",
    subtitle: "Grounded in the IRDAI (Protection of Policyholders' Interests) Regulations, 2017.",
    footer: "Source: IRDAI (Protection of Policyholders' Interests) Regulations, 2017",
    suggestions: [
      { q: "What is the free look period for a life insurance policy?", label: "Free look period" },
      { q: "How should an insurer handle claim repudiation?", label: "Claim repudiation" },
      { q: "What is the timeline for grievance redressal?", label: "Grievance redressal timeline" },
    ],
  },
};
const LAW_CODES = Object.keys(LAWS);

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let currentLaw = localStorage.getItem("regintel_current_law") || "dpdp";
let adminKey = localStorage.getItem("regintel_admin_key") || "";
let isStreaming = false;
let currentAuditTab = "queries";
let lawStatusCache = {}; // from GET /laws

const el = {
  lawPicker: document.getElementById("lawPicker"),
  statusDot: document.getElementById("statusDot"),
  statusText: document.getElementById("statusText"),
  ingestBanner: document.getElementById("ingestBanner"),
  ingestBannerText: document.getElementById("ingestBannerText"),
  ingestBtn: document.getElementById("ingestBtn"),
  statIndexed: document.getElementById("statIndexed"),
  statPending: document.getElementById("statPending"),
  reviewBadge: document.getElementById("reviewBadge"),
  reviewList: document.getElementById("reviewList"),
  chatScroll: document.getElementById("chatScroll"),
  messages: document.getElementById("messages"),
  welcomeCard: document.getElementById("welcomeCard"),
  suggestionRow: document.getElementById("suggestionRow"),
  composerForm: document.getElementById("composerForm"),
  composerInput: document.getElementById("composerInput"),
  sendBtn: document.getElementById("sendBtn"),
  sidebar: document.getElementById("sidebar"),
  sidebarToggle: document.getElementById("sidebarToggle"),
  sidebarFooter: document.getElementById("sidebarFooter"),
  viewTitle: document.getElementById("viewTitle"),
  viewSubtitle: document.getElementById("viewSubtitle"),
  viewTabs: document.getElementById("viewTabs"),
  modalBackdrop: document.getElementById("modalBackdrop"),
  modalTitle: document.getElementById("modalTitle"),
  modalBody: document.getElementById("modalBody"),
  modalClose: document.getElementById("modalClose"),
  adminToggle: document.getElementById("adminToggle"),
  adminPanel: document.getElementById("adminPanel"),
  adminDot: document.getElementById("adminDot"),
  adminKeyInput: document.getElementById("adminKeyInput"),
  adminSaveBtn: document.getElementById("adminSaveBtn"),
  classifyForm: document.getElementById("classifyForm"),
  classifyInput: document.getElementById("classifyInput"),
  classifyBtn: document.getElementById("classifyBtn"),
  classifyResult: document.getElementById("classifyResult"),
  resultClasses: document.getElementById("resultClasses"),
  resultControls: document.getElementById("resultControls"),
  resultRationale: document.getElementById("resultRationale"),
  resultKeywordsBlock: document.getElementById("resultKeywordsBlock"),
  resultKeywords: document.getElementById("resultKeywords"),
  auditLawFilter: document.getElementById("auditLawFilter"),
  auditList: document.getElementById("auditList"),
};

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

function authHeaders() {
  return adminKey ? { "X-Admin-Key": adminKey } : {};
}

// ---------------------------------------------------------------------------
// Sidebar toggle (mobile)
// ---------------------------------------------------------------------------
el.sidebarToggle.addEventListener("click", () => el.sidebar.classList.toggle("open"));

// ---------------------------------------------------------------------------
// Admin key panel
// ---------------------------------------------------------------------------
function updateAdminDot() {
  el.adminDot.className = adminKey ? "admin-dot set" : "admin-dot";
}
el.adminKeyInput.value = adminKey;
updateAdminDot();

el.adminToggle.addEventListener("click", () => {
  el.adminPanel.hidden = !el.adminPanel.hidden;
});
el.adminSaveBtn.addEventListener("click", () => {
  adminKey = el.adminKeyInput.value.trim();
  localStorage.setItem("regintel_admin_key", adminKey);
  updateAdminDot();
  el.adminPanel.hidden = true;
});

// ---------------------------------------------------------------------------
// Law selector
// ---------------------------------------------------------------------------
function renderLawButtons() {
  el.lawPicker.querySelectorAll(".law-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.law === currentLaw);
  });
}

function applyLawChrome() {
  const meta = LAWS[currentLaw];
  el.viewTitle.textContent = meta.heading;
  el.viewSubtitle.textContent = meta.subtitle;
  el.sidebarFooter.innerHTML = `${meta.footer}<br/>Answers are AI-generated — always verify against the cited section.`;

  el.suggestionRow.innerHTML = "";
  for (const s of meta.suggestions) {
    const chip = document.createElement("button");
    chip.className = "suggestion-chip";
    chip.dataset.q = s.q;
    chip.textContent = s.label;
    chip.addEventListener("click", () => sendQuery(s.q));
    el.suggestionRow.appendChild(chip);
  }
}

function selectLaw(law) {
  if (law === currentLaw) return;
  currentLaw = law;
  localStorage.setItem("regintel_current_law", law);
  renderLawButtons();
  applyLawChrome();

  // Answers are law-specific - starting a fresh chat on switch avoids
  // showing e.g. a KYC citation next to a DPDP question.
  el.messages.innerHTML = "";
  el.welcomeCard.style.display = "";

  refreshStats();
  refreshReviewQueue();
  applyIngestBanner();
}

el.lawPicker.addEventListener("click", (e) => {
  const btn = e.target.closest(".law-btn");
  if (btn) selectLaw(btn.dataset.law);
});

// ---------------------------------------------------------------------------
// View tabs (Chat / Data Classification / Audit & Monitoring)
// ---------------------------------------------------------------------------
el.viewTabs.addEventListener("click", (e) => {
  const btn = e.target.closest(".view-tab");
  if (!btn) return;
  const view = btn.dataset.view;

  el.viewTabs.querySelectorAll(".view-tab").forEach((b) => b.classList.toggle("active", b === btn));
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));

  if (view === "chat") {
    el.viewTitle.textContent = LAWS[currentLaw].heading;
    el.viewSubtitle.textContent = LAWS[currentLaw].subtitle;
  } else if (view === "classify") {
    el.viewTitle.textContent = "Data Classification & Policy Engine";
    el.viewSubtitle.textContent = "Masking, encryption, and tokenisation controls, derived from the text you paste.";
  } else if (view === "audit") {
    el.viewTitle.textContent = "Audit & Monitoring";
    el.viewSubtitle.textContent = "Full query and review-decision history across all four laws.";
    loadAudit();
  }
});

// ---------------------------------------------------------------------------
// Ingest banner - shown when the selected law hasn't been ingested yet.
// KYC/AML and RBI Cyber use a slow LLM-based extraction pipeline
// (several minutes); the backend runs it as a background task, so this
// polls /laws until it's done instead of blocking on the POST response.
// ---------------------------------------------------------------------------
function applyIngestBanner() {
  const status = lawStatusCache[currentLaw];
  if (!status || status.ingested) {
    el.ingestBanner.hidden = true;
    return;
  }
  el.ingestBanner.hidden = false;
  if (status.in_progress) {
    el.ingestBannerText.textContent = `Indexing ${LAWS[currentLaw].label}… this can take several minutes.`;
    el.ingestBtn.disabled = true;
    el.ingestBtn.textContent = "Indexing…";
  } else if (status.ingestion_error) {
    el.ingestBannerText.textContent = `Last attempt failed: ${status.ingestion_error}`;
    el.ingestBtn.disabled = false;
    el.ingestBtn.textContent = "Retry ingest";
  } else {
    el.ingestBannerText.textContent = `${LAWS[currentLaw].label} hasn't been indexed yet.`;
    el.ingestBtn.disabled = false;
    el.ingestBtn.textContent = "Ingest now";
  }
}

let ingestPollTimer = null;
el.ingestBtn.addEventListener("click", async () => {
  if (!adminKey) {
    alert("Set an admin key first (⚙ Admin key in the sidebar) - ingestion requires it.");
    el.adminPanel.hidden = false;
    return;
  }
  el.ingestBtn.disabled = true;
  el.ingestBtn.textContent = "Starting…";
  try {
    const res = await fetch(`${API}/ingest/${currentLaw}`, {
      method: "POST",
      headers: authHeaders(),
    });
    if (res.status === 401) {
      alert("Admin key rejected. Check the key and try again.");
      return;
    }
    if (!res.ok) {
      alert(`Could not start ingestion (${res.status}).`);
      return;
    }
    if (ingestPollTimer) clearInterval(ingestPollTimer);
    ingestPollTimer = setInterval(refreshLawsMeta, 5000);
    refreshLawsMeta();
  } catch {
    alert("Connection failed while starting ingestion.");
  }
});

// ---------------------------------------------------------------------------
// /laws polling - drives law-tab dots and the ingest banner
// ---------------------------------------------------------------------------
async function refreshLawsMeta() {
  try {
    const res = await fetch(`${API}/laws`);
    const data = await res.json();
    lawStatusCache = data;
    for (const code of LAW_CODES) {
      const dot = el.lawPicker.querySelector(`[data-dot="${code}"]`);
      if (!dot) continue;
      const s = data[code];
      dot.className = "law-dot";
      if (s?.in_progress) dot.classList.add("busy");
      else if (s?.ingestion_error) dot.classList.add("offline");
      else if (s?.ingested) dot.classList.add("online");
    }
    applyIngestBanner();
    if (ingestPollTimer && !lawStatusCache[currentLaw]?.in_progress) {
      clearInterval(ingestPollTimer);
      ingestPollTimer = null;
    }
  } catch { /* backend still starting */ }
}

// ---------------------------------------------------------------------------
// Health + stats + review queue
// ---------------------------------------------------------------------------
async function refreshHealth() {
  try {
    const res = await fetch(`${API}/health`);
    const data = await res.json();
    const status = currentLaw === "dpdp"
      ? { ingested: data.ingested, error: data.ingestion_error }
      : { ingested: data.other_laws?.[currentLaw]?.done, error: data.other_laws?.[currentLaw]?.error };

    if (status.error) {
      el.statusDot.className = "status-dot offline";
      el.statusText.textContent = `Ingestion failed: ${status.error}`;
    } else if (status.ingested) {
      el.statusDot.className = "status-dot online";
      el.statusText.textContent = "Backend online";
    } else {
      el.statusDot.className = "status-dot";
      el.statusText.textContent = "Not indexed yet";
    }
  } catch {
    el.statusDot.className = "status-dot offline";
    el.statusText.textContent = "Backend unreachable";
  }
}

async function refreshStats() {
  try {
    const res = await fetch(`${API}/stats?law=${currentLaw}`);
    const data = await res.json();
    // NOTE: backend field is "indexed", not "indexed_in_qdrant".
    el.statIndexed.textContent = data.indexed ?? "–";
    el.statPending.textContent = data.pending_review ?? "–";
    el.reviewBadge.textContent = data.pending_review ?? "0";
  } catch { /* backend still starting */ }
}

async function refreshReviewQueue() {
  try {
    const res = await fetch(`${API}/pending-review?law=${currentLaw}`);
    const items = await res.json();
    renderReviewQueue(items);
  } catch { /* backend still starting */ }
}

function renderReviewQueue(items) {
  if (!items.length) {
    el.reviewList.innerHTML = `<div class="empty-state">No sections waiting for review right now.</div>`;
    return;
  }
  el.reviewList.innerHTML = "";
  for (const item of items) {
    const card = document.createElement("div");
    card.className = "review-item";
    const pct = Math.round(item.confidence * 100);
    card.innerHTML = `
      <div class="review-item-title">${item.section_id} · ${escapeHtml(item.title)}</div>
      <div class="review-item-meta">
        <span>Confidence ${pct}%</span>
        ${item.sensitive ? `<span class="flag">⚠ sensitive</span>` : ""}
      </div>
      <div class="confidence-bar"><div class="confidence-fill" style="width:${pct}%"></div></div>
      <div class="review-actions">
        <button class="btn-mini approve">Approve</button>
        <button class="btn-mini reject">Reject</button>
      </div>
    `;
    const [approveBtn, rejectBtn] = card.querySelectorAll("button");
    approveBtn.addEventListener("click", () => decide(item.section_id, "approve", card));
    rejectBtn.addEventListener("click", () => decide(item.section_id, "reject", card));
    el.reviewList.appendChild(card);
  }
}

async function decide(sectionId, decision, cardEl) {
  if (!adminKey) {
    alert("Set an admin key first (⚙ Admin key in the sidebar) - review decisions require it.");
    el.adminPanel.hidden = false;
    return;
  }
  cardEl.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    const res = await fetch(`${API}/approve-review-item?law=${currentLaw}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ section_id: sectionId, decision, reviewer: "demo_reviewer" }),
    });
    if (res.status === 401) {
      alert("Admin key rejected. Check the key and try again.");
      cardEl.querySelectorAll("button").forEach((b) => (b.disabled = false));
      return;
    }
    await Promise.all([refreshReviewQueue(), refreshStats()]);
  } catch {
    cardEl.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------
function scrollToBottom() {
  el.chatScroll.scrollTop = el.chatScroll.scrollHeight;
}

function addMessage(role) {
  el.welcomeCard.style.display = "none";
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  wrap.innerHTML = `
    <div class="msg-avatar">${role === "user" ? "🧑" : "⚖️"}</div>
    <div class="msg-body">
      <div class="msg-role">${role === "user" ? "You" : LAWS[currentLaw].label}</div>
      <div class="msg-content"></div>
    </div>
  `;
  el.messages.appendChild(wrap);
  scrollToBottom();
  return wrap.querySelector(".msg-content");
}

function showTyping(contentEl) {
  contentEl.innerHTML = `<span class="typing-dots"><span></span><span></span><span></span></span>`;
}

async function sendQuery(query) {
  if (isStreaming) return;
  isStreaming = true;
  el.sendBtn.disabled = true;

  addMessage("user").textContent = query;
  const assistantContent = addMessage("assistant");
  showTyping(assistantContent);

  let firstToken = true;
  let citationMeta = [];

  try {
    const res = await fetch(`${API}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, law: currentLaw }),
    });

    if (res.status === 429) {
      assistantContent.className = "msg-content status-error";
      assistantContent.textContent = "You're sending questions a bit fast — please wait a moment and try again.";
      return;
    }
    if (!res.ok || !res.body) {
      assistantContent.className = "msg-content status-error";
      assistantContent.textContent = `Request failed (${res.status}).`;
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep the last (possibly incomplete) line

      for (const line of lines) {
        if (!line.trim()) continue;
        const evt = JSON.parse(line);

        if (evt.type === "token") {
          if (firstToken) { assistantContent.textContent = ""; firstToken = false; }
          assistantContent.textContent += evt.text;
          scrollToBottom();
        } else if (evt.type === "error") {
          assistantContent.className = "msg-content status-error";
          assistantContent.textContent = `The model hit an error: ${evt.detail}`;
        } else if (evt.type === "done") {
          citationMeta = evt.citation_meta || [];
          if (evt.status === "no_answer") {
            assistantContent.className = "msg-content status-empty";
            assistantContent.textContent = evt.note;
          } else if (evt.status === "pending_review") {
            assistantContent.className = "msg-content status-pending";
            assistantContent.textContent = evt.note;
          } else if (firstToken) {
            assistantContent.textContent = "(no content generated)";
          }
          renderCitations(assistantContent, citationMeta, evt.cached);
        }
      }
    }
  } catch {
    assistantContent.className = "msg-content status-error";
    assistantContent.textContent = "Connection lost while generating the answer. Please try again.";
  } finally {
    isStreaming = false;
    el.sendBtn.disabled = false;
    scrollToBottom();
  }
}

function renderCitations(afterEl, citationMeta, cached) {
  if (!citationMeta.length) return;
  const row = document.createElement("div");
  row.className = "citations";
  for (const c of citationMeta) {
    const chip = document.createElement("button");
    chip.className = "citation-chip";
    chip.type = "button";
    chip.textContent = `${c.id} · ${c.title}`;
    chip.addEventListener("click", () => openCitation(c.id));
    row.appendChild(chip);
  }
  afterEl.insertAdjacentElement("afterend", row);
  if (cached) {
    const tag = document.createElement("div");
    tag.className = "cache-tag";
    tag.textContent = "served from cache";
    row.insertAdjacentElement("afterend", tag);
  }
}

async function openCitation(sectionId) {
  el.modalTitle.textContent = "Loading…";
  el.modalBody.textContent = "";
  el.modalBackdrop.classList.add("open");
  try {
    const res = await fetch(`${API}/section/${sectionId}?law=${currentLaw}`);
    const data = await res.json();
    el.modalTitle.textContent = `${data.id} · ${data.title}`;
    el.modalBody.textContent = data.text;
  } catch {
    el.modalTitle.textContent = "Error";
    el.modalBody.textContent = "Could not load this section.";
  }
}

el.modalClose.addEventListener("click", () => el.modalBackdrop.classList.remove("open"));
el.modalBackdrop.addEventListener("click", (e) => {
  if (e.target === el.modalBackdrop) el.modalBackdrop.classList.remove("open");
});

el.composerForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = el.composerInput.value.trim();
  if (!q) return;
  el.composerInput.value = "";
  sendQuery(q);
});

// ---------------------------------------------------------------------------
// Data Classification & Policy Engine
// ---------------------------------------------------------------------------
el.classifyForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = el.classifyInput.value.trim();
  if (!text) return;

  el.classifyBtn.disabled = true;
  el.classifyBtn.textContent = "Evaluating…";
  try {
    const res = await fetch(`${API}/evaluate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) {
      alert(`Evaluation failed (${res.status}).`);
      return;
    }
    const data = await res.json();
    renderClassifyResult(data);
  } catch {
    alert("Connection failed while evaluating.");
  } finally {
    el.classifyBtn.disabled = false;
    el.classifyBtn.textContent = "Evaluate";
  }
});

function renderClassifyResult(data) {
  el.classifyResult.hidden = false;

  el.resultClasses.innerHTML = "";
  const classes = data.data_classes || [];
  if (!classes.length) {
    el.resultClasses.innerHTML = `<span class="empty-inline">No sensitive data classes matched.</span>`;
  }
  for (const c of classes) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = c;
    el.resultClasses.appendChild(chip);
  }

  el.resultControls.innerHTML = "";
  const controls = data.required_controls || [];
  if (!controls.length) {
    el.resultControls.innerHTML = `<span class="empty-inline">No controls required.</span>`;
  }
  for (const c of controls) {
    const chip = document.createElement("span");
    chip.className = "chip chip-control";
    chip.textContent = c;
    el.resultControls.appendChild(chip);
  }

  el.resultRationale.textContent = data.rationale || "—";

  const keywords = data.matched_keywords || [];
  el.resultKeywordsBlock.hidden = keywords.length === 0;
  el.resultKeywords.innerHTML = "";
  for (const k of keywords) {
    const chip = document.createElement("span");
    chip.className = "chip chip-muted";
    chip.textContent = k;
    el.resultKeywords.appendChild(chip);
  }
}

// ---------------------------------------------------------------------------
// Audit & Monitoring
// ---------------------------------------------------------------------------
document.querySelectorAll(".audit-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".audit-tab").forEach((t) => t.classList.toggle("active", t === tab));
    currentAuditTab = tab.dataset.audit;
    loadAudit();
  });
});
el.auditLawFilter.addEventListener("change", loadAudit);

async function loadAudit() {
  el.auditList.innerHTML = `<div class="empty-state">Loading…</div>`;
  const lawParam = el.auditLawFilter.value ? `&law=${el.auditLawFilter.value}` : "";
  const path = currentAuditTab === "queries" ? "/audit-log" : "/audit-log/reviews";
  const url = currentAuditTab === "queries"
    ? `${API}${path}?limit=100${lawParam}`
    : `${API}${path}?limit=100`; // review log endpoint has no law filter on the backend

  try {
    const res = await fetch(url);
    const data = await res.json();
    renderAudit(data);
  } catch {
    el.auditList.innerHTML = `<div class="empty-state">Could not load the audit log.</div>`;
  }
}

// Renders each entry's fields generically (key: value), since the exact
// shape returned by audit_log.export_query_log()/export_review_log() may
// evolve independently of this file - this avoids the table breaking if a
// field is renamed or added on the backend.
function renderAudit(entries) {
  if (!entries || !entries.length) {
    el.auditList.innerHTML = `<div class="empty-state">No entries yet.</div>`;
    return;
  }
  const priorityKeys = ["timestamp", "created_at", "law_code", "query_text", "decision", "item_reference"];
  el.auditList.innerHTML = "";
  for (const entry of entries) {
    const card = document.createElement("div");
    card.className = "audit-card";
    const keys = Object.keys(entry).sort((a, b) => {
      const ai = priorityKeys.indexOf(a), bi = priorityKeys.indexOf(b);
      if (ai === -1 && bi === -1) return a.localeCompare(b);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    });
    for (const key of keys) {
      const value = entry[key];
      if (value === null || value === undefined || value === "") continue;
      const row = document.createElement("div");
      row.className = "audit-row";
      const display = Array.isArray(value) ? value.join(", ") : String(value);
      row.innerHTML = `<span class="audit-key">${escapeHtml(key)}</span><span class="audit-val">${escapeHtml(display)}</span>`;
      card.appendChild(row);
    }
    el.auditList.appendChild(card);
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
renderLawButtons();
applyLawChrome();
refreshHealth();
refreshStats();
refreshReviewQueue();
refreshLawsMeta();

setInterval(refreshHealth, 8000);
setInterval(() => { refreshStats(); refreshReviewQueue(); }, 6000);
setInterval(refreshLawsMeta, 15000);
