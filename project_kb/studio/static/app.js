(function () {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";

  async function api(path, options = {}) {
    const headers = options.headers || {};
    if (options.method && options.method !== "GET") {
      headers["x-csrf-token"] = csrf;
      if (!(options.body instanceof FormData)) {
        headers["content-type"] = "application/json";
      }
    }
    const response = await fetch(path, { ...options, headers });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || response.statusText);
    }
    return payload;
  }

  function showMessage(text, kind = "system") {
    const target = document.getElementById("chat-messages");
    if (!target) return;
    const node = document.createElement("div");
    node.className = `message ${kind}`;
    node.textContent = text;
    target.appendChild(node);
    target.scrollTop = target.scrollHeight;
  }

  function renderEvidence(items) {
    const panel = document.getElementById("evidence-panel");
    if (!panel) return;
    panel.innerHTML = "";
    items.forEach((item) => {
      const node = document.createElement("div");
      node.className = "evidence-item";
      node.innerHTML = `<code>${escapeHtml(item.source_path || "")}</code><small>${escapeHtml(item.heading || "")} chunk=${escapeHtml(String(item.chunk_index ?? ""))}</small><span>${escapeHtml(item.snippet || "")}</span>`;
      panel.appendChild(node);
    });
  }

  function escapeHtml(value) {
    value = String(value ?? "");
    return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  }

  async function copyText(value) {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }

  document.getElementById("chat-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = document.getElementById("chat-question").value.trim();
    if (!question) return;
    showMessage(question, "user");
    showMessage("Loading local evidence search...", "system");
    try {
      const payload = await api("/api/chat/messages", {
        method: "POST",
        body: JSON.stringify({
          question,
          source_mode: document.getElementById("chat-source").value,
          search_mode: document.getElementById("chat-search-mode").value,
          provider: document.getElementById("chat-provider").value,
        }),
      });
      const answer = payload.answer_available ? payload.answer : "Evidence Search Mode: no full answer was generated. Review the evidence panel.";
      showMessage(answer, "assistant");
      if (payload.warnings?.length) showMessage(payload.warnings.join("\n"), "system");
      renderEvidence(payload.evidence || []);
    } catch (error) {
      showMessage(error.message, "system");
    }
  });

  document.getElementById("upload-button")?.addEventListener("click", async () => {
    const input = document.getElementById("source-files");
    const data = new FormData();
    Array.from(input.files || []).forEach((file) => data.append("files", file));
    try {
      await api("/api/sources/upload", { method: "POST", body: data });
      location.reload();
    } catch (error) {
      alert(error.message);
    }
  });

  const dropZone = document.getElementById("drop-zone");
  if (dropZone) {
    ["dragenter", "dragover"].forEach((name) => dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      dropZone.classList.add("dragover");
    }));
    ["dragleave", "drop"].forEach((name) => dropZone.addEventListener(name, () => dropZone.classList.remove("dragover")));
    dropZone.addEventListener("drop", async (event) => {
      event.preventDefault();
      const data = new FormData();
      Array.from(event.dataTransfer.files || []).forEach((file) => data.append("files", file));
      try {
        await api("/api/sources/upload", { method: "POST", body: data });
        location.reload();
      } catch (error) {
        alert(error.message);
      }
    });
  }

  document.querySelectorAll("[data-job]").forEach((button) => {
    button.addEventListener("click", async () => {
      const type = button.getAttribute("data-job");
      const path = type === "import" ? "/api/jobs/import" : "/api/jobs/curate";
      try {
        const payload = await api(path, { method: "POST", body: "{}" });
        alert(`Job queued: ${payload.job_id}`);
      } catch (error) {
        alert(error.message);
      }
    });
  });

  let currentReviewNote = null;

  function hasMissingSourceRefs(note) {
    const refs = note?.source_refs || [];
    return !refs.some((ref) => String(ref.source_path || "").trim());
  }

  function renderReviewList(items) {
    const list = document.getElementById("review-note-list");
    if (!list) return;
    list.innerHTML = "";
    items.forEach((note) => {
      const row = document.createElement("button");
      row.className = "note-row";
      row.dataset.noteId = note.id;
      row.innerHTML = `<span>${escapeHtml(note.title || note.rel_path)}</span><small>${escapeHtml(note.status || "")}</small>`;
      row.addEventListener("click", () => loadReviewNote(note.id));
      list.appendChild(row);
    });
    if (items.length) {
      loadReviewNote(items[0].id);
    } else {
      renderReviewNote(null);
    }
  }

  function renderReviewNote(note) {
    currentReviewNote = note;
    document.querySelectorAll(".note-row").forEach((row) => {
      row.classList.toggle("active", note && row.dataset.noteId === note.id);
    });
    const title = document.getElementById("review-title");
    if (!title) return;
    const path = document.getElementById("review-path");
    const preview = document.getElementById("review-preview");
    const refs = document.getElementById("review-source-refs");
    const warnings = document.getElementById("review-warnings");
    const obsidianPath = document.getElementById("review-obsidian-path");
    const obsidianLink = document.getElementById("review-open-obsidian");
    const overrideRow = document.getElementById("review-override-row");
    const override = document.getElementById("review-override-missing-refs");
    const message = document.getElementById("review-action-message");
    const approve = document.getElementById("review-approve");
    const gap = document.getElementById("review-mark-gap");
    const duplicate = document.getElementById("review-mark-duplicate");
    if (!note) {
      title.textContent = "No matching notes";
      path.textContent = "";
      preview.textContent = "";
      refs.innerHTML = "";
      warnings.innerHTML = "";
      obsidianPath.textContent = "";
      obsidianLink.href = "#";
      [approve, gap, duplicate].forEach((button) => { if (button) button.disabled = true; });
      return;
    }
    title.textContent = note.title || note.rel_path;
    path.textContent = note.rel_path || "";
    preview.textContent = note.body_preview || "";
    refs.innerHTML = (note.source_refs || []).map((ref) => (
      `<div class="source-ref"><code>${escapeHtml(ref.source_path || "")}</code><small>${escapeHtml(ref.heading || "")} chunk=${escapeHtml(String(ref.chunk_index ?? ""))}</small></div>`
    )).join("") || "<p class=\"muted\">No source_refs</p>";
    warnings.innerHTML = (note.warnings || []).map((warning) => `<p class="warning">${escapeHtml(warning)}</p>`).join("");
    obsidianPath.textContent = note.obsidian_path || note.rel_path || "";
    obsidianLink.href = note.obsidian_uri || "#";
    const missingRefs = hasMissingSourceRefs(note);
    overrideRow?.classList.toggle("hidden", !missingRefs || note.status !== "needs_review");
    if (override) override.checked = false;
    if (message) message.textContent = missingRefs && note.status === "needs_review" ? "Approve needs explicit override because source_refs are missing." : "";
    if (approve) approve.disabled = note.status !== "needs_review" || missingRefs;
    if (gap) gap.disabled = note.status === "reviewed";
    if (duplicate) duplicate.disabled = note.status === "reviewed";
  }

  async function loadReviewNote(noteId) {
    try {
      const payload = await api(`/api/review-items/${noteId}`);
      renderReviewNote(payload.note);
    } catch (error) {
      alert(error.message);
    }
  }

  async function refreshReviewList() {
    const status = document.getElementById("review-status-filter")?.value || "";
    const missing = document.getElementById("review-missing-source-filter")?.checked;
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (missing) params.set("missing_source_refs", "true");
    const payload = await api(`/api/review-items${params.toString() ? `?${params}` : ""}`);
    renderReviewList(payload.items || []);
  }

  document.querySelectorAll(".note-row").forEach((row) => {
    row.addEventListener("click", () => loadReviewNote(row.dataset.noteId));
  });
  if (document.getElementById("review-note-list")) {
    const first = document.querySelector(".note-row");
    if (first) loadReviewNote(first.dataset.noteId);
  }
  document.getElementById("review-status-filter")?.addEventListener("change", refreshReviewList);
  document.getElementById("review-missing-source-filter")?.addEventListener("change", refreshReviewList);
  document.getElementById("review-override-missing-refs")?.addEventListener("change", (event) => {
    const approve = document.getElementById("review-approve");
    if (approve && currentReviewNote) {
      approve.disabled = currentReviewNote.status !== "needs_review" || (hasMissingSourceRefs(currentReviewNote) && !event.target.checked);
    }
  });
  document.getElementById("review-approve")?.addEventListener("click", async () => {
    if (!currentReviewNote) return;
    const override = document.getElementById("review-override-missing-refs")?.checked || false;
    if (override && !confirm("Approve this note without source_refs? A warning will be recorded.")) return;
    try {
      const payload = await api(`/api/review/${currentReviewNote.id}/approve`, {
        method: "POST",
        body: JSON.stringify({ override_missing_refs: override }),
      });
      renderReviewNote(payload.note);
      await refreshReviewList();
    } catch (error) {
      alert(error.message);
    }
  });
  document.getElementById("review-mark-gap")?.addEventListener("click", async () => {
    if (!currentReviewNote) return;
    try {
      const payload = await api(`/api/review/${currentReviewNote.id}/mark-evidence-gap`, { method: "POST", body: "{}" });
      renderReviewNote(payload.note);
      await refreshReviewList();
    } catch (error) {
      alert(error.message);
    }
  });
  document.getElementById("review-mark-duplicate")?.addEventListener("click", async () => {
    if (!currentReviewNote) return;
    try {
      const payload = await api(`/api/review/${currentReviewNote.id}/mark-duplicate`, { method: "POST", body: "{}" });
      renderReviewNote(payload.note);
      await refreshReviewList();
    } catch (error) {
      alert(error.message);
    }
  });
  document.getElementById("review-copy-path")?.addEventListener("click", async () => {
    const value = document.getElementById("review-obsidian-path")?.textContent || "";
    if (!value) return;
    await copyText(value);
  });

  document.getElementById("publish-button")?.addEventListener("click", async () => {
    if (!confirm("Publish reviewed docs and rebuild the agent index?")) return;
    try {
      const payload = await api("/api/publish/confirm", { method: "POST", body: "{}" });
      alert(`Publish job queued: ${payload.job_id}`);
    } catch (error) {
      alert(error.message);
    }
  });

  document.querySelectorAll("[data-preview-agent]").forEach((button) => {
    button.addEventListener("click", async () => {
      const agent = button.getAttribute("data-preview-agent");
      const payload = await api(`/api/agent-hub/${agent}/preview-install`, { method: "POST", body: "{}" });
      document.getElementById("agent-preview").textContent = payload.diff || payload.preview;
    });
  });

  document.querySelectorAll("[data-confirm-agent]").forEach((button) => {
    button.addEventListener("click", async () => {
      const agent = button.getAttribute("data-confirm-agent");
      if (!confirm(`Write ${agent} MCP config after backup?`)) return;
      const payload = await api(`/api/agent-hub/${agent}/confirm-install`, { method: "POST", body: "{}" });
      document.getElementById("agent-preview").textContent = JSON.stringify(payload, null, 2);
    });
  });

  document.querySelectorAll("[data-test-agent]").forEach((button) => {
    button.addEventListener("click", async () => {
      const agent = button.getAttribute("data-test-agent");
      try {
        const payload = await api(`/api/agent-hub/${agent}/test`, { method: "POST", body: "{}" });
        document.getElementById("agent-preview").textContent = JSON.stringify(payload, null, 2);
      } catch (error) {
        alert(error.message);
      }
    });
  });

  document.querySelectorAll("[data-prompt-agent]").forEach((button) => {
    button.addEventListener("click", async () => {
      const agent = button.getAttribute("data-prompt-agent");
      try {
        const payload = await api(`/api/agent-hub/${agent}/prompt`, { method: "POST", body: "{}" });
        await copyText(payload.prompt || "");
        document.getElementById("agent-preview").textContent = payload.prompt || "";
      } catch (error) {
        alert(error.message);
      }
    });
  });

  document.getElementById("save-settings")?.addEventListener("click", async () => {
    const payload = {
      ui_language: document.getElementById("ui-language").value,
      content_language: document.getElementById("content-language").value,
      profile: document.getElementById("profile").value,
      default_chat_source: document.getElementById("default-chat-source").value,
      external_llm_enabled: document.getElementById("external-llm-enabled").checked,
    };
    await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
    location.reload();
  });

  document.getElementById("quick-language")?.addEventListener("change", async (event) => {
    const value = event.target.value;
    await api("/api/settings", { method: "POST", body: JSON.stringify({ ui_language: value }) });
    if (value === "follow_browser") {
      document.cookie = "lang=; Max-Age=0; SameSite=Strict; path=/";
    } else {
      document.cookie = `lang=${value}; SameSite=Strict; path=/`;
    }
    location.href = location.pathname;
  });
})();
