/**
 * frontend_snippet.js
 * --------------------
 * Additive JS for the banking regulatory intelligence expansion.
 * Paste this at the end of your existing app.js, AFTER the line that
 * defines your API base URL constant (the same one /ask already uses -
 * per your README, app.js has "the backend's public URL set directly in
 * the API constant near the top of the file"). This code assumes that
 * constant is named API_BASE_URL - rename the reference below to match
 * your actual constant name if it differs.
 */

// ---- 1. Law selector: read the selected law wherever you currently
// build the /ask request body, and add it as a `law` field. Example -
// adjust to match your actual fetch call for /ask:
//
//   const selectedLaw = document.getElementById('bri-law-selector').value;
//   fetch(`${API_BASE_URL}/ask`, {
//     method: 'POST',
//     headers: { 'Content-Type': 'application/json' },
//     body: JSON.stringify({ question: userQuestion, law: selectedLaw }),
//   });
//
// This one line (`law: selectedLaw`) is the only change needed to your
// existing /ask call, once your backend /ask handler accepts a `law`
// field - see INTEGRATION_GUIDE.md for that backend change.

// ---- 2. Classify & Policy tool ----
document.addEventListener("DOMContentLoaded", () => {
  const classifyBtn = document.getElementById("bri-classify-btn");
  const classifyInput = document.getElementById("bri-classify-input");
  const panel = document.getElementById("bri-policy-panel");
  const classesEl = document.getElementById("bri-policy-classes");
  const controlsEl = document.getElementById("bri-policy-controls");
  const rationaleEl = document.getElementById("bri-policy-rationale");

  if (!classifyBtn) return; // snippet not mounted on this page

  classifyBtn.addEventListener("click", async () => {
    const text = classifyInput.value.trim();
    if (!text) return;

    classifyBtn.disabled = true;
    classifyBtn.textContent = "Classifying...";

    try {
      const response = await fetch(`${API_BASE_URL}/evaluate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });

      if (!response.ok) {
        throw new Error(`Server returned ${response.status}`);
      }

      const result = await response.json();
      renderPolicyResult(result);
    } catch (err) {
      console.error("Classification failed:", err);
      classesEl.textContent = "";
      controlsEl.textContent = "Could not classify - check that the backend is running and reachable.";
      rationaleEl.textContent = "";
      panel.hidden = false;
    } finally {
      classifyBtn.disabled = false;
      classifyBtn.textContent = "Classify & Get Policy";
    }
  });

  function renderPolicyResult(result) {
    panel.hidden = false;

    if (!result.data_classes || result.data_classes.length === 0) {
      classesEl.textContent = "No data classification matched - no automatic controls apply. Consider manual review.";
      controlsEl.textContent = "";
      rationaleEl.textContent = "";
      return;
    }

    classesEl.innerHTML =
      "<strong>Classification:</strong> " +
      result.data_classes.map((c) => `<span class="bri-tag">${escapeHtml(c)}</span>`).join(" ");

    controlsEl.innerHTML =
      "<strong>Required controls:</strong> " +
      result.required_controls.map((c) => `<span class="bri-control">${escapeHtml(c)}</span>`).join(" ");

    const rationaleLines = Object.entries(result.rationale || {})
      .map(([cls, text]) => `<li><strong>${escapeHtml(cls)}:</strong> ${escapeHtml(text)}</li>`)
      .join("");
    rationaleEl.innerHTML = rationaleLines ? `<ul>${rationaleLines}</ul>` : "";
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }
});
