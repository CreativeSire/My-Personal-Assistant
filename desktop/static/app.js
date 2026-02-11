const chatStream = document.getElementById("chatStream");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");
const metricsEl = document.getElementById("metrics");
const healthBars = document.getElementById("healthBars");
const tasksList = document.getElementById("tasksList");
const logsList = document.getElementById("logsList");
const invoiceForm = document.getElementById("invoiceForm");
const invoicePath = document.getElementById("invoicePath");
const invoiceStatus = document.getElementById("invoiceStatus");
const truthRows = document.getElementById("truthRows");
const ingestFile = document.getElementById("ingestFile");
const dropZone = document.querySelector(".drop-zone");
const stageTitle = document.getElementById("stageTitle");
const stageSubtitle = document.getElementById("stageSubtitle");
const devAgentStatus = document.getElementById("devAgentStatus");
const rowModal = document.getElementById("rowModal");
const modalContent = document.getElementById("modalContent");
const closeModal = document.getElementById("closeModal");
const watchtower = document.getElementById("watchtower");
const toggleWatchtower = document.getElementById("toggleWatchtower");
const auditorConsole = document.getElementById("auditorConsole");
const toggleAuditor = document.getElementById("toggleAuditor");

const railButtons = document.querySelectorAll(".rail-btn");
const tabViews = document.querySelectorAll(".tab-view");

let latestTasks = [];
let latestGridRows = [];
let thinkingNode = null;

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  chatStream.appendChild(div);
  chatStream.scrollTop = chatStream.scrollHeight;
}

function showThinking() {
  if (thinkingNode) return;
  const div = document.createElement("div");
  div.className = "msg bot thinking";
  div.innerHTML = `
    <span class="think-wave"></span>
    <span class="think-wave"></span>
    <span class="think-wave"></span>
  `;
  chatStream.appendChild(div);
  chatStream.scrollTop = chatStream.scrollHeight;
  thinkingNode = div;
}

function hideThinking() {
  if (!thinkingNode) return;
  thinkingNode.remove();
  thinkingNode = null;
}

function setStageMeta(tab) {
  const meta = {
    command: ["Command Center", "Primary operations channel"],
    foundry: ["Invoice Foundry", "Document processing and truth grid"],
    cortex: ["Cortex", "Memory intelligence layer"],
    systems: ["Systems", "Runtime and platform controls"],
  };
  const [title, subtitle] = meta[tab] || meta.command;
  stageTitle.textContent = title;
  stageSubtitle.textContent = subtitle;
}

function switchTab(tab) {
  railButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  tabViews.forEach((view) => {
    view.classList.toggle("active", view.id === `tab-${tab}`);
  });
  setStageMeta(tab);
}

async function apiGet(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} failed`);
  return res.json();
}

async function apiPost(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const json = await res.json();
  if (!res.ok || !json.ok) throw new Error(json.error || `POST ${url} failed`);
  return json;
}

function barClass(value) {
  if (value >= 90) return "danger";
  if (value >= 75) return "warn";
  return "";
}

function renderSystemHealthBars(metrics) {
  const rows = [
    ["CPU", Number(metrics.cpu || 0)],
    ["RAM", Number(metrics.ram || 0)],
    ["DISK", Number(metrics.disk || 0)],
  ];
  healthBars.innerHTML = rows.map(([name, value]) => (
    `<div class="bar-row">
      <div class="label">${name}: ${value.toFixed(1)}%</div>
      <div class="bar-bg"><div class="bar-fill ${barClass(value)}" style="width:${Math.max(0, Math.min(100, value))}%"></div></div>
    </div>`
  )).join("");
}

async function refreshHealth() {
  try {
    const data = await apiGet("/api/health");
    if (!data.ok) throw new Error("health check failed");
  } catch (_err) {
    addMessage("bot", "Health check degraded.");
  }
}

async function refreshMetrics() {
  try {
    const data = await apiGet("/api/metrics");
    const m = data.metrics || {};
    const entries = [
      ["CPU", `${m.cpu ?? "-"}%`],
      ["RAM", `${m.ram ?? "-"}%`],
      ["DISK", `${m.disk ?? "-"}%`],
      ["Queue", `${m.queue_depth ?? "-"}`],
    ];
    metricsEl.innerHTML = entries.map(([k, v]) => (
      `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div></div>`
    )).join("");
    renderSystemHealthBars(m);
    devAgentStatus.textContent = Number(m.queue_depth || 0) > 0 ? "Scanning" : "Idle";
    devAgentStatus.className = `card-value ${Number(m.queue_depth || 0) > 0 ? "scanning" : "idle"}`;
  } catch (err) {
    metricsEl.innerHTML = `<div class="terminal-line warn">Metrics unavailable: ${err.message}</div>`;
  }
}

function statusSymbol(status) {
  if (status === "completed") return "●";
  if (status === "failed") return "▲";
  if (status === "running") return "◍";
  return "○";
}

function statusLineClass(status) {
  if (status === "completed") return "ok";
  if (status === "failed") return "warn";
  return "";
}

function parseSummaryFromResult(result) {
  const text = String(result || "");
  const m = text.match(/Total=(\d+), OK=(\d+), Review=(\d+), Skipped=(\d+), Failed=(\d+)/i);
  if (!m) return null;
  return {
    total: Number(m[1]),
    ok: Number(m[2]),
    review: Number(m[3]),
    skipped: Number(m[4]),
    failed: Number(m[5]),
  };
}

function renderTruthGrid(rows) {
  if (!rows.length) {
    truthRows.innerHTML = `<tr><td colspan="7">No processed invoice rows yet. Queue a file to populate this grid.</td></tr>`;
    return;
  }
  truthRows.innerHTML = rows.map((r, idx) => {
    const confidencePct = Math.round((Number(r.confidence_score || 0)) * 100);
    const rawStatus = String(r.status || "").toLowerCase();
    const isFail = rawStatus === "failed";
    const isWarn = Boolean(r.warning) || rawStatus.includes("warning") || rawStatus === "review";
    const statusClass = isFail ? "fail" : (isWarn ? "warn" : "ok");
    const statusText = isFail ? "Failed" : (isWarn ? "Warning" : "Success");
    const invoiceValue = r.invoice_number || "-";
    const receiver = r.receiver || "-";
    const location = r.location || "-";
    const dateValue = r.delivery_date || "-";
    return (
    `<tr class="${isWarn ? "warn" : ""}" data-row="${idx}">
      <td><span class="status-pill ${statusClass}">${statusText}</span></td>
      <td title="${r.file_name}"><span class="cell-clip">${r.file_name}</span></td>
      <td title="${receiver}"><span class="cell-clip">${receiver}</span></td>
      <td title="${location}"><span class="cell-clip">${location}</span></td>
      <td title="${invoiceValue}"><span class="cell-clip">${invoiceValue}</span></td>
      <td title="${dateValue}"><span class="cell-clip">${dateValue}</span></td>
      <td><span class="status-pill ${statusClass}">${confidencePct}%</span></td>
    </tr>`
  );
}).join("");

  const trNodes = truthRows.querySelectorAll("tr[data-row]");
  trNodes.forEach((node) => {
    node.addEventListener("click", () => {
      const row = rows[Number(node.dataset.row)];
      modalContent.textContent = JSON.stringify(row.raw, null, 2);
      rowModal.classList.add("show");
    });
  });
}

async function refreshTruthGrid() {
  try {
    const data = await apiGet("/api/invoice/grid");
    latestGridRows = data.rows || [];
    renderTruthGrid(latestGridRows);
  } catch (err) {
    truthRows.innerHTML = `<tr><td colspan="7">Grid refresh failed: ${err.message}</td></tr>`;
  }
}

async function refreshTasks() {
  try {
    const data = await apiGet("/api/tasks");
    latestTasks = data.tasks || [];
    tasksList.innerHTML = latestTasks.slice(0, 24).map((t) => (
      `<div class="terminal-line ${statusLineClass(t.status)}">[${statusSymbol(t.status)}] ${t.task_type} #${String(t.id).slice(0, 8)} ${t.progress}%</div>`
    )).join("");
    if (!latestTasks.length) {
      tasksList.innerHTML = `<div class="terminal-line">No tasks yet.</div>`;
    }
    tasksList.scrollTop = tasksList.scrollHeight;
  } catch (err) {
    tasksList.innerHTML = `<div class="terminal-line warn">Task refresh failed: ${err.message}</div>`;
  }
}

async function refreshLogs() {
  try {
    const data = await apiGet("/api/logs");
    const rows = data.rows || [];
    logsList.innerHTML = rows.slice(-40).map((r) => (
      `<div class="terminal-line">${r.Timestamp || ""} ${r.Agent || ""} ${r.Action || ""} ${r.Status || ""}</div>`
    )).join("");
    if (!rows.length) logsList.innerHTML = `<div class="terminal-line">No logs yet.</div>`;
    logsList.scrollTop = logsList.scrollHeight;
  } catch (err) {
    logsList.innerHTML = `<div class="terminal-line warn">Log refresh failed: ${err.message}</div>`;
  }
}

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;
  addMessage("user", message);
  chatInput.value = "";
  showThinking();
  try {
    const data = await apiPost("/api/chat", { message, user_id: "desktop_ceejay" });
    hideThinking();
    addMessage("bot", data.response || "(no response)");
  } catch (err) {
    hideThinking();
    addMessage("bot", `Error: ${err.message}`);
  }
});

invoiceForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const path = invoicePath.value.trim();
  if (!path) return;
  invoiceStatus.textContent = "Queueing batch...";
  try {
    const data = await apiPost("/api/invoice/enqueue", { input_path: path, user_id: "desktop_ceejay" });
    invoiceStatus.textContent = `Queued task: ${data.task_id}`;
    refreshTasks();
  } catch (err) {
    invoiceStatus.textContent = `Queue failed: ${err.message}`;
  }
});

function setupDropZone() {
  ["dragenter", "dragover"].forEach((evt) => {
    dropZone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropZone.classList.add("drag-over");
    });
  });
  ["dragleave", "drop"].forEach((evt) => {
    dropZone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropZone.classList.remove("drag-over");
    });
  });
  dropZone.addEventListener("drop", (e) => {
    const files = e.dataTransfer?.files;
    if (!files || !files.length) return;
    const f = files[0];
    uploadAndQueueFile(f);
  });
  ingestFile.addEventListener("change", () => {
    const f = ingestFile.files?.[0];
    if (!f) return;
    uploadAndQueueFile(f);
  });
}

async function uploadAndQueueFile(file) {
  invoiceStatus.textContent = `Uploading ${file.name}...`;
  const fd = new FormData();
  fd.append("file", file);
  fd.append("user_id", "desktop_ceejay");
  try {
    const res = await fetch("/api/invoice/upload", { method: "POST", body: fd });
    const json = await res.json();
    if (!res.ok || !json.ok) {
      throw new Error(json.error || "upload failed");
    }
    invoiceStatus.textContent = `Queued ${json.filename} as task ${json.task_id}`;
    await refreshTasks();
    await refreshTruthGrid();
  } catch (err) {
    invoiceStatus.textContent = `Upload failed: ${err.message}`;
  }
}

toggleWatchtower.addEventListener("click", () => {
  watchtower.classList.toggle("collapsed");
  toggleWatchtower.textContent = watchtower.classList.contains("collapsed") ? "❮" : "❯";
});

toggleAuditor.addEventListener("click", () => {
  auditorConsole.classList.toggle("collapsed");
  toggleAuditor.textContent = auditorConsole.classList.contains("collapsed") ? "⌃" : "⌄";
});

closeModal.addEventListener("click", () => rowModal.classList.remove("show"));
rowModal.addEventListener("click", (e) => {
  if (e.target === rowModal) rowModal.classList.remove("show");
});

railButtons.forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

async function boot() {
  setupDropZone();
  switchTab("command");
  await refreshHealth();
  await refreshMetrics();
  await refreshTasks();
  await refreshTruthGrid();
  await refreshLogs();
  setInterval(refreshHealth, 7000);
  setInterval(refreshMetrics, 5000);
  setInterval(refreshTasks, 3500);
  setInterval(refreshTruthGrid, 3500);
  setInterval(refreshLogs, 7000);
}

boot();
