const API = "/api";
let projects = [];
let currentProjectId = null;
let currentProjectSlug = null;
let allTasks = [];
let tasks = [];
let columns = [];
let dirty = false;
let currentTaskId = null;
let descMode = "edit";
let fullscreen = false;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const t = await res.text();
    let msg = t || res.statusText;
    try {
      const j = JSON.parse(t);
      if (j && j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch (e) {}
    throw new Error(msg);
  }
  return res.json();
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

/* ---------- Projects ---------- */
async function loadProjects() {
  projects = await api("/projects");
  if (!currentProjectId) {
    const savedSlug = localStorage.getItem("activeProjectSlug");
    let p = projects.find(x => x.slug === savedSlug);
    if (!p) p = projects.find(x => x.slug === "default") || projects[0];
    if (p) { currentProjectId = p.id; currentProjectSlug = p.slug; }
  } else {
    if (!projects.find(x => x.id === currentProjectId)) {
      const p = projects.find(x => x.slug === "default") || projects[0];
      if (p) { currentProjectId = p.id; currentProjectSlug = p.slug; }
    }
  }
  renderProjectSelect();
}

function renderProjectSelect() {
  const sel = $("#projectSelect");
  sel.innerHTML = "";
  projects.forEach(p => {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.slug === "default" ? p.name : `${p.name} (${p.slug})`;
    opt.title = `MCP: ?project=${p.slug}`;
    if (p.id === currentProjectId) opt.selected = true;
    sel.appendChild(opt);
  });
}

async function switchProject(id) {
  const p = projects.find(x => x.id === id);
  if (!p) return;
  currentProjectId = p.id;
  currentProjectSlug = p.slug;
  localStorage.setItem("activeProjectSlug", p.slug);
  renderProjectSelect();
  await loadColumns();
  await loadTasks();
  await renderFilters();
}

async function createProject() {
  const name = prompt("Название проекта:");
  if (!name) return;
  const p = await api("/projects", { method: "POST", body: JSON.stringify({ name }) });
  projects.push(p);
  await switchProject(p.id);
}

async function renameProject() {
  const p = projects.find(x => x.id === currentProjectId);
  if (!p) return;
  const name = prompt("Новое название:", p.name);
  if (!name || name === p.name) return;
  const updated = await api(`/projects/${p.id}`, { method: "PUT", body: JSON.stringify({ name }) });
  Object.assign(p, updated);
  renderProjectSelect();
}

async function deleteProject() {
  if (projects.length <= 1) { alert("Нельзя удалить последний проект"); return; }
  if (!confirm("Удалить проект и все его задачи/колонки?")) return;
  await api(`/projects/${currentProjectId}`, { method: "DELETE" });
  projects = projects.filter(x => x.id !== currentProjectId);
  const p = projects.find(x => x.slug === "default") || projects[0];
  currentProjectId = p.id; currentProjectSlug = p.slug;
  localStorage.setItem("activeProjectSlug", p.slug);
  await loadColumns();
  await loadTasks();
  await renderFilters();
  renderProjectSelect();
}

/* ---------- Columns ---------- */
async function loadColumns() {
  columns = await api(`/columns?project_id=${currentProjectId}`);
}

function columnHasTasks(colId) {
  return allTasks.some(t => t.column_id === colId);
}

async function openColumnModal(colId) {
  $("#columnId").value = colId ?? "";
  if (colId) {
    const c = columns.find(x => x.id === colId);
    $("#columnModalTitle").textContent = "Редактировать колонку";
    $("#columnName").value = c ? c.name : "";
    $("#columnColor").value = c ? c.color : "#4a90d9";
    $("#btnDeleteColumn").style.display = "inline-block";
  } else {
    $("#columnModalTitle").textContent = "Новая колонка";
    $("#columnName").value = "";
    $("#columnColor").value = "#4a90d9";
    $("#btnDeleteColumn").style.display = "none";
  }
  $("#columnModal").classList.add("active");
}

function closeColumnModal() {
  $("#columnModal").classList.remove("active");
}

async function saveColumn() {
  const id = $("#columnId").value;
  const name = $("#columnName").value.trim();
  if (!name) { alert("Укажите название"); return; }
  const color = $("#columnColor").value;
  try {
    if (id) {
      await api(`/columns/${id}`, { method: "PUT", body: JSON.stringify({ name, color }) });
    } else {
      await api("/columns", { method: "POST", body: JSON.stringify({ name, color, project_id: currentProjectId }) });
    }
    closeColumnModal();
    await loadColumns();
    await loadTasks();
    renderBoard();
  } catch (err) { alert(err.message); }
}

async function deleteColumn() {
  const id = $("#columnId").value;
  if (!id) return;
  if (!confirm("Удалить колонку?")) return;
  try {
    await api(`/columns/${id}`, { method: "DELETE" });
    closeColumnModal();
    await loadColumns();
    await loadTasks();
    renderBoard();
  } catch (err) { alert(err.message); }
}

/* ---------- Tasks ---------- */
async function loadTasks() {
  allTasks = await api(`/tasks?project_id=${currentProjectId}`);
  applyFilters();
}

function applyFilters() {
  const q = ($("#searchInput").value || "").toLowerCase().trim();
  const cats = getMultiSelected("catFilter");
  const prios = getMultiSelected("prioFilter");
  tasks = allTasks.filter(t => {
    if (q) {
      const hay = ((t.title||"") + " " + (t.description||"") + " " + (t.affected_files||"")).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (cats.length && !cats.includes(t.category)) return false;
    if (prios.length && !prios.includes(t.priority)) return false;
    return true;
  });
  renderBoard();
}

function renderBoard() {
  const board = $("#board");
  board.innerHTML = "";
  columns.forEach(col => {
    const colEl = document.createElement("div");
    colEl.className = "column";
    colEl.dataset.columnId = col.id;
    const count = tasks.filter(t => t.column_id === col.id).length;
    colEl.innerHTML = `
      <div class="column-header">
        <div class="left">
          <span class="column-color" style="background:${col.color}"></span>
          <span class="column-title">${escapeHtml(col.name)}</span>
          <span class="counter-badge">${count}</span>
        </div>
        <div class="column-menu">
          <button class="menu-btn" type="button">•••</button>
          <div class="menu-panel">
            <button data-action="rename">Переименовать</button>
            <button data-action="color">Цвет</button>
            <button data-action="delete" class="danger">Удалить</button>
          </div>
        </div>
      </div>
      <div class="column-body" data-column-id="${col.id}"></div>
    `;
    const body = colEl.querySelector(".column-body");
    body.addEventListener("dragover", (e) => { e.preventDefault(); body.classList.add("drag-over"); });
    body.addEventListener("dragleave", () => body.classList.remove("drag-over"));
    body.addEventListener("drop", async (e) => {
      e.preventDefault();
      body.classList.remove("drag-over");
      if (draggedTaskId == null) return;
      const targetCol = parseInt(body.dataset.columnId);
      try {
        await api(`/tasks/${draggedTaskId}/move`, { method: "POST", body: JSON.stringify({ column_id: targetCol }) });
        await loadTasks();
      } catch (err) { alert(err.message); }
      draggedTaskId = null;
    });

    // menu
    const menuBtn = colEl.querySelector(".menu-btn");
    const menuPanel = colEl.querySelector(".menu-panel");
    menuBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      // close others
      $$(".menu-panel.open").forEach(p => { if (p !== menuPanel) p.classList.remove("open"); });
      menuPanel.classList.toggle("open");
    });
    menuPanel.querySelector('[data-action="rename"]').addEventListener("click", () => {
      menuPanel.classList.remove("open");
      openColumnModal(col.id);
    });
    menuPanel.querySelector('[data-action="color"]').addEventListener("click", () => {
      menuPanel.classList.remove("open");
      openColumnModal(col.id);
    });
    menuPanel.querySelector('[data-action="delete"]').addEventListener("click", async () => {
      menuPanel.classList.remove("open");
      if (columnHasTasks(col.id)) { alert("В колонке есть задачи — удаление запрещено"); return; }
      if (!confirm("Удалить колонку?")) return;
      try {
        await api(`/columns/${col.id}`, { method: "DELETE" });
        await loadColumns(); await loadTasks(); renderBoard();
      } catch (err) { alert(err.message); }
    });

    tasks.filter(t => t.column_id === col.id).forEach(t => {
      const card = document.createElement("div");
      card.className = "card";
      card.draggable = true;
      card.dataset.taskId = t.id;
      card.innerHTML = `
        <div class="card-title">${escapeHtml(t.title)}</div>
        <div class="card-meta">
          <span class="badge priority-${t.priority}">${t.priority}</span>
          <span class="badge">${escapeHtml(t.category)}</span>
        </div>
        <div class="card-comments">💬 ${t.comment_count || 0}</div>
      `;
      card.addEventListener("dragstart", (e) => { draggedTaskId = t.id; card.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
      card.addEventListener("dragend", () => { draggedTaskId = null; card.classList.remove("dragging"); });
      card.addEventListener("click", () => openTaskModal(t.id));
      body.appendChild(card);
    });
    board.appendChild(colEl);
  });

  // add column card
  const addCol = document.createElement("div");
  addCol.className = "column add-column";
  addCol.innerHTML = `<button class="add-stage-btn" type="button">+ Добавить стадию</button>`;
  addCol.querySelector("button").addEventListener("click", () => openColumnModal(null));
  board.appendChild(addCol);

  // close menus on outside click handled globally
}

let draggedTaskId = null;

function closeMenus() {
  $$(".menu-panel.open").forEach(p => p.classList.remove("open"));
  $$(".ms.open").forEach(m => m.classList.remove("open"));
}

/* ---------- Task modal ---------- */
async function openTaskModal(taskId) {
  currentTaskId = taskId;
  dirty = false;
  $("#taskId").value = taskId ?? "";
  $("#commentsSection").style.display = "none";
  // Existing tasks open in read/preview mode; new tasks need the editor immediately.
  descMode = taskId ? "preview" : "edit";
  $("#taskDesc").style.display = taskId ? "none" : "";
  $("#taskDescPreview").style.display = taskId ? "" : "none";
  $("#btnEditDesc").classList.toggle("active", !taskId);
  $("#btnPreviewDesc").classList.toggle("active", Boolean(taskId));

  if (taskId) {
    const t = await api(`/tasks/${taskId}`);
    $("#taskTitle").value = t.title;
    $("#taskDesc").value = t.description || "";
    $("#taskPriority").value = t.priority;
    $("#taskCategory").value = t.category;
    $("#taskAgent").value = t.agent_id || "";
    $("#taskAffectedFiles").value = t.affected_files || "";
    $("#modalTitle").textContent = `Задача #${t.id}`;
    $("#commentsSection").style.display = "block";
    await loadComments(taskId);
  } else {
    $("#taskTitle").value = "";
    $("#taskDesc").value = "";
    $("#taskPriority").value = "Medium";
    $("#taskCategory").value = "Feature";
    $("#taskAgent").value = "";
    $("#taskAffectedFiles").value = "";
    $("#modalTitle").textContent = "Новая задача";
    $("#commentsList").innerHTML = "";
  }

  if (taskId) renderPreview();

  // status list
  const sel = $("#taskColumn");
  // we use status buttons
  const statusList = $("#statusList");
  statusList.innerHTML = "";
  columns.forEach(c => {
    const btn = document.createElement("button");
    btn.className = "status-btn";
    btn.textContent = c.name;
    btn.dataset.columnId = c.id;
    btn.addEventListener("click", () => {
      statusList.querySelectorAll(".status-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      // store selected
      statusList.dataset.selected = c.id;
      dirty = true;
    });
    statusList.appendChild(btn);
  });
  // set active
  if (taskId) {
    const t = tasks.find(x => x.id === taskId);
    if (t) {
      const btn = statusList.querySelector(`[data-column-id="${t.column_id}"]`);
      if (btn) { btn.classList.add("active"); statusList.dataset.selected = t.column_id; }
    }
  } else {
    if (columns[0]) {
      const btn = statusList.querySelector(`[data-column-id="${columns[0].id}"]`);
      if (btn) { btn.classList.add("active"); statusList.dataset.selected = columns[0].id; }
    }
  }

  $("#taskModal").classList.add("active");
}

function closeModal() {
  if (dirty && !confirm("Есть несохранённые изменения. Закрыть?")) return;
  $("#taskModal").classList.remove("active");
  if (fullscreen) toggleFullscreen(false);
  currentTaskId = null;
  dirty = false;
}

async function loadComments(taskId) {
  const list = $("#commentsList");
  list.innerHTML = "Загрузка...";
  const comments = await api(`/tasks/${taskId}/comments`);
  list.innerHTML = "";
  comments.forEach(c => {
    const div = document.createElement("div");
    div.className = "comment-item";
    div.innerHTML = `<div class="comment-author">${escapeHtml(c.author)}</div><div class="comment-text">${escapeHtml(c.text)}</div>`;
    list.appendChild(div);
  });
  if (!comments.length) list.innerHTML = "<div class='muted'>Нет комментариев</div>";
}

function toggleFullscreen(force) {
  fullscreen = (force !== undefined) ? force : !fullscreen;
  $("#taskModalDialog").classList.toggle("fullscreen", fullscreen);
}

function trackDirty() {
  const fields = ["#taskTitle","#taskDesc","#taskPriority","#taskCategory","#taskAgent","#taskAffectedFiles"];
  fields.forEach(sel => {
    const el = $(sel);
    el.addEventListener("input", () => { dirty = true; }, { once: false });
    el.addEventListener("change", () => { dirty = true; });
  });
}

function renderMarkdown(text) {
  const lines = (text || "").split("\n");
  let html = "";
  let inCode = false;
  let codeBuf = [];
  lines.forEach((line, idx) => {
    if (line.trim().startsWith("```")) {
      if (inCode) { html += `<pre><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`; codeBuf = []; inCode = false; }
      else inCode = true;
      return;
    }
    if (inCode) { codeBuf.push(line); return; }
    const cb = line.match(/^[-*]\s*\[([ xX])\]\s*(.*)$/);
    if (cb) {
      const checked = cb[1].toLowerCase() === "x";
      const content = inlineMd(cb[2]);
      html += `<label class="md-checkbox"><input type="checkbox" data-line="${idx}" ${checked ? "checked" : ""}> <span>${content}</span></label>`;
      return;
    }
    if (/^###\s/.test(line)) { html += `<h3>${inlineMd(line.slice(4))}</h3>`; return; }
    if (/^##\s/.test(line)) { html += `<h2>${inlineMd(line.slice(3))}</h2>`; return; }
    if (/^#\s/.test(line)) { html += `<h1>${inlineMd(line.slice(2))}</h1>`; return; }
    if (/^[-*]\s/.test(line)) { html += `<ul><li>${inlineMd(line.slice(2))}</li></ul>`; return; }
    if (line.trim() === "") { html += `<br>`; return; }
    html += `<p>${inlineMd(line)}</p>`;
  });
  if (inCode) html += `<pre><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`;
  return html;
}

function inlineMd(s) {
  let t = escapeHtml(s);
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  return t;
}

function toggleCheckboxInDesc(lineIndex) {
  const ta = $("#taskDesc");
  const lines = ta.value.split("\n");
  const line = lines[lineIndex];
  const m = line.match(/^([-*]\s*\[)([ xX])(\]\s*.*)$/);
  if (m) {
    const newMark = m[2].toLowerCase() === "x" ? " " : "x";
    lines[lineIndex] = m[1] + newMark + m[3];
    ta.value = lines.join("\n");
    dirty = true;
    renderPreview();
  }
}

function renderPreview() {
  const html = renderMarkdown($("#taskDesc").value);
  $("#taskDescPreview").innerHTML = html;
  // attach checkbox handlers
  $("#taskDescPreview").querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener("change", () => {
      toggleCheckboxInDesc(parseInt(cb.dataset.line));
    });
  });
}

async function saveTask() {
  const id = $("#taskId").value;
  const statusList = $("#statusList");
  const columnId = parseInt(statusList.dataset.selected || $("#taskColumn")?.value);
  const payload = {
    title: $("#taskTitle").value.trim(),
    description: $("#taskDesc").value,
    priority: $("#taskPriority").value,
    category: $("#taskCategory").value,
    column_id: columnId,
    agent_id: $("#taskAgent").value.trim() || null,
    affected_files: $("#taskAffectedFiles").value,
  };
  if (!payload.title) { alert("Укажите название"); return; }
  try {
    if (id) {
      await api(`/tasks/${id}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      const created = await api("/tasks", { method: "POST", body: JSON.stringify({ ...payload, project_id: currentProjectId }) });
      // switch to edit mode with new id
      $("#taskId").value = created.id;
      currentTaskId = created.id;
      $("#modalTitle").textContent = `Задача #${created.id}`;
      $("#commentsSection").style.display = "block";
      await loadComments(created.id);
    }
    dirty = false;
    await loadTasks();
  } catch (err) { alert(err.message); }
}

async function deleteTask() {
  const id = $("#taskId").value;
  if (!id) { closeModal(); return; }
  if (!confirm("Удалить задачу?")) return;
  try {
    await api(`/tasks/${id}`, { method: "DELETE" });
    dirty = false;
    closeModal();
    await loadTasks();
  } catch (err) { alert(err.message); }
}

/* ---------- Filters (multi) ---------- */
function getMultiSelected(id) {
  const panel = $(`#${id} .ms-panel`);
  return Array.from(panel.querySelectorAll("input:checked")).map(i => i.value);
}

function renderFilters() {
  const defaults = ["Feature","Bug","Rework","Balance","Cleanup"];
  const cats = Array.from(new Set([...defaults, ...allTasks.map(t => t.category).filter(Boolean)]));
  const prios = ["Low","Medium","High","Critical"];
  buildMulti("catFilter", cats, "Категории");
  buildMulti("prioFilter", prios, "Приоритеты");
}

function buildMulti(id, options, label) {
  const container = $(`#${id}`);
  const btn = container.querySelector(".ms-btn");
  const panel = container.querySelector(".ms-panel");
  panel.innerHTML = "";
  options.forEach(opt => {
    const lab = document.createElement("label");
    lab.innerHTML = `<input type="checkbox" value="${opt}"> ${opt}`;
    panel.appendChild(lab);
  });
  btn.textContent = `${label} ▾`;
  // update label with count
  panel.onchange = () => {
    const n = panel.querySelectorAll("input:checked").length;
    btn.textContent = n ? `${label} (${n}) ▾` : `${label} ▾`;
    applyFilters();
  };
  btn.onclick = (e) => {
    e.stopPropagation();
    container.classList.toggle("open");
  };
}

/* ---------- Settings ---------- */
async function openSettings() {
  $("#settingsDrawer").classList.add("open");
  $("#settingsOverlay").classList.add("active");
  const settings = await api("/settings");
  const body = $("#settingsBody");
  body.innerHTML = "";

  const mcpUrl = `${location.origin}/mcp?project=${encodeURIComponent(currentProjectSlug || "default")}`;
  const info = document.createElement("div");
  info.className = "mcp-url-card";
  info.innerHTML = `
    <div class="mcp-url-label">MCP HTTP Stream для текущего проекта</div>
    <code>${escapeHtml(mcpUrl)}</code>
    <button class="btn btn-ghost" type="button">Копировать URL</button>
  `;
  info.querySelector("button").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(mcpUrl);
      info.querySelector("button").textContent = "Скопировано";
    } catch (e) {
      prompt("Скопируйте URL:", mcpUrl);
    }
  });
  body.appendChild(info);

  settings.forEach(s => {
    const row = document.createElement("div");
    row.className = "setting-row";
    row.innerHTML = `
      <div>
        <div><strong>${escapeHtml(s.key)}</strong></div>
        <div class="desc">${escapeHtml(s.description)}</div>
      </div>
      <div class="toggle ${s.is_enabled ? "on" : ""}" data-key="${s.key}"></div>
    `;
    row.querySelector(".toggle").addEventListener("click", async (e) => {
      const key = e.currentTarget.dataset.key;
      const enabled = !e.currentTarget.classList.contains("on");
      await api(`/settings/${key}`, { method: "PUT", body: JSON.stringify({ is_enabled: enabled }) });
      e.currentTarget.classList.toggle("on", enabled);
    });
    body.appendChild(row);
  });
}
function closeSettings() {
  $("#settingsDrawer").classList.remove("open");
  $("#settingsOverlay").classList.remove("active");
}

/* ---------- Analyze ---------- */
async function analyze() {
  const res = await api(`/analyze?project_id=${currentProjectId}`);
  const s = res.suggestion;
  let msg = "Рекомендация: ";
  if (s.recommendation) {
    msg += `#${s.recommendation.id} ${s.recommendation.title} (${s.reason})`;
  } else {
    msg += s.reason;
  }
  if (res.stalled_tasks && res.stalled_tasks.length) {
    msg += `\n\n зависших задач: ${res.stalled_tasks.length}`;
  }
  alert(msg);
}

/* ---------- Init ---------- */
document.addEventListener("DOMContentLoaded", async () => {
  await loadProjects();
  await loadColumns();
  await loadTasks();
  await renderFilters();

  $("#projectSelect").addEventListener("change", (e) => switchProject(parseInt(e.target.value)));
  $("#btnNewProject").addEventListener("click", createProject);
  $("#btnRenameProject").addEventListener("click", renameProject);
  $("#btnDeleteProject").addEventListener("click", deleteProject);

  $("#searchInput").addEventListener("input", applyFilters);

  $("#btnNewTask").addEventListener("click", () => openTaskModal(null));
  $("#btnSettings").addEventListener("click", openSettings);
  $("#btnAnalyze").addEventListener("click", analyze);
  $("#modalClose").addEventListener("click", closeModal);
  $("#btnSaveTask").addEventListener("click", saveTask);
  $("#btnDeleteTask").addEventListener("click", deleteTask);
  $("#btnFullscreen").addEventListener("click", () => toggleFullscreen());

  $("#btnEditDesc").addEventListener("click", () => {
    descMode = "edit";
    $("#taskDesc").style.display = "";
    $("#taskDescPreview").style.display = "none";
    $("#btnEditDesc").classList.add("active");
    $("#btnPreviewDesc").classList.remove("active");
  });
  $("#btnPreviewDesc").addEventListener("click", () => {
    descMode = "preview";
    $("#taskDesc").style.display = "none";
    $("#taskDescPreview").style.display = "";
    $("#btnPreviewDesc").classList.add("active");
    $("#btnEditDesc").classList.remove("active");
    renderPreview();
  });

  // live preview update on typing
  $("#taskDesc").addEventListener("input", () => {
    if (descMode === "preview") renderPreview();
  });

  $("#btnAddComment").addEventListener("click", async () => {
    const id = $("#taskId").value;
    if (!id) return;
    const text = $("#commentText").value.trim();
    if (!text) return;
    await api(`/tasks/${id}/comments`, { method: "POST", body: JSON.stringify({ author: "User", text }) });
    $("#commentText").value = "";
    await loadComments(id);
    await loadTasks();
  });

  // column modal
  $("#columnModalClose").addEventListener("click", closeColumnModal);
  $("#btnSaveColumn").addEventListener("click", saveColumn);
  $("#btnDeleteColumn").addEventListener("click", deleteColumn);
  $("#columnModal").addEventListener("click", (e) => { if (e.target.id === "columnModal") closeColumnModal(); });

  $("#settingsClose").addEventListener("click", closeSettings);
  $("#settingsOverlay").addEventListener("click", closeSettings);
  $("#taskModal").addEventListener("click", (e) => { if (e.target.id === "taskModal") closeModal(); });

  trackDirty();

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".column-menu") && !e.target.closest(".ms")) closeMenus();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if ($("#taskModal").classList.contains("active")) closeModal();
    else if ($("#columnModal").classList.contains("active")) closeColumnModal();
    else if ($("#settingsDrawer").classList.contains("open")) closeSettings();
  });

  // polling
  setInterval(async () => {
    if ($("#taskModal").classList.contains("active")) return;
    try { await loadTasks(); } catch (e) {}
  }, 2500);
});
