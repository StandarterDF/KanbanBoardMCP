const API = "/api";
let projects = [];
let currentProjectId = null;
let currentProjectSlug = null;
let boards = [];
let currentBoardId = null;
let allTasks = [];
let tasks = [];
let columns = [];
let categories = [];
let dirty = false;
let currentTaskId = null;
let descMode = "edit";
let fullscreen = false;
let historyEvents = [];

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

/* ---------- Dates & stage helpers ---------- */
// Selects must never look empty when the stored value differs only by case
// (e.g. legacy "high"/"balance") or is a custom user value.
function setSelectValue(select, value) {
  if (!select || value === null || value === undefined || value === "") return;
  const target = String(value);
  const match = Array.from(select.options).find(
    (opt) => opt.value.toLowerCase() === target.toLowerCase()
  );
  if (match) {
    select.value = match.value;
    return;
  }
  const opt = document.createElement("option");
  opt.value = target;
  opt.textContent = target;
  select.appendChild(opt);
  select.value = target;
}

function formatDateTime(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (isNaN(d.getTime())) return String(value);
  try {
    return d.toLocaleString("ru-RU", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch (e) {
    return d.toLocaleString();
  }
}

function columnById(id) {
  return columns.find(c => c.id === id) || null;
}

function columnPosition(id) {
  return columns.findIndex(c => c.id === id);
}

// Only previous/next columns in the sorted project stage list are reachable.
function adjacentColumns(columnId) {
  const i = columnPosition(columnId);
  if (i < 0) return [];
  return [columns[i - 1], columns[i + 1]].filter(Boolean);
}

function allowedTargetIds(columnId) {
  return adjacentColumns(columnId).map(c => c.id);
}

function stageHint(columnId) {
  const names = adjacentColumns(columnId).map(c => c.name);
  if (!names.length) return "В проекте нет соседних стадий.";
  if (names.length === 1) return `Можно перемещать только на соседнюю стадию: ${names[0]}.`;
  return `Можно перемещать только на соседнюю стадию: ${names[0]} или ${names[1]}.`;
}

function actorLabel(actor) {
  const a = String(actor || "system");
  if (a === "user") return "Пользователь";
  if (a === "system") return "Система";
  if (a.startsWith("agent:")) return `Агент: ${a.slice(6)}`;
  return a;
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

/* ---------- Boards ---------- */
function boardStorageKey(projectId) {
  return `activeBoardId:${projectId}`;
}

function savedBoardId(projectId) {
  const raw = localStorage.getItem(boardStorageKey(projectId));
  const id = raw ? parseInt(raw, 10) : NaN;
  return Number.isFinite(id) ? id : null;
}

async function loadBoards() {
  boards = await api(`/boards?project_id=${currentProjectId}`);
  const saved = savedBoardId(currentProjectId);
  let active = boards.find(b => b.id === currentBoardId);
  if (!active) active = boards.find(b => b.id === saved);
  if (!active) active = boards.find(b => b.is_default) || boards[0] || null;
  currentBoardId = active ? active.id : null;
  if (currentBoardId) localStorage.setItem(boardStorageKey(currentProjectId), String(currentBoardId));
  renderBoards();
}

function renderBoards() {
  const wrap = $("#boardTabs");
  if (!wrap) return;
  const scrollLeft = wrap.scrollLeft;
  wrap.innerHTML = "";
  boards.forEach(b => {
    const chip = document.createElement("div");
    chip.className = "board-tab" + (b.id === currentBoardId ? " active" : "");
    chip.dataset.boardId = b.id;
    chip.title = b.is_default
      ? "Главная доска проекта (удалить нельзя)"
      : "Доска проекта";

    const name = document.createElement("span");
    name.className = "board-tab-name";
    name.textContent = b.name;
    chip.appendChild(name);

    const count = document.createElement("span");
    count.className = "board-tab-count";
    count.textContent = b.task_count ?? 0;
    chip.appendChild(count);

    if (!b.is_default) {
      const rename = document.createElement("button");
      rename.type = "button";
      rename.className = "board-tab-action";
      rename.title = "Переименовать доску";
      rename.textContent = "✎";
      rename.addEventListener("click", (e) => {
        e.stopPropagation();
        renameBoard(b.id);
      });
      chip.appendChild(rename);

      const del = document.createElement("button");
      del.type = "button";
      del.className = "board-tab-action danger";
      del.title = "Удалить доску";
      del.textContent = "🗑";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteBoard(b.id);
      });
      chip.appendChild(del);
    }

    chip.addEventListener("click", () => switchBoard(b.id));
    wrap.appendChild(chip);
  });
  wrap.scrollLeft = scrollLeft;
}

async function switchBoard(id) {
  if (id === currentBoardId) return;
  if (!boards.find(b => b.id === id)) return;
  currentBoardId = id;
  localStorage.setItem(boardStorageKey(currentProjectId), String(id));
  renderBoards();
  await loadTasks();
}

async function createBoard() {
  const name = prompt("Название доски:");
  if (!name || !name.trim()) return;
  try {
    const board = await api("/boards", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), project_id: currentProjectId }),
    });
    await loadBoards();
    if (board && board.id) await switchBoard(board.id);
    else await loadTasks();
  } catch (err) { alert(err.message); }
}

async function renameBoard(id) {
  const board = boards.find(b => b.id === id);
  if (!board) return;
  const name = prompt("Новое название доски:", board.name);
  if (!name || !name.trim() || name.trim() === board.name) return;
  try {
    await api(`/boards/${id}`, { method: "PUT", body: JSON.stringify({ name: name.trim() }) });
    await loadBoards();
    await loadTasks();
  } catch (err) { alert(err.message); }
}

async function deleteBoard(id) {
  const board = boards.find(b => b.id === id);
  if (!board) return;
  if (board.is_default) { alert("Главную доску удалить нельзя"); return; }
  if (!confirm(`Удалить доску «${board.name}»?`)) return;
  try {
    await api(`/boards/${id}`, { method: "DELETE" });
    await loadBoards();
    await loadTasks();
  } catch (err) { alert(err.message); }
}

function syncActiveBoardCount() {
  const board = boards.find(b => b.id === currentBoardId);
  if (!board) return;
  if (board.task_count !== allTasks.length) {
    board.task_count = allTasks.length;
    renderBoards();
  }
}

async function switchProject(id) {
  const p = projects.find(x => x.id === id);
  if (!p) return;
  currentProjectId = p.id;
  currentProjectSlug = p.slug;
  currentBoardId = null;
  localStorage.setItem("activeProjectSlug", p.slug);
  renderProjectSelect();
  await loadBoards();
  await loadCategories();
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
  currentBoardId = null;
  localStorage.setItem("activeProjectSlug", p.slug);
  await loadBoards();
  await loadCategories();
  await loadColumns();
  await loadTasks();
  await renderFilters();
  renderProjectSelect();
}

/* ---------- Columns ---------- */
async function loadColumns() {
  columns = await api(`/columns?project_id=${currentProjectId}`);
}

/* ---------- Categories ---------- */
async function loadCategories() {
  categories = await api(`/categories?project_id=${currentProjectId}`);
}

function categoryByName(name) {
  const target = String(name ?? "").toLowerCase();
  return categories.find(c => String(c.name).toLowerCase() === target) || null;
}

function categoryColor(name) {
  const c = categoryByName(name);
  return c ? c.color : "#5b8cff";
}

// Category select is fed exclusively from the current project's categories.
// Existing tasks whose category is missing from the list keep a temporary
// option so their value is not lost; new tasks can only pick list values.
function populateCategorySelect(selected, allowLegacy) {
  const sel = $("#taskCategory");
  sel.innerHTML = "";
  categories.forEach(cat => {
    const opt = document.createElement("option");
    opt.value = cat.name;
    opt.textContent = cat.name;
    sel.appendChild(opt);
  });
  if (selected) {
    const match = Array.from(sel.options).find(
      opt => opt.value.toLowerCase() === String(selected).toLowerCase()
    );
    if (match) {
      sel.value = match.value;
    } else if (allowLegacy) {
      const opt = document.createElement("option");
      opt.value = selected;
      opt.textContent = `${selected} (нет в проекте)`;
      sel.appendChild(opt);
      sel.value = selected;
    }
  }
  if (!sel.value && sel.options.length) sel.selectedIndex = 0;
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
  const boardQuery = currentBoardId != null ? `&board_id=${currentBoardId}` : "";
  allTasks = await api(`/tasks?project_id=${currentProjectId}${boardQuery}`);
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

function captureBoardScroll() {
  const board = $("#board");
  if (!board) return null;
  const columnsScroll = {};
  board.querySelectorAll(".column-body").forEach(body => {
    columnsScroll[body.dataset.columnId] = body.scrollTop;
  });
  return { left: board.scrollLeft, top: board.scrollTop, columns: columnsScroll };
}

function restoreBoardScroll(state) {
  if (!state) return;
  const board = $("#board");
  if (!board) return;
  board.scrollLeft = state.left;
  board.scrollTop = state.top;
  board.querySelectorAll(".column-body").forEach(body => {
    const value = state.columns[body.dataset.columnId];
    if (value != null) body.scrollTop = value;
  });
}

function renderBoard() {
  const board = $("#board");
  // Polling re-renders the whole board; keep the user's scroll position.
  const scrollState = captureBoardScroll();
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
    body.addEventListener("dragover", (e) => {
      if (draggedTaskId == null) return;
      const dragged = allTasks.find(t => t.id === draggedTaskId);
      const targetCol = parseInt(body.dataset.columnId);
      const valid = dragged && allowedTargetIds(dragged.column_id).includes(targetCol);
      e.preventDefault();
      if (valid) {
        e.dataTransfer.dropEffect = "move";
        body.classList.add("drag-over");
        body.classList.remove("drag-invalid");
      } else {
        e.dataTransfer.dropEffect = "none";
        body.classList.add("drag-invalid");
        body.classList.remove("drag-over");
      }
    });
    body.addEventListener("dragleave", () => {
      body.classList.remove("drag-over");
      body.classList.remove("drag-invalid");
    });
    body.addEventListener("drop", async (e) => {
      e.preventDefault();
      body.classList.remove("drag-over");
      body.classList.remove("drag-invalid");
      if (draggedTaskId == null) return;
      const dragged = allTasks.find(t => t.id === draggedTaskId);
      const targetCol = parseInt(body.dataset.columnId);
      const target = columnById(targetCol);
      const targetName = target ? target.name : `#${targetCol}`;
      if (!dragged || !allowedTargetIds(dragged.column_id).includes(targetCol)) {
        alert(`Нельзя переместить задачу сразу на стадию «${targetName}».\n${dragged ? stageHint(dragged.column_id) : "Обновите доску и попробуйте снова."}`);
        draggedTaskId = null;
        clearDropTargets();
        return;
      }
      try {
        await api(`/tasks/${draggedTaskId}/move`, { method: "POST", body: JSON.stringify({ column_id: targetCol }) });
        await loadTasks();
      } catch (err) { alert(err.message); }
      draggedTaskId = null;
      clearDropTargets();
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
          <span class="badge cat-badge" style="--cat-color:${escapeHtml(categoryColor(t.category))}">${escapeHtml(t.category)}</span>
        </div>
        <div class="card-comments">💬 ${t.comment_count || 0}</div>
      `;
      card.addEventListener("dragstart", (e) => {
        draggedTaskId = t.id;
        card.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        markDropTargets(t);
      });
      card.addEventListener("dragend", () => {
        draggedTaskId = null;
        card.classList.remove("dragging");
        clearDropTargets();
      });
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

  restoreBoardScroll(scrollState);
  syncActiveBoardCount();

  // close menus on outside click handled globally
}

let draggedTaskId = null;

function markDropTargets(task) {
  const allowed = allowedTargetIds(task.column_id);
  $$(".column-body").forEach(body => {
    const cid = parseInt(body.dataset.columnId);
    const isAllowed = allowed.includes(cid);
    body.classList.toggle("drop-allowed", isAllowed);
    body.classList.toggle("drop-disabled", !isAllowed);
  });
}

function clearDropTargets() {
  $$(".column-body").forEach(body => {
    body.classList.remove("drop-allowed", "drop-disabled", "drag-over", "drag-invalid");
  });
}

function closeMenus() {
  $$(".menu-panel.open").forEach(p => p.classList.remove("open"));
  $$(".ms.open").forEach(m => m.classList.remove("open"));
}

/* ---------- Task modal ---------- */
function setTaskTab(tab) {
  const isDesc = tab === "desc";
  $("#taskDescPane").style.display = isDesc ? "" : "none";
  $("#taskHistoryPane").style.display = isDesc ? "none" : "";
  $("#btnTabDesc").classList.toggle("active", isDesc);
  $("#btnTabHistory").classList.toggle("active", !isDesc);
}

function renderStatusButtons(taskColumnId) {
  const statusList = $("#statusList");
  statusList.innerHTML = "";
  statusList.dataset.selected = "";

  const isNew = !taskColumnId;
  const defaultCol = isNew
    ? (columns.find(c => c.name === "Backlog") || columns[0])
    : null;
  const currentId = isNew ? (defaultCol ? defaultCol.id : null) : taskColumnId;
  const currentIndex = columnPosition(currentId);
  const allowed = isNew
    ? columns.map(c => c.id)
    : (currentIndex < 0
        ? []
        : [columns[currentIndex - 1], columns[currentIndex], columns[currentIndex + 1]]
            .filter(Boolean)
            .map(c => c.id));

  columns.forEach(c => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "status-btn";
    btn.textContent = c.name;
    btn.dataset.columnId = c.id;

    const enabled = allowed.includes(c.id);
    const isCurrent = c.id === currentId;
    btn.disabled = !enabled;
    if (!enabled) {
      btn.title = isNew ? "Недоступно" : `Недоступно. ${stageHint(currentId)}`;
    } else if (!isCurrent && !isNew) {
      btn.title = `Переместить на соседнюю стадию «${c.name}»`;
    } else if (isCurrent) {
      btn.title = "Текущая стадия";
    }

    if (isCurrent) {
      btn.classList.add("active");
      statusList.dataset.selected = c.id;
    }
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      statusList.querySelectorAll(".status-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      statusList.dataset.selected = c.id;
      dirty = true;
    });
    statusList.appendChild(btn);
  });
}

async function openTaskModal(taskId) {
  currentTaskId = taskId;
  dirty = false;
  historyEvents = [];
  $("#taskId").value = taskId ?? "";
  $("#commentsSection").style.display = "none";
  setTaskTab("desc");
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
    setSelectValue($("#taskPriority"), t.priority);
    populateCategorySelect(t.category, true);
    $("#taskAgent").value = t.agent_id || "";
    $("#taskAffectedFiles").value = t.affected_files || "";
    $("#modalTitle").textContent = `Задача #${t.id}`;
    $("#taskPublicId").value = t.id;
    $("#btnCopyTaskId").disabled = false;
    $("#btnCopyTaskId").textContent = "Копировать ID";
    $("#saveStatus").textContent = "";
    $("#commentsSection").style.display = "block";
    $("#taskCreated").textContent = formatDateTime(t.created_at);
    $("#taskUpdated").textContent = formatDateTime(t.updated_at);
    $("#taskLastMove").textContent = "—";
    renderStatusButtons(t.column_id);
    renderPreview();
    await Promise.all([loadComments(taskId), loadTaskHistory(taskId)]);
  } else {
    $("#taskTitle").value = "";
    $("#taskDesc").value = "";
    $("#taskPriority").value = "Medium";
    populateCategorySelect(null, false);
    $("#taskAgent").value = "";
    $("#taskAffectedFiles").value = "";
    $("#modalTitle").textContent = "Новая задача";
    $("#taskPublicId").value = "";
    $("#btnCopyTaskId").disabled = true;
    $("#btnCopyTaskId").textContent = "Копировать ID";
    $("#saveStatus").textContent = "";
    $("#commentsList").innerHTML = "";
    $("#taskCreated").textContent = "—";
    $("#taskUpdated").textContent = "—";
    $("#taskLastMove").textContent = "—";
    $("#historyList").innerHTML = "<div class='history-empty'>История появится после создания задачи.</div>";
    renderStatusButtons(null);
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

/* ---------- Task history ---------- */
function historyIcon(action) {
  return ({ created: "＋", moved: "→", updated: "✎", note: "💬" })[action] || "•";
}

function historyActionLabel(action) {
  return ({
    created: "Создание",
    moved: "Перемещение",
    updated: "Изменение",
    note: "Заметка",
  })[action] || action;
}

function parseFieldSnapshot(value) {
  try {
    const parsed = JSON.parse(value ?? "null");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch (e) {
    return null;
  }
}

function shortValue(value, limit = 80) {
  const s = value === null || value === undefined || value === "" ? "—" : String(value);
  return s.length > limit ? s.slice(0, limit - 1) + "…" : s;
}

function historyBody(ev) {
  if (ev.action === "created") {
    return ev.new_column_name
      ? `Задача создана в колонке <span class="col-chip new">${escapeHtml(ev.new_column_name)}</span>`
      : "Задача создана";
  }
  if (ev.action === "moved") {
    const from = ev.old_column_name || (ev.old_column_id ? `Колонка #${ev.old_column_id}` : "—");
    const to = ev.new_column_name || (ev.new_column_id ? `Колонка #${ev.new_column_id}` : "—");
    return `<span class="col-chip">${escapeHtml(from)}</span><span class="history-arrow">→</span><span class="col-chip new">${escapeHtml(to)}</span>`;
  }
  if (ev.action === "updated") {
    const oldVals = parseFieldSnapshot(ev.old_value) || {};
    const newVals = parseFieldSnapshot(ev.new_value) || {};
    const keys = Object.keys(newVals);
    if (!keys.length) return "Поля задачи изменены";
    if (
      keys.length === 1 &&
      String(oldVals[keys[0]] ?? "").length <= 80 &&
      String(newVals[keys[0]] ?? "").length <= 80
    ) {
      const key = keys[0];
      return `Поле <span class="history-fields">${escapeHtml(key)}</span>: ${escapeHtml(shortValue(oldVals[key]))} <span class="history-arrow">→</span> ${escapeHtml(shortValue(newVals[key]))}`;
    }
    return `Изменены поля: <span class="history-fields">${keys.map(escapeHtml).join(", ")}</span>`;
  }
  if (ev.action === "note") {
    return escapeHtml(ev.new_value || ev.note || "Заметка");
  }
  return escapeHtml(ev.new_value || ev.action);
}

function renderHistory() {
  const box = $("#historyList");
  if (!historyEvents.length) {
    box.innerHTML = "<div class='history-empty'>Событий пока нет.</div>";
    return;
  }
  box.innerHTML = "";
  historyEvents.forEach(ev => {
    const item = document.createElement("div");
    item.className = "history-item";
    const note = (ev.action !== "note" && ev.note) ? ev.note : "";
    item.innerHTML = `
      <div class="history-icon action-${escapeHtml(ev.action)}">${historyIcon(ev.action)}</div>
      <div class="history-content">
        <div class="history-head">
          <span class="history-action">${escapeHtml(historyActionLabel(ev.action))}</span>
          <span class="history-time">${escapeHtml(formatDateTime(ev.created_at))}</span>
        </div>
        <div class="history-body">${historyBody(ev)}</div>
        <div class="history-actor">${escapeHtml(actorLabel(ev.actor))}</div>
        ${note ? `<div class="history-note">${escapeHtml(note)}</div>` : ""}
      </div>
    `;
    box.appendChild(item);
  });
}

async function loadTaskHistory(taskId) {
  const box = $("#historyList");
  box.innerHTML = "<div class='history-empty'>Загрузка истории...</div>";
  try {
    historyEvents = await api(`/tasks/${taskId}/history?project_id=${currentProjectId}`);
  } catch (err) {
    historyEvents = [];
    box.innerHTML = `<div class='history-empty'>Не удалось загрузить историю: ${escapeHtml(err.message)}</div>`;
    return;
  }
  renderHistory();
  const moves = historyEvents.filter(e => e.action === "moved");
  $("#taskLastMove").textContent = moves.length
    ? formatDateTime(moves[moves.length - 1].created_at)
    : "—";
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
      dirty = false;
      await loadTasks();
      closeModal();
    } else {
      await api("/tasks", { method: "POST", body: JSON.stringify({ ...payload, project_id: currentProjectId, board_id: currentBoardId }) });
      dirty = false;
      await loadTasks();
      closeModal();
    }
  } catch (err) { alert(err.message); }
}

function showSaveStatus(msg) {
  const el = $("#saveStatus");
  if (!el) return;
  el.textContent = msg || "Сохранено";
  el.classList.add("visible");
  if (showSaveStatus._timer) clearTimeout(showSaveStatus._timer);
  showSaveStatus._timer = setTimeout(() => {
    el.textContent = "";
    el.classList.remove("visible");
  }, 1800);
}

async function copyTaskId() {
  const input = $("#taskPublicId");
  const value = String(input ? input.value : "").trim();
  if (!value) return;
  const btn = $("#btnCopyTaskId");
  const original = "Копировать ID";
  let ok = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      ok = true;
    }
  } catch (e) { ok = false; }
  if (!ok) {
    const ta = document.createElement("textarea");
    ta.value = value;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
  }
  if (ok && btn) {
    btn.textContent = "Скопировано";
    btn.classList.add("copied");
    if (copyTaskId._timer) clearTimeout(copyTaskId._timer);
    copyTaskId._timer = setTimeout(() => {
      btn.textContent = original;
      btn.classList.remove("copied");
    }, 1500);
  } else if (btn) {
    window.prompt("Скопируйте ID:", value);
  }
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
  const catNames = categories.map(c => c.name);
  // Defensive: keep filters for task categories that somehow disappeared.
  allTasks.forEach(t => {
    if (t.category && !catNames.some(n => n.toLowerCase() === t.category.toLowerCase())) {
      catNames.push(t.category);
    }
  });
  const catColors = {};
  categories.forEach(c => { catColors[c.name] = c.color; });
  const prios = ["Low","Medium","High","Critical"];
  buildMulti("catFilter", catNames, "Категории", catColors);
  buildMulti("prioFilter", prios, "Приоритеты");
}

function buildMulti(id, options, label, colors) {
  const container = $(`#${id}`);
  const btn = container.querySelector(".ms-btn");
  const panel = container.querySelector(".ms-panel");
  panel.innerHTML = "";
  options.forEach(opt => {
    const lab = document.createElement("label");
    const color = colors && colors[opt];
    lab.innerHTML = `<input type="checkbox" value="${escapeHtml(opt)}">` +
      (color ? ` <span class="cat-dot" style="background:${escapeHtml(color)}"></span>` : "") +
      ` <span>${escapeHtml(opt)}</span>`;
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
function buildCategoryManager() {
  const wrap = document.createElement("div");
  wrap.className = "category-manager";
  wrap.innerHTML = `
    <div class="section-title">Категории проекта</div>
    <div class="category-list"></div>
    <div class="category-add">
      <input type="color" class="new-cat-color" value="#5b8cff" title="Цвет">
      <input type="text" class="new-cat-name" placeholder="Новая категория" maxlength="60">
      <button class="btn btn-primary btn-add-category" type="button">Добавить</button>
    </div>
  `;
  const list = wrap.querySelector(".category-list");

  const refresh = async () => {
    await loadCategories();
    renderFilters();
    applyFilters();
    renderRows();
  };

  const renderRows = () => {
    list.innerHTML = "";
    if (!categories.length) {
      list.innerHTML = '<div class="muted">Нет категорий</div>';
      return;
    }
    categories.forEach(cat => {
      const row = document.createElement("div");
      row.className = "category-row";
      row.innerHTML = `
        <input type="color" class="cat-color" value="${escapeHtml(cat.color)}" title="Цвет">
        <input type="text" class="cat-name" value="${escapeHtml(cat.name)}" maxlength="60">
        <button class="icon-btn cat-save" type="button" title="Сохранить">✓</button>
        <button class="icon-btn cat-delete" type="button" title="Удалить">🗑</button>
      `;
      const nameEl = row.querySelector(".cat-name");
      row.querySelector(".cat-save").addEventListener("click", async () => {
        const name = nameEl.value.trim();
        const color = row.querySelector(".cat-color").value;
        if (!name) { alert("Укажите название категории"); return; }
        try {
          await api(`/categories/${cat.id}`, {
            method: "PUT",
            body: JSON.stringify({ name, color }),
          });
          await refresh();
        } catch (err) { alert(err.message); }
      });
      row.querySelector(".cat-delete").addEventListener("click", async () => {
        if (!confirm(`Удалить категорию «${cat.name}»?`)) return;
        try {
          await api(`/categories/${cat.id}`, { method: "DELETE" });
          await refresh();
        } catch (err) { alert(err.message); }
      });
      list.appendChild(row);
    });
  };

  const addCategory = async () => {
    const nameEl = wrap.querySelector(".new-cat-name");
    const name = nameEl.value.trim();
    if (!name) { nameEl.focus(); return; }
    const color = wrap.querySelector(".new-cat-color").value;
    try {
      await api("/categories", {
        method: "POST",
        body: JSON.stringify({ name, color, project_id: currentProjectId }),
      });
      nameEl.value = "";
      await refresh();
    } catch (err) { alert(err.message); }
  };
  wrap.querySelector(".btn-add-category").addEventListener("click", addCategory);
  wrap.querySelector(".new-cat-name").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); addCategory(); }
  });

  renderRows();
  return wrap;
}

async function openSettings() {
  $("#settingsDrawer").classList.add("open");
  $("#settingsOverlay").classList.add("active");
  const settings = await api("/settings");
  const body = $("#settingsBody");
  body.innerHTML = "";

  await loadCategories();
  body.appendChild(buildCategoryManager());

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
  await loadBoards();
  await loadCategories();
  await loadColumns();
  await loadTasks();
  await renderFilters();

  $("#projectSelect").addEventListener("change", (e) => switchProject(parseInt(e.target.value)));
  $("#btnNewProject").addEventListener("click", createProject);
  $("#btnRenameProject").addEventListener("click", renameProject);
  $("#btnDeleteProject").addEventListener("click", deleteProject);
  $("#btnNewBoard").addEventListener("click", createBoard);

  $("#searchInput").addEventListener("input", applyFilters);

  $("#btnNewTask").addEventListener("click", () => openTaskModal(null));
  $("#btnSettings").addEventListener("click", openSettings);
  $("#btnAnalyze").addEventListener("click", analyze);
  $("#modalClose").addEventListener("click", closeModal);
  $("#btnSaveTask").addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); saveTask(); });
  $("#btnCopyTaskId").addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); copyTaskId(); });
  $("#btnDeleteTask").addEventListener("click", deleteTask);
  $("#btnFullscreen").addEventListener("click", () => toggleFullscreen());

  $("#btnTabDesc").addEventListener("click", () => setTaskTab("desc"));
  $("#btnTabHistory").addEventListener("click", () => setTaskTab("history"));

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
  // Never let clicks inside the dialog bubble to the overlay and close it.
  $("#taskModalDialog").addEventListener("click", (e) => e.stopPropagation());

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
    if (document.hidden) return;
    // Do not rebuild the DOM while the task modal is open or a card is dragged.
    if ($("#taskModal").classList.contains("active")) return;
    if ($("#columnModal").classList.contains("active")) return;
    if (draggedTaskId != null) return;
    try { await loadTasks(); } catch (e) {}
  }, 2500);
});
