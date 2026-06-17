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
    return value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  }

  document.getElementById("chat-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = document.getElementById("chat-question").value.trim();
    if (!question) return;
    showMessage(question, "user");
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

  document.querySelectorAll("[data-approve]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await api(`/api/review/${button.getAttribute("data-approve")}/approve`, { method: "POST", body: "{}" });
        location.reload();
      } catch (error) {
        alert(error.message);
      }
    });
  });

  document.querySelectorAll("[data-mark-gap]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/review/${button.getAttribute("data-mark-gap")}/mark-evidence-gap`, { method: "POST", body: "{}" });
      location.reload();
    });
  });

  document.querySelectorAll("[data-mark-duplicate]").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/review/${button.getAttribute("data-mark-duplicate")}/mark-duplicate`, { method: "POST", body: "{}" });
      location.reload();
    });
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
