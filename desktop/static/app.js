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
const rowModal = document.getElementById("rowModal");
const modalContent = document.getElementById("modalContent");
const closeModal = document.getElementById("closeModal");
const watchtower = document.getElementById("watchtower");
const toggleWatchtower = document.getElementById("toggleWatchtower");
const auditorConsole = document.getElementById("auditorConsole");
const toggleAuditor = document.getElementById("toggleAuditor");
const historyGroups = document.getElementById("historyGroups");
const refreshSessions = document.getElementById("refreshSessions");
const newChat = document.getElementById("newChat");
const agentActivityText = document.getElementById("agentActivityText");
const gatewayState = document.getElementById("gatewayState");
const commandTrace = document.getElementById("commandTrace");
const visionRec = document.getElementById("visionRec");
const missionBoard = document.getElementById("missionBoard");
const resourceRows = document.getElementById("resourceRows");
const leftResourceRows = document.getElementById("leftResourceRows");
const collectionBin = document.getElementById("collectionBin");
const collectionMeta = document.getElementById("collectionMeta");
const openOutputFolderBtn = document.getElementById("openOutputFolderBtn");
const downloadZipBtn = document.getElementById("downloadZipBtn");
const exportAuditBtn = document.getElementById("exportAuditBtn");
const artifactGrid = document.getElementById("artifactGrid");
const leftArtifactRail = document.getElementById("leftArtifactRail");
const macroSitrep = document.getElementById("macroSitrep");
const macroReboot = document.getElementById("macroReboot");
const macroClearLogs = document.getElementById("macroClearLogs");
const macroOpenVault = document.getElementById("macroOpenVault");
const strategyGoals = document.getElementById("strategyGoals");
const strategyPlans = document.getElementById("strategyPlans");
const toggleClipboardMonitor = document.getElementById("toggleClipboardMonitor");
const toggleTimelineMode = document.getElementById("toggleTimelineMode");
const timelineList = document.getElementById("timelineList");
const workflowBuilder = document.getElementById("workflowBuilder");

const railButtons = document.querySelectorAll(".rail-btn");
const tabViews = document.querySelectorAll(".tab-view");
const agentNodes = document.querySelectorAll(".agent-node");
const chainLines = document.querySelectorAll(".chain-svg line");
const galleryFilterButtons = document.querySelectorAll(".gallery-filter");

let latestTasks = [];
let latestGridRows = [];
let latestOutputMeta = null;
let currentSessionId = `desktop_desktop_ceejay_${Date.now()}`;
let thinkingNode = null;
let currentArtifactFilter = "all";
let missionItems = [];
let resourceState = {};
let announcedCollectionTaskId = "";
let desktopSettings = { monitor_clipboard: false, timeline_mode: true };
let lastTimelineItems = [];
let workflowDraft = { name: "workflow_desktop_new", nodes: [], edges: [] };

const NODE_MAP = {
  Chief_of_Staff: "node-chief",
  Planner_Agent: "node-planner",
  Dev_Agent: "node-dev",
  Research_Agent: "node-research",
  Data_Scientist: "node-data",
  Script_Agent: "node-script",
  Academic_Writer: "node-academic",
};

const CHIEF_LINES = {
  Dev_Agent: "line-chief-dev",
  Research_Agent: "line-chief-research",
  Data_Scientist: "line-chief-data",
  Script_Agent: "line-chief-script",
  Academic_Writer: "line-chief-academic",
};

const PLANNER_LINES = {
  Dev_Agent: "line-planner-dev",
  Research_Agent: "line-planner-research",
  Data_Scientist: "line-planner-data",
  Script_Agent: "line-planner-script",
};

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function newSessionId() {
  return `desktop_desktop_ceejay_${Date.now()}_${Math.random().toString(16).slice(2, 7)}`;
}

function clearChain() {
  agentNodes.forEach((n) => n.classList.remove("active", "working", "error"));
  chainLines.forEach((l) => l.classList.remove("active"));
}

function setGateway(allowed) {
  if (!gatewayState) return;
  gatewayState.classList.toggle("allow", allowed);
  gatewayState.classList.toggle("block", !allowed);
  gatewayState.textContent = allowed ? "ALLOW" : "BLOCK";
}

function describeAgentActivity(agentName) {
  const map = {
    Chief_of_Staff: "Chief_of_Staff is routing your mission.",
    Planner_Agent: "Planner_Agent is decomposing the mission plan.",
    Dev_Agent: "Dev_Agent is inspecting code and scanning visual state.",
    Research_Agent: "Research_Agent is querying live sources.",
    Data_Scientist: "Data_Scientist is analyzing structured data.",
    Script_Agent: "Script_Agent is preparing communication output.",
    Academic_Writer: "Academic_Writer is drafting formal content.",
  };
  return map[agentName] || "Chief_of_Staff is coordinating execution.";
}

function applyResourceDimming() {
  Object.entries(NODE_MAP).forEach(([agent, nodeId]) => {
    const node = document.getElementById(nodeId);
    if (!node) return;
    const active = resourceState[agent] !== false;
    node.classList.toggle("sleeping", !active);
  });
}

function highlightChain(activeAgent, delegatedAgents = []) {
  clearChain();
  document.getElementById(NODE_MAP.Chief_of_Staff)?.classList.add("active", "working");

  const hasPlanner = delegatedAgents.includes("Planner_Agent") || activeAgent === "Planner_Agent";
  const selected = activeAgent || "Chief_of_Staff";
  const nodeId = NODE_MAP[selected];
  if (nodeId) document.getElementById(nodeId)?.classList.add("active", "working");

  if (hasPlanner) {
    document.getElementById(NODE_MAP.Planner_Agent)?.classList.add("active", "working");
    document.getElementById("line-chief-planner")?.classList.add("active");
    if (selected !== "Planner_Agent" && PLANNER_LINES[selected]) {
      document.getElementById(PLANNER_LINES[selected])?.classList.add("active");
    }
    if (commandTrace) {
      commandTrace.textContent = selected === "Planner_Agent"
        ? "Chief_of_Staff -> Planner_Agent"
        : `Chief_of_Staff -> Planner_Agent -> ${selected}`;
    }
  } else if (CHIEF_LINES[selected]) {
    document.getElementById(CHIEF_LINES[selected])?.classList.add("active");
    if (commandTrace) commandTrace.textContent = `Chief_of_Staff -> ${selected}`;
  } else {
    if (commandTrace) commandTrace.textContent = "Chief_of_Staff awaiting task routing.";
  }

  if (visionRec) {
    visionRec.classList.toggle("hidden", selected !== "Dev_Agent");
  }
  applyResourceDimming();
}

function relTimeNow() {
  return "now";
}

function addMessage(role, text, meta = {}) {
  if (!chatStream) return;
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  if (role === "bot") {
    const agent = meta.agent || "Chief of Staff";
    div.innerHTML = `
      <div class="msg-head">
        <span class="msg-avatar">V</span>
        <span class="msg-title">Victor (${escapeHtml(agent)})</span>
        <span class="msg-time">${escapeHtml(meta.time || relTimeNow())}</span>
      </div>
      <div>${escapeHtml(text)}</div>
    `;
  } else {
    div.textContent = text;
  }
  chatStream.appendChild(div);
  chatStream.scrollTop = chatStream.scrollHeight;
}

function addStreamingBotMessage(meta = {}) {
  if (!chatStream) return null;
  const div = document.createElement("div");
  div.className = "msg bot streaming";
  const agent = meta.agent || "Chief of Staff";
  div.innerHTML = `
    <div class="msg-head">
      <span class="msg-avatar">V</span>
      <span class="msg-title">Victor (${escapeHtml(agent)})</span>
      <span class="msg-time">${escapeHtml(meta.time || relTimeNow())}</span>
    </div>
    <div class="stream-text"></div>
  `;
  chatStream.appendChild(div);
  chatStream.scrollTop = chatStream.scrollHeight;
  return div;
}

function setStreamingContent(node, text) {
  if (!node) return;
  const textEl = node.querySelector(".stream-text");
  if (!textEl) return;
  textEl.innerHTML = `${escapeHtml(text || "")}<span class="stream-cursor">|</span>`;
}

function finalizeStreamingContent(node, text) {
  if (!node) return;
  const textEl = node.querySelector(".stream-text");
  if (!textEl) return;
  textEl.textContent = text || "";
  node.classList.remove("streaming");
}

function addFileCard(pathText) {
  if (!chatStream) return;
  const safe = String(pathText || "").trim();
  if (!safe) return;
  const div = document.createElement("div");
  div.className = "msg bot file-card";
  div.innerHTML = `
    <div class="file-card-row">
      <span class="file-icon">📎</span>
      <div>
        <div>Generated Artifact</div>
        <div class="file-meta">${escapeHtml(safe)}</div>
      </div>
    </div>
  `;
  chatStream.appendChild(div);
  chatStream.scrollTop = chatStream.scrollHeight;
}

function addCollectionBinInlineCard() {
  if (!chatStream || !latestOutputMeta) return;
  const taskShort = String(latestOutputMeta.task_id || "").slice(0, 8);
  const summary = (latestOutputMeta.summary || {}).counts || {};
  const div = document.createElement("div");
  div.className = "msg bot file-card";
  div.innerHTML = `
    <div class="msg-head">
      <span class="msg-avatar">V</span>
      <span class="msg-title">Victor (Chief of Staff)</span>
      <span class="msg-time">${relTimeNow()}</span>
    </div>
    <div>Invoice Batch #${escapeHtml(taskShort)} completed. OK:${summary.ok || 0}, Review:${summary.review || 0}, Failed:${summary.failed || 0}.</div>
    <div class="inline-actions">
      <button class="macro-btn" data-inline-action="open-output">📂 Open Output Folder</button>
      <button class="macro-btn" data-inline-action="download-zip">⬇️ Download ZIP</button>
      <button class="macro-btn" data-inline-action="export-audit">📊 Export Audit CSV</button>
    </div>
  `;
  chatStream.appendChild(div);
  chatStream.scrollTop = chatStream.scrollHeight;
}

function showThinking() {
  if (!chatStream || thinkingNode) return;
  const div = document.createElement("div");
  div.className = "msg bot thinking";
  div.innerHTML = `<span class="think-wave"></span><span class="think-wave"></span><span class="think-wave"></span>`;
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
  if (!stageTitle || !stageSubtitle) return;
  const meta = {
    command: ["Command Center", "Primary operations channel"],
    foundry: ["Invoice Foundry", "Document processing and truth grid"],
    cortex: ["Cortex", "Artifact intelligence layer"],
    strategy: ["Strategy", "Goals and execution plans"],
    systems: ["Systems", "Runtime and governance controls"],
  };
  const [title, subtitle] = meta[tab] || meta.command;
  stageTitle.textContent = title;
  stageSubtitle.textContent = subtitle;
}

function switchTab(tab) {
  railButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === tab));
  tabViews.forEach((view) => view.classList.toggle("active", view.id === `tab-${tab}`));
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
  if (!healthBars) return;
  const rows = [["CPU", Number(metrics.cpu || 0)], ["RAM", Number(metrics.ram || 0)], ["DISK", Number(metrics.disk || 0)]];
  healthBars.innerHTML = rows.map(([name, value]) => (
    `<div class="bar-row"><div class="label">${name}: ${value.toFixed(1)}%</div><div class="bar-bg"><div class="bar-fill ${barClass(value)}" style="width:${Math.max(0, Math.min(100, value))}%"></div></div></div>`
  )).join("");
}

async function refreshMetrics() {
  if (!metricsEl) return;
  try {
    const data = await apiGet("/api/metrics");
    const m = data.metrics || {};
    metricsEl.innerHTML = [["CPU", `${m.cpu ?? "-"}%`], ["RAM", `${m.ram ?? "-"}%`], ["DISK", `${m.disk ?? "-"}%`], ["Queue", `${m.queue_depth ?? "-"}`]]
      .map(([k, v]) => `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
    renderSystemHealthBars(m);
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

function renderTruthGrid(rows) {
  if (!truthRows) return;
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
    return `<tr class="${isWarn ? "warn" : ""}" data-row="${idx}">
      <td><span class="status-pill ${statusClass}">${statusText}</span></td>
      <td title="${escapeHtml(r.file_name)}"><span class="cell-clip">${escapeHtml(r.file_name)}</span></td>
      <td title="${escapeHtml(r.receiver || "-")}"><span class="cell-clip">${escapeHtml(r.receiver || "-")}</span></td>
      <td title="${escapeHtml(r.location || "-")}"><span class="cell-clip">${escapeHtml(r.location || "-")}</span></td>
      <td title="${escapeHtml(r.invoice_number || "-")}"><span class="cell-clip">${escapeHtml(r.invoice_number || "-")}</span></td>
      <td title="${escapeHtml(r.delivery_date || "-")}"><span class="cell-clip">${escapeHtml(r.delivery_date || "-")}</span></td>
      <td><span class="status-pill ${statusClass}">${confidencePct}%</span></td>
    </tr>`;
  }).join("");

  truthRows.querySelectorAll("tr[data-row]").forEach((node) => {
    node.addEventListener("click", () => {
      const row = rows[Number(node.dataset.row)];
      if (modalContent && rowModal) {
        modalContent.textContent = JSON.stringify(row.raw, null, 2);
        rowModal.classList.add("show");
      }
    });
  });
}

async function refreshTruthGrid() {
  try {
    const data = await apiGet("/api/invoice/grid");
    latestGridRows = data.rows || [];
    renderTruthGrid(latestGridRows);
  } catch (err) {
    if (truthRows) truthRows.innerHTML = `<tr><td colspan="7">Grid refresh failed: ${err.message}</td></tr>`;
  }
}

async function refreshTasks() {
  if (!tasksList) return;
  try {
    const data = await apiGet("/api/tasks");
    latestTasks = data.tasks || [];
    tasksList.innerHTML = latestTasks.slice(0, 24).map((t) => (
      `<div class="terminal-line ${statusLineClass(t.status)}">[${statusSymbol(t.status)}] ${t.task_type} #${String(t.id).slice(0, 8)} ${t.progress}%</div>`
    )).join("") || `<div class="terminal-line">No tasks yet.</div>`;
    tasksList.scrollTop = tasksList.scrollHeight;
  } catch (err) {
    tasksList.innerHTML = `<div class="terminal-line warn">Task refresh failed: ${err.message}</div>`;
  }
}

async function refreshLogs() {
  if (!logsList) return;
  try {
    const data = await apiGet("/api/logs");
    const rows = data.rows || [];
    logsList.innerHTML = rows.slice(-40).map((r) => {
      const text = `${r.Timestamp || ""} ${r.Agent || ""} ${r.Action || ""} ${r.Status || ""}`.trim();
      const upper = text.toUpperCase();
      let cls = "info";
      if (upper.includes("WARN")) cls = "warn";
      else if (upper.includes("ERROR") || upper.includes("FAILED")) cls = "warn";
      else if (upper.includes("INFO")) cls = "info";
      return `<div class="terminal-line ${cls}">${escapeHtml(text)}</div>`;
    }).join("") || `<div class="terminal-line">No logs yet.</div>`;
    logsList.scrollTop = logsList.scrollHeight;
  } catch (err) {
    logsList.innerHTML = `<div class="terminal-line warn">Log refresh failed: ${err.message}</div>`;
  }
}

function renderTimelines(items) {
  if (!timelineList) return;
  if (!items.length) {
    timelineList.innerHTML = `<div class="terminal-line">No timeline data yet.</div>`;
    return;
  }
  const latest = items[items.length - 1] || {};
  const spans = Array.isArray(latest.timeline) ? latest.timeline : [];
  timelineList.innerHTML = spans.map((span) => {
    const ms = Number(span.duration_ms || 0).toFixed(1);
    return `<div class="timeline-row"><span class="timeline-agent">${escapeHtml(span.agent || "Agent")}</span><span class="timeline-bar"><i style="width:${Math.min(100, Math.max(8, Number(ms) / 60))}%"></i></span><span class="timeline-ms">${ms}ms</span></div>`;
  }).join("") || `<div class="terminal-line">No spans in latest request.</div>`;
}

async function refreshTimelines() {
  if (!timelineList) return;
  try {
    const data = await apiGet("/api/timelines?limit=100");
    lastTimelineItems = data.items || [];
    renderTimelines(lastTimelineItems);
  } catch (err) {
    timelineList.innerHTML = `<div class="terminal-line warn">${escapeHtml(err.message)}</div>`;
  }
}

function renderGoals(goals) {
  if (!strategyGoals) return;
  if (!goals.length) {
    strategyGoals.innerHTML = `<div class="terminal-line">No active goals.</div>`;
    return;
  }
  strategyGoals.innerHTML = goals.map((g) => {
    const pct = Math.max(0, Math.min(100, Number(g.progress || 0)));
    return `
      <div class="goal-card">
        <div class="goal-title">${escapeHtml(g.title || g.id)}</div>
        <div class="goal-meta">P${escapeHtml(g.priority)} · ${escapeHtml(g.status)}</div>
        <div class="goal-progress"><i style="width:${pct}%"></i></div>
      </div>
    `;
  }).join("");
}

function renderPlans(plans, overlay) {
  if (!strategyPlans) return;
  if (!plans.length) {
    strategyPlans.innerHTML = `<div class="terminal-line">No active plans.</div>`;
    return;
  }
  const overlayTimeline = Array.isArray(overlay?.timeline) ? overlay.timeline : [];
  const activeOverlayAgent = overlayTimeline.length ? overlayTimeline[overlayTimeline.length - 1].agent : "";
  strategyPlans.innerHTML = plans.slice(0, 8).map((p) => {
    const steps = Array.isArray(p.steps) ? p.steps : [];
    const stepHtml = steps.slice(0, 12).map((s, idx) => {
      let status = s.status || "pending";
      if (status === "pending" && activeOverlayAgent && s.assigned_agent === activeOverlayAgent) {
        status = "in_progress";
      }
      return `<div class="plan-step ${status}">
        <span class="step-mark">${status === "completed" ? "✓" : status === "in_progress" ? ">" : status === "failed" ? "!" : "•"}</span>
        <span class="step-text">${escapeHtml(s.action || s.description || `Step ${idx + 1}`)}</span>
      </div>`;
    }).join("");
    return `
      <div class="plan-card">
        <div class="goal-title">${escapeHtml(p.goal || p.plan_id)}</div>
        <div class="goal-meta">${escapeHtml(p.status)}</div>
        <div class="plan-steps">${stepHtml || '<div class="terminal-line">No steps yet.</div>'}</div>
      </div>
    `;
  }).join("");
}

async function refreshStrategy() {
  if (!strategyGoals || !strategyPlans) return;
  try {
    const data = await apiGet("/api/strategy/overview");
    renderGoals(data.goals || []);
    renderPlans(data.plans || [], data.live_overlay || {});
  } catch (err) {
    strategyGoals.innerHTML = `<div class="terminal-line warn">${escapeHtml(err.message)}</div>`;
    strategyPlans.innerHTML = "";
  }
}

function groupKey(ts) {
  const now = new Date();
  const d = new Date((Number(ts) || 0) * 1000);
  const ms = now.setHours(0, 0, 0, 0) - d.setHours(0, 0, 0, 0);
  const day = 24 * 60 * 60 * 1000;
  if (ms <= 0) return "Today";
  if (ms <= day) return "Yesterday";
  if (ms <= day * 7) return "Previous 7 Days";
  return "Older";
}

function formatSessionMeta(session) {
  const dt = new Date((Number(session.update_time) || 0) * 1000);
  const t = dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `${t} · ${session.event_count} events`;
}

function renderSessionGroups(sessions) {
  if (!historyGroups) return;
  const groups = { Today: [], Yesterday: [], "Previous 7 Days": [], Older: [] };
  sessions.forEach((s) => groups[groupKey(s.update_time)].push(s));

  historyGroups.innerHTML = Object.entries(groups).filter(([, items]) => items.length).map(([name, items]) => (
    `<div class="history-group">
      <div class="history-label">${name}</div>
      ${items.map((s) => (
        `<button class="session-item ${s.session_id === currentSessionId ? "active" : ""}" data-session="${escapeHtml(s.session_id)}">
          <span class="session-title">${escapeHtml(s.title || s.session_id)}</span>
          <span class="session-meta">${escapeHtml(formatSessionMeta(s))}</span>
        </button>`
      )).join("")}
    </div>`
  )).join("");

  historyGroups.querySelectorAll(".session-item").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const sid = btn.dataset.session;
      if (!sid) return;
      await loadSessionMessages(sid);
      await refreshSessionsList();
    });
  });
}

async function refreshSessionsList() {
  if (!historyGroups) return;
  try {
    const data = await apiGet("/api/sessions");
    renderSessionGroups(data.sessions || []);
  } catch (err) {
    historyGroups.innerHTML = `<div class="terminal-line warn">Sessions load failed: ${escapeHtml(err.message)}</div>`;
  }
}

function clearChatStream() {
  if (!chatStream) return;
  chatStream.innerHTML = "";
}

async function loadSessionMessages(sessionId) {
  try {
    const data = await apiGet(`/api/sessions/${encodeURIComponent(sessionId)}/messages`);
    currentSessionId = sessionId;
    clearChatStream();
    (data.messages || []).forEach((m) => {
      addMessage(m.role === "user" ? "user" : "bot", m.text || "");
      (m.artifacts || []).forEach((p) => addFileCard(p));
    });
    if (!(data.messages || []).length) {
      addMessage("bot", "Session is empty. Send a command to begin.");
    }
  } catch (err) {
    addMessage("bot", `Session load error: ${err.message}`);
  }
}

async function checkGateway(message) {
  try {
    const gate = await apiPost("/api/gateway/check", { message });
    const allowed = Boolean(gate.allowed);
    setGateway(allowed);
    return allowed;
  } catch (_err) {
    setGateway(true);
    return true;
  }
}

function getSleepingAgents() {
  return Object.entries(resourceState)
    .filter(([, active]) => active === false)
    .map(([name]) => name);
}

function parseSseEventBlock(block) {
  const out = { event: "message", data: "" };
  const lines = block.split("\n");
  for (const line of lines) {
    if (line.startsWith("event:")) out.event = line.slice(6).trim();
    if (line.startsWith("data:")) out.data += line.slice(5).trim();
  }
  try {
    return { event: out.event, data: out.data ? JSON.parse(out.data) : {} };
  } catch (_err) {
    return { event: out.event, data: {} };
  }
}

async function streamChatRequest(message) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      user_id: "desktop_ceejay",
      session_id: currentSessionId,
      sleeping_agents: getSleepingAgents(),
    }),
  });
  if (!response.ok || !response.body) {
    throw new Error(`stream failed: ${response.status}`);
  }

  let streamText = "";
  let botNode = addStreamingBotMessage({ agent: "Chief of Staff" });
  let activeAgent = "Chief_of_Staff";
  let delegated = [];
  const timeline = [];

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      if (!part.trim()) continue;
      const evt = parseSseEventBlock(part);
      const payload = evt.data || {};
      if (evt.event === "meta") {
        if (payload.session_id) currentSessionId = payload.session_id;
        continue;
      }
      if (evt.event === "token") {
        const chunk = String(payload.text_chunk || "");
        if (chunk) {
          streamText += chunk;
          setStreamingContent(botNode, streamText);
        }
        if (payload.agent) {
          activeAgent = payload.agent;
          if (agentActivityText) agentActivityText.textContent = describeAgentActivity(activeAgent);
          highlightChain(activeAgent, delegated);
        }
        continue;
      }
      if (evt.event === "agent") {
        if (payload.phase === "start" && payload.agent) {
          activeAgent = payload.agent;
          if (!delegated.includes(activeAgent) && activeAgent !== "Chief_of_Staff") delegated.push(activeAgent);
          if (agentActivityText) agentActivityText.textContent = describeAgentActivity(activeAgent);
          highlightChain(activeAgent, delegated);
        }
        continue;
      }
      if (evt.event === "artifact") {
        if (payload.path) addFileCard(payload.path);
        continue;
      }
      if (evt.event === "done") {
        activeAgent = payload.active_agent || activeAgent;
        delegated = payload.delegated_agents || delegated;
        const finalText = String(payload.final_text || streamText || "(no response)");
        finalizeStreamingContent(botNode, finalText);
        (payload.artifacts || []).forEach((p) => addFileCard(p));
        if (Array.isArray(payload.timeline)) {
          timeline.length = 0;
          payload.timeline.forEach((item) => timeline.push(item));
        }
        return { activeAgent, delegated, timeline };
      }
      if (evt.event === "error") {
        throw new Error(payload.message || "stream error");
      }
    }
  }
  finalizeStreamingContent(botNode, streamText || "(no response)");
  return { activeAgent, delegated, timeline };
}

async function refreshCollectionBin() {
  if (!collectionBin || !collectionMeta) return;
  try {
    const res = await apiGet("/api/invoice/latest_output");
    if (!res.available) {
      collectionBin.classList.add("hidden");
      latestOutputMeta = null;
      return;
    }
    latestOutputMeta = res;
    const counts = (res.summary || {}).counts || {};
    collectionMeta.textContent = `Job ${String(res.task_id || "").slice(0, 8)} · OK:${counts.ok || 0} Review:${counts.review || 0} Failed:${counts.failed || 0}`;
    collectionBin.classList.remove("hidden");
    const taskId = String(res.task_id || "");
    if (taskId && taskId !== announcedCollectionTaskId) {
      announcedCollectionTaskId = taskId;
      addCollectionBinInlineCard();
    }
  } catch (_err) {
    collectionBin.classList.add("hidden");
  }
}

async function renderMissionBoard() {
  if (!missionBoard) return;
  try {
    const data = await apiGet("/api/mission_board");
    missionItems = data.items || [];
    missionBoard.innerHTML = missionItems.map((item, idx) => (
      `<label class="mission-item ${item.done ? "done" : ""}">
        <input type="checkbox" data-mission="${idx}" ${item.done ? "checked" : ""}/>
        <span class="mission-title">${escapeHtml(item.title)}</span>
      </label>`
    )).join("");
    missionBoard.querySelectorAll("input[data-mission]").forEach((cb) => {
      cb.addEventListener("change", async () => {
        const i = Number(cb.dataset.mission);
        if (!Number.isFinite(i) || !missionItems[i]) return;
        missionItems[i].done = cb.checked;
        await apiPost("/api/mission_board", { items: missionItems });
        renderMissionBoard();
      });
    });
  } catch (err) {
    missionBoard.innerHTML = `<div class="terminal-line warn">${escapeHtml(err.message)}</div>`;
  }
}

async function renderResourceGovernor() {
  if (!resourceRows) return;
  try {
    const data = await apiGet("/api/resource_governor");
    resourceState = data.state || {};
    const agents = Object.keys(resourceState);
    const rowsHtml = agents.map((agent) => (
      `<div class="resource-row">
        <span>${escapeHtml(agent)}</span>
        <label class="resource-toggle">
          <input type="checkbox" data-agent="${escapeHtml(agent)}" ${resourceState[agent] ? "checked" : ""}/>
          <span>${resourceState[agent] ? "ACTIVE" : "SLEEP"}</span>
        </label>
      </div>`
    )).join("");
    resourceRows.innerHTML = rowsHtml;
    if (leftResourceRows) {
      leftResourceRows.innerHTML = agents.map((agent) => (
        `<label class="left-resource-row">
          <span>${escapeHtml(agent.replace("_Agent", "").replace("_", " "))}</span>
          <input type="checkbox" data-agent-left="${escapeHtml(agent)}" ${resourceState[agent] ? "checked" : ""}/>
        </label>`
      )).join("");
    }
    resourceRows.querySelectorAll("input[data-agent]").forEach((cb) => {
      cb.addEventListener("change", async () => {
        const name = cb.dataset.agent;
        if (!name) return;
        resourceState[name] = cb.checked;
        await apiPost("/api/resource_governor", { state: resourceState });
        renderResourceGovernor();
        applyResourceDimming();
      });
    });
    leftResourceRows?.querySelectorAll("input[data-agent-left]").forEach((cb) => {
      cb.addEventListener("change", async () => {
        const name = cb.dataset.agentLeft;
        if (!name) return;
        resourceState[name] = cb.checked;
        await apiPost("/api/resource_governor", { state: resourceState });
        renderResourceGovernor();
        applyResourceDimming();
      });
    });
    applyResourceDimming();
  } catch (err) {
    resourceRows.innerHTML = `<div class="terminal-line warn">${escapeHtml(err.message)}</div>`;
  }
}

function artifactPreview(item) {
  if (item.kind === "image") {
    const src = `/api/artifacts/file?path=${encodeURIComponent(item.path)}`;
    return `<img src="${src}" alt="${escapeHtml(item.name)}"/>`;
  }
  if (item.kind === "code") {
    return `<pre class="artifact-meta">${escapeHtml(item.snippet || "")}</pre>`;
  }
  return `<div class="artifact-meta">📄 ${escapeHtml(item.ext || ".file")}</div>`;
}

async function refreshArtifacts() {
  if (!artifactGrid) return;
  const kindParam = currentArtifactFilter === "all" ? "all" : currentArtifactFilter;
  try {
    const data = await apiGet(`/api/artifacts?kind=${encodeURIComponent(kindParam)}`);
    const items = data.items || [];
    if (leftArtifactRail) {
      const thumbs = items.filter((x) => x.kind === "image").slice(0, 6);
      leftArtifactRail.innerHTML = thumbs.map((item) => (
        `<div class="left-thumb"><img src="/api/artifacts/file?path=${encodeURIComponent(item.path)}" alt="${escapeHtml(item.name)}"/></div>`
      )).join("") || `<div class="artifact-meta">No previews</div>`;
    }
    artifactGrid.innerHTML = items.map((item) => (
      `<div class="artifact-card">
        <div class="artifact-preview">${artifactPreview(item)}</div>
        <div class="artifact-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div>
        <div class="artifact-meta">${escapeHtml(item.kind)} · ${Math.round((Number(item.size) || 0) / 1024)} KB</div>
      </div>`
    )).join("") || `<div class="terminal-line">No artifacts found.</div>`;
  } catch (err) {
    artifactGrid.innerHTML = `<div class="terminal-line warn">${escapeHtml(err.message)}</div>`;
  }
}

async function refreshDesktopSettings() {
  try {
    const data = await apiGet("/api/settings/desktop");
    desktopSettings = data.settings || desktopSettings;
    if (toggleClipboardMonitor) toggleClipboardMonitor.checked = !!desktopSettings.monitor_clipboard;
    if (toggleTimelineMode) toggleTimelineMode.checked = desktopSettings.timeline_mode !== false;
  } catch (_err) {
    // no-op
  }
}

async function saveDesktopSettings(partial) {
  desktopSettings = { ...desktopSettings, ...partial };
  try {
    const data = await apiPost("/api/settings/desktop", { settings: desktopSettings });
    desktopSettings = data.settings || desktopSettings;
  } catch (err) {
    addMessage("bot", `Settings save failed: ${err.message}`, { agent: "Chief of Staff" });
  }
}

function renderWorkflowBuilder(definitions = [], actions = []) {
  if (!workflowBuilder) return;
  const actionRows = actions.slice(0, 18).map((a) => (
    `<option value="${escapeHtml(a.id)}">${escapeHtml(a.label)}</option>`
  )).join("");
  const defRows = definitions.slice(0, 20).map((d) => (
    `<option value="${escapeHtml(d.name)}">${escapeHtml(d.name)}</option>`
  )).join("");
  workflowBuilder.innerHTML = `
    <div class="workflow-toolbar">
      <select id="workflowDefSelect"><option value="">Load workflow...</option>${defRows}</select>
      <input id="workflowNameInput" type="text" value="${escapeHtml(workflowDraft.name || "workflow_desktop_new")}" />
      <button id="workflowLoadBtn" class="macro-btn">Load</button>
      <button id="workflowSaveBtn" class="macro-btn">Save</button>
    </div>
    <div class="workflow-toolbar">
      <select id="workflowActionSelect"><option value="">Add node...</option>${actionRows}</select>
      <button id="workflowAddNodeBtn" class="macro-btn">Add Node</button>
      <button id="workflowValidateBtn" class="macro-btn">Validate</button>
      <button id="workflowDeleteBtn" class="macro-btn">Archive</button>
    </div>
    <div class="workflow-columns">
      <textarea id="workflowNodesEditor" class="workflow-editor" spellcheck="false">${escapeHtml(JSON.stringify(workflowDraft.nodes || [], null, 2))}</textarea>
      <textarea id="workflowEdgesEditor" class="workflow-editor" spellcheck="false">${escapeHtml(JSON.stringify(workflowDraft.edges || [], null, 2))}</textarea>
    </div>
    <div id="workflowStatus" class="inline-status"></div>
  `;

  const statusEl = document.getElementById("workflowStatus");
  const nameInput = document.getElementById("workflowNameInput");
  const nodesEditor = document.getElementById("workflowNodesEditor");
  const edgesEditor = document.getElementById("workflowEdgesEditor");
  const actionSelect = document.getElementById("workflowActionSelect");
  const defSelect = document.getElementById("workflowDefSelect");

  const readDraftFromEditors = () => {
    workflowDraft.name = (nameInput?.value || "workflow_desktop_new").trim() || "workflow_desktop_new";
    workflowDraft.nodes = JSON.parse(nodesEditor?.value || "[]");
    workflowDraft.edges = JSON.parse(edgesEditor?.value || "[]");
  };

  document.getElementById("workflowAddNodeBtn")?.addEventListener("click", () => {
    try {
      readDraftFromEditors();
      const id = `node_${Date.now()}`;
      const action = actionSelect?.value || "action";
      workflowDraft.nodes.push({ id, type: "action", action });
      nodesEditor.value = JSON.stringify(workflowDraft.nodes, null, 2);
      statusEl.textContent = `Node ${id} added.`;
    } catch (err) {
      statusEl.textContent = `Parse error: ${err.message}`;
    }
  });

  document.getElementById("workflowValidateBtn")?.addEventListener("click", async () => {
    try {
      readDraftFromEditors();
      const res = await apiPost("/api/workflow/validate", workflowDraft);
      statusEl.textContent = res.ok ? "Workflow payload valid." : `Invalid: ${(res.errors || []).join(", ")}`;
    } catch (err) {
      statusEl.textContent = `Validation failed: ${err.message}`;
    }
  });

  document.getElementById("workflowSaveBtn")?.addEventListener("click", async () => {
    try {
      readDraftFromEditors();
      const res = await apiPost("/api/workflow/save", workflowDraft);
      statusEl.textContent = `Saved: ${res.name}`;
      await refreshWorkflowBuilder();
    } catch (err) {
      statusEl.textContent = `Save failed: ${err.message}`;
    }
  });

  document.getElementById("workflowLoadBtn")?.addEventListener("click", async () => {
    const selected = defSelect?.value || "";
    if (!selected) return;
    try {
      const res = await apiGet(`/api/workflow/definition/${encodeURIComponent(selected)}`);
      const wf = res.workflow || {};
      workflowDraft = {
        name: selected.replace(/\.json$/i, ""),
        nodes: wf.nodes || [],
        edges: wf.edges || [],
      };
      renderWorkflowBuilder(definitions, actions);
    } catch (err) {
      statusEl.textContent = `Load failed: ${err.message}`;
    }
  });

  document.getElementById("workflowDeleteBtn")?.addEventListener("click", async () => {
    const name = (nameInput?.value || "").trim();
    if (!name) return;
    try {
      const res = await apiPost("/api/workflow/delete", { name });
      statusEl.textContent = `Archived: ${res.archived_as}`;
      workflowDraft = { name: "workflow_desktop_new", nodes: [], edges: [] };
      await refreshWorkflowBuilder();
    } catch (err) {
      statusEl.textContent = `Archive failed: ${err.message}`;
    }
  });
}

async function refreshWorkflowBuilder() {
  if (!workflowBuilder) return;
  try {
    const [defs, actions] = await Promise.all([
      apiGet("/api/workflow/definitions"),
      apiGet("/api/workflow/actions"),
    ]);
    renderWorkflowBuilder(defs.definitions || [], actions.actions || []);
  } catch (err) {
    workflowBuilder.innerHTML = `<div class="terminal-line warn">${escapeHtml(err.message)}</div>`;
  }
}

if (chatForm && chatInput) {
  chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const message = chatInput.value.trim();
    if (!message) return;
    await checkGateway(message);
    addMessage("user", message);
    chatInput.value = "";
    if (agentActivityText) agentActivityText.textContent = "Chief_of_Staff is routing your request...";
    showThinking();
    try {
      const data = await streamChatRequest(message);
      hideThinking();
      const active = data.activeAgent || "Chief_of_Staff";
      const delegated = data.delegated || [];
      if (agentActivityText) agentActivityText.textContent = describeAgentActivity(active);
      highlightChain(active, delegated);
      await refreshSessionsList();
      await refreshTimelines();
      await refreshStrategy();
    } catch (err) {
      hideThinking();
      addMessage("bot", `Error: ${err.message}`);
    }
  });
}

if (invoiceForm && invoicePath && invoiceStatus) {
  invoiceForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const path = invoicePath.value.trim();
    if (!path) return;
    invoiceStatus.textContent = "Queueing batch...";
    try {
      const data = await apiPost("/api/invoice/enqueue", { input_path: path, user_id: "desktop_ceejay" });
      invoiceStatus.textContent = `Queued task: ${data.task_id}`;
      refreshTasks();
      refreshCollectionBin();
    } catch (err) {
      invoiceStatus.textContent = `Queue failed: ${err.message}`;
    }
  });
}

function setupDropZone() {
  if (!dropZone || !ingestFile) return;
  ["dragenter", "dragover"].forEach((evt) => {
    dropZone.addEventListener(evt, (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
  });
  ["dragleave", "drop"].forEach((evt) => {
    dropZone.addEventListener(evt, (e) => { e.preventDefault(); dropZone.classList.remove("drag-over"); });
  });
  dropZone.addEventListener("drop", (e) => {
    const files = e.dataTransfer?.files;
    if (!files || !files.length) return;
    uploadAndQueueFile(files[0]);
  });
  ingestFile.addEventListener("change", () => {
    const f = ingestFile.files?.[0];
    if (!f) return;
    uploadAndQueueFile(f);
  });
}

async function uploadAndQueueFile(file) {
  if (!invoiceStatus) return;
  invoiceStatus.textContent = `Uploading ${file.name}...`;
  const fd = new FormData();
  fd.append("file", file);
  fd.append("user_id", "desktop_ceejay");
  try {
    const res = await fetch("/api/invoice/upload", { method: "POST", body: fd });
    const json = await res.json();
    if (!res.ok || !json.ok) throw new Error(json.error || "upload failed");
    invoiceStatus.textContent = `Queued ${json.filename} as task ${json.task_id}`;
    await refreshTasks();
    await refreshTruthGrid();
    await refreshCollectionBin();
  } catch (err) {
    invoiceStatus.textContent = `Upload failed: ${err.message}`;
  }
}

if (toggleWatchtower && watchtower) {
  toggleWatchtower.addEventListener("click", () => {
    watchtower.classList.toggle("collapsed");
    toggleWatchtower.textContent = watchtower.classList.contains("collapsed") ? "❮" : "❯";
  });
}

if (toggleAuditor && auditorConsole) {
  toggleAuditor.addEventListener("click", () => {
    auditorConsole.classList.toggle("collapsed");
    toggleAuditor.textContent = auditorConsole.classList.contains("collapsed") ? "⌃" : "⌄";
  });
  toggleAuditor.textContent = auditorConsole.classList.contains("collapsed") ? "⌃" : "⌄";
}

if (refreshSessions) refreshSessions.addEventListener("click", refreshSessionsList);
if (newChat) {
  newChat.addEventListener("click", async () => {
    currentSessionId = newSessionId();
    clearChatStream();
    addMessage("bot", "New mission thread initialized. Send your first command.", { agent: "Chief of Staff" });
    highlightChain("Chief_of_Staff", []);
    await refreshSessionsList();
  });
}

if (closeModal && rowModal) {
  closeModal.addEventListener("click", () => rowModal.classList.remove("show"));
  rowModal.addEventListener("click", (e) => { if (e.target === rowModal) rowModal.classList.remove("show"); });
}

railButtons.forEach((btn) => btn.addEventListener("click", () => switchTab(btn.dataset.tab)));

if (macroSitrep && chatInput) macroSitrep.addEventListener("click", () => { chatInput.value = "Give me a status report"; chatInput.focus(); });
if (macroReboot) macroReboot.addEventListener("click", async () => {
  try {
    const res = await apiPost("/api/system/reboot", {});
    addMessage("bot", res.message || "Reboot command acknowledged.", { agent: "Chief of Staff" });
  } catch (err) {
    addMessage("bot", `Reboot error: ${err.message}`, { agent: "Chief of Staff" });
  }
});
if (macroClearLogs) macroClearLogs.addEventListener("click", () => {
  if (logsList) logsList.innerHTML = `<div class="terminal-line">Logs cleared (visual buffer).</div>`;
  if (tasksList) tasksList.innerHTML = `<div class="terminal-line">Tasks buffer cleared.</div>`;
});
if (macroOpenVault) macroOpenVault.addEventListener("click", () => switchTab("cortex"));

if (toggleClipboardMonitor) {
  toggleClipboardMonitor.addEventListener("change", async () => {
    await saveDesktopSettings({ monitor_clipboard: !!toggleClipboardMonitor.checked });
  });
}
if (toggleTimelineMode) {
  toggleTimelineMode.addEventListener("change", async () => {
    await saveDesktopSettings({ timeline_mode: !!toggleTimelineMode.checked });
    if (timelineList) timelineList.style.display = toggleTimelineMode.checked ? "" : "none";
  });
}

if (openOutputFolderBtn) openOutputFolderBtn.addEventListener("click", async () => {
  if (!latestOutputMeta?.output_dir) return;
  try {
    await apiPost("/api/invoice/open_output", { path: latestOutputMeta.output_dir });
  } catch (err) {
    addMessage("bot", `Open folder failed: ${err.message}`, { agent: "Chief of Staff" });
  }
});
if (downloadZipBtn) downloadZipBtn.addEventListener("click", () => {
  window.location.href = "/api/invoice/download_zip";
});
if (exportAuditBtn) exportAuditBtn.addEventListener("click", () => {
  window.location.href = "/api/invoice/export_audit_csv";
});

if (chatStream) {
  chatStream.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-inline-action]");
    if (!btn) return;
    const action = btn.dataset.inlineAction;
    if (action === "open-output") {
      if (latestOutputMeta?.output_dir) {
        await apiPost("/api/invoice/open_output", { path: latestOutputMeta.output_dir });
      }
      return;
    }
    if (action === "download-zip") {
      window.location.href = "/api/invoice/download_zip";
      return;
    }
    if (action === "export-audit") {
      window.location.href = "/api/invoice/export_audit_csv";
    }
  });
}

galleryFilterButtons.forEach((btn) => {
  btn.addEventListener("click", async () => {
    galleryFilterButtons.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentArtifactFilter = btn.dataset.kind || "all";
    await refreshArtifacts();
  });
});

window.focusChatInput = function focusChatInput() {
  if (!chatInput) return;
  switchTab("command");
  chatInput.focus();
};

async function boot() {
  setupDropZone();
  switchTab("command");
  setGateway(true);
  highlightChain("Chief_of_Staff", []);
  await refreshMetrics();
  await refreshTasks();
  await refreshTruthGrid();
  await refreshLogs();
  await refreshSessionsList();
  await renderMissionBoard();
  await renderResourceGovernor();
  await refreshCollectionBin();
  await refreshArtifacts();
  await refreshDesktopSettings();
  await refreshTimelines();
  await refreshStrategy();
  await refreshWorkflowBuilder();
  await loadSessionMessages(currentSessionId);
  setInterval(refreshMetrics, 5000);
  setInterval(refreshTasks, 3500);
  setInterval(refreshTruthGrid, 3500);
  setInterval(refreshLogs, 7000);
  setInterval(refreshTimelines, 7000);
  setInterval(refreshStrategy, 12000);
  setInterval(refreshSessionsList, 12000);
  setInterval(refreshCollectionBin, 5000);
}

boot();
