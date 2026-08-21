// IMPORTANT: fill this in after you deploy the backend to Render (see
// DEPLOYMENT.md). Frontend (Vercel) and backend (Render) are different
// domains now, so this can no longer be a same-origin empty string.
// Example: "https://dpdp-act-backend.onrender.com"
const API = "   const API_BASE = "https://india-dpdp-act-graphrag-assistant-with.onrender.com";";

const el = {
  statusDot: document.getElementById("statusDot"),
  statusText: document.getElementById("statusText"),
  statIndexed: document.getElementById("statIndexed"),
  statPending: document.getElementById("statPending"),
  reviewBadge: document.getElementById("reviewBadge"),
  reviewList: document.getElementById("reviewList"),
  chatScroll: document.getElementById("chatScroll"),
  messages: document.getElementById("messages"),
  welcomeCard: document.getElementById("welcomeCard"),
  composerForm: document.getElementById("composerForm"),
  composerInput: document.getElementById("composerInput"),
  sendBtn: document.getElementById("sendBtn"),
  sidebar: document.getElementById("sidebar"),
  sidebarToggle: document.getElementById("sidebarToggle"),
  modalBackdrop: document.getElementById("modalBackdrop"),
  modalTitle: document.getElementById("modalTitle"),
  modalBody: document.getElementById("modalBody"),
  modalClose: document.getElementById("modalClose"),
};

let isStreaming = false;

// ---------------------------------------------------------------------------
// Sidebar toggle (mobile)
// ---------------------------------------------------------------------------
el.sidebarToggle.addEventListener("click", () => el.sidebar.classList.toggle("open"));

// ---------------------------------------------------------------------------
// Health + stats + review queue polling
// ---------------------------------------------------------------------------
async function refreshHealth() {
  try {
    const res = await fetch(`${API}/health`);
    const data = await res.json();
    if (data.ingestion_error) {
      el.statusDot.className = "status-dot offline";
      el.statusText.textContent = `Ingestion failed: ${data.ingestion_error}`;
    } else if (data.ingested) {
      el.statusDot.className = "status-dot online";
      el.statusText.textContent = "Backend online";
    } else {
      el.statusDot.className = "status-dot";
      el.statusText.textContent = "Ingesting Act text…";
    }
  } catch {
    el.statusDot.className = "status-dot offline";
    el.statusText.textContent = "Backend unreachable";
  }
}

async function refreshStats() {
  try {
    const res = await fetch(`${API}/stats`);
    const data = await res.json();
    el.statIndexed.textContent = data.indexed_in_qdrant ?? "–";
    el.statPending.textContent = data.pending_review ?? "–";
    el.reviewBadge.textContent = data.pending_review ?? "0";
  } catch { /* backend still starting */ }
}

async function refreshReviewQueue() {
  try {
    const res = await fetch(`${API}/pending-review`);
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
  cardEl.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    await fetch(`${API}/approve-review-item`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ section_id: sectionId, decision, reviewer: "demo_reviewer" }),
    });
    await Promise.all([refreshReviewQueue(), refreshStats()]);
  } catch {
    cardEl.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
}

refreshHealth();
refreshStats();
refreshReviewQueue();
setInterval(refreshHealth, 8000);
setInterval(() => { refreshStats(); refreshReviewQueue(); }, 6000);

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------
function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

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
      <div class="msg-role">${role === "user" ? "You" : "DPDP Assistant"}</div>
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
      body: JSON.stringify({ query }),
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
          assistantContent.textContent = `The local model hit an error: ${evt.detail}`;
        } else if (evt.type === "done") {
          citationMeta = evt.citation_meta || [];
          if (evt.status === "no_answer") {
            assistantContent.className = "msg-content status-empty";
            assistantContent.textContent = evt.note;
          } else if (evt.status === "pending_review") {
            assistantContent.className = "msg-content status-pending";
            assistantContent.textContent = evt.note;
          } else if (firstToken) {
            // shouldn't normally happen, but guards against an empty stream
            assistantContent.textContent = "(no content generated)";
          }
          renderCitations(assistantContent, citationMeta, evt.cached);
        }
      }
    }
  } catch (err) {
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
    const res = await fetch(`${API}/section/${sectionId}`);
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

document.querySelectorAll(".suggestion-chip").forEach((chip) => {
  chip.addEventListener("click", () => sendQuery(chip.dataset.q));
});
