const API = "/api";
let projects = [];
let currentProjectId = null;
let currentProjectSlug = null;
let boards = [];
let currentBoardId = null;
let columnSorts = {};
let allTasks = [];
let tasks = [];
let columns = [];
let categories = [];
let dirty = false;
let currentTaskId = null;
let currentTask = null;
let descMode = "edit";
let fullscreen = false;
let historyEvents = [];
let selectedTaskIds = new Set();
let commentAttachments = [];
let reviewAttachments = [];

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
      ? "Главная доска: все задачи проекта (удалить нельзя)"
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
  selectedTaskIds.clear();
  updateBulkBar();
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
  selectedTaskIds.clear();
  updateBulkBar();
  localStorage.setItem("activeProjectSlug", p.slug);
  renderProjectSelect();
  await loadBoards();
  await loadCategories();
  await loadColumns();
  await loadTasks();
  await renderFilters();
  renderViews();
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
  renderViews();
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

function populateBoardSelect(selected, forCreate = false) {
  const sel = $("#taskBoard");
  sel.innerHTML = "";
  const additional = boards.filter(b => !b.is_default);
  const mainBoards = boards.filter(b => b.is_default);
  // The main board is an aggregate view. While the project has additional
  // boards, new tasks must target one of them; the main board is not offered.
  const available = forCreate && additional.length
    ? additional
    : [...additional, ...mainBoards];

  if (forCreate && additional.length) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Выберите доску";
    placeholder.disabled = true;
    placeholder.selected = true;
    sel.appendChild(placeholder);
  }

  available.forEach(board => {
    const opt = document.createElement("option");
    opt.value = board.id;
    opt.textContent = board.is_default ? `${board.name} (без группы)` : board.name;
    sel.appendChild(opt);
  });

  let desired = selected;
  if (desired == null && !(forCreate && additional.length)) desired = currentBoardId;
  if (desired != null) {
    const match = Array.from(sel.options).find(opt => parseInt(opt.value, 10) === desired);
    if (match) sel.value = match.value;
  }
  if (!forCreate && !sel.value && sel.options.length) sel.selectedIndex = 0;
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
  const archivedQuery = $("#showArchived") && $("#showArchived").checked
    ? "&include_archived=true"
    : "";
  allTasks = await api(`/tasks?project_id=${currentProjectId}${boardQuery}${archivedQuery}`);
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
  const visible = new Set(tasks.map(t => t.id));
  for (const id of Array.from(selectedTaskIds)) {
    if (!visible.has(id)) selectedTaskIds.delete(id);
  }
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

const PRIORITY_RANK = { Critical: 0, High: 1, Medium: 2, Low: 3 };

function columnSortKey(columnId) {
  return `${currentProjectId}:${currentBoardId ?? "none"}:${columnId}`;
}

function columnSortMode(columnId) {
  return columnSorts[columnSortKey(columnId)] || "none";
}

function saveColumnSorts() {
  try {
    localStorage.setItem("kanbanColumnSorts", JSON.stringify(columnSorts));
  } catch (e) {}
}

function sortedColumnTasks(columnId) {
  const list = tasks.filter(t => t.column_id === columnId);
  const mode = columnSortMode(columnId);
  if (mode === "none") return list;
  const copy = [...list];
  if (mode === "priority") {
    copy.sort((a, b) => {
      const rank = (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9);
      return rank || (b.id - a.id);
    });
  } else if (mode === "priority_asc") {
    copy.sort((a, b) => {
      const rank = (PRIORITY_RANK[b.priority] ?? 9) - (PRIORITY_RANK[a.priority] ?? 9);
      return rank || (a.id - b.id);
    });
  } else if (mode === "date_desc") {
    copy.sort((a, b) => {
      const diff = Date.parse(b.created_at || 0) - Date.parse(a.created_at || 0);
      return (Number.isNaN(diff) ? 0 : diff) || (b.id - a.id);
    });
  } else if (mode === "date_asc") {
    copy.sort((a, b) => {
      const diff = Date.parse(a.created_at || 0) - Date.parse(b.created_at || 0);
      return (Number.isNaN(diff) ? 0 : diff) || (a.id - b.id);
    });
  }
  return copy;
}

function renderBoard() {
  const board = $("#board");
  const activeBoard = boards.find(b => b.id === currentBoardId) || null;
  const mainBoardActive = Boolean(activeBoard && activeBoard.is_default);
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
        <div class="column-tools">
          <select class="column-sort" data-column-id="${col.id}" title="Сортировка задач в колонке">
            <option value="none">Без сортировки</option>
            <option value="priority">Важность ↓</option>
            <option value="priority_asc">Важность ↑</option>
            <option value="date_desc">Дата ↓</option>
            <option value="date_asc">Дата ↑</option>
          </select>
          <div class="column-menu">
            <button class="menu-btn" type="button">•••</button>
            <div class="menu-panel">
              <button data-action="rename">Переименовать</button>
              <button data-action="color">Цвет</button>
              <button data-action="delete" class="danger">Удалить</button>
            </div>
          </div>
        </div>
      </div>
      <div class="column-body" data-column-id="${col.id}"></div>
    `;
    const sortSelect = colEl.querySelector(".column-sort");
    sortSelect.value = columnSortMode(col.id);
    sortSelect.addEventListener("change", (e) => {
      e.stopPropagation();
      columnSorts[columnSortKey(col.id)] = e.target.value;
      saveColumnSorts();
      renderBoard();
    });
    // Do not let the sort control start a card drag or close menus.
    sortSelect.addEventListener("mousedown", (e) => e.stopPropagation());
    sortSelect.addEventListener("click", (e) => e.stopPropagation());
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

    sortedColumnTasks(col.id).forEach(t => {
      const card = document.createElement("div");
      card.className = "card";
      card.draggable = true;
      card.dataset.taskId = t.id;
      const boardBadge = mainBoardActive && t.board_id !== currentBoardId
        ? `<span class="badge board-badge">${escapeHtml(t.board_name || "—")}</span>`
        : "";
      const criteriaBadge = t.criteria_total
        ? `<span class="badge criteria-badge">✓ ${t.criteria_done}/${t.criteria_total}</span>`
        : "";
      const rejectionBadge = t.rejections
        ? `<span class="badge rejection-badge">↺ ${t.rejections}</span>`
        : "";
      const archiveBadge = t.archived
        ? `<span class="badge archive-badge">архив</span>`
        : "";
      card.innerHTML = `
        <input type="checkbox" class="card-select" title="Выбрать задачу" ${selectedTaskIds.has(t.id) ? "checked" : ""} />
        <div class="card-title">${escapeHtml(t.title)}</div>
        <div class="card-meta">
          <span class="badge priority-${t.priority}">${t.priority}</span>
          <span class="badge cat-badge" style="--cat-color:${escapeHtml(categoryColor(t.category))}">${escapeHtml(t.category)}</span>
          ${boardBadge}${criteriaBadge}${rejectionBadge}${archiveBadge}
        </div>
        <div class="card-comments">💬 ${t.comment_count || 0}</div>
      `;
      if (t.archived) card.classList.add("archived");
      if (selectedTaskIds.has(t.id)) card.classList.add("selected");
      const selectBox = card.querySelector(".card-select");
      selectBox.addEventListener("click", (e) => e.stopPropagation());
      selectBox.addEventListener("change", (e) => {
        e.stopPropagation();
        toggleTaskSelection(t.id, selectBox.checked);
      });
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
      card.addEventListener("click", (e) => {
        if (e.target.closest(".card-select")) return;
        if (e.ctrlKey || e.metaKey) {
          toggleTaskSelection(t.id, !selectedTaskIds.has(t.id));
          return;
        }
        openTaskModal(t.id);
      });
      body.appendChild(card);
    });
    board.appendChild(colEl);
  });

  renderBulkColumns();
  updateBulkBar();

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
  const isCriteria = tab === "criteria";
  $("#taskDescPane").style.display = isDesc ? "" : "none";
  $("#taskCriteriaPane").style.display = isCriteria ? "" : "none";
  $("#taskHistoryPane").style.display = !isDesc && !isCriteria ? "" : "none";
  $("#btnTabDesc").classList.toggle("active", isDesc);
  $("#btnTabCriteria").classList.toggle("active", isCriteria);
  $("#btnTabHistory").classList.toggle("active", !isDesc && !isCriteria);
}

let pendingCriteria = [];

function criteriaItems() {
  if (currentTask) return currentTask.acceptance || [];
  return pendingCriteria;
}

function renderCriteria(task) {
  const items = task ? task.acceptance || [] : pendingCriteria;
  const done = items.filter(c => c.done).length;
  $("#criteriaProgress").textContent = `${done}/${items.length}`;
  $("#criteriaBadge").textContent = items.length ? `${done}/${items.length}` : "";
  const list = $("#criteriaList");
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = "<div class='muted criteria-empty'>Критериев пока нет. Добавьте пункты, которые доказывают готовность.</div>";
    return;
  }
  items.forEach((c, i) => {
    const row = document.createElement("div");
    row.className = "criterion" + (c.done ? " done" : "");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!c.done;
    cb.disabled = !currentTaskId;
    cb.title = currentTaskId ? "Отметить с доказательством" : "Сохраните задачу, чтобы отмечать критерии";
    cb.addEventListener("change", () => toggleCriterion(i + 1, cb.checked));
    const text = document.createElement("div");
    text.className = "criterion-text";
    text.innerHTML =
      `<div>${escapeHtml(c.text)}</div>` +
      (c.evidence ? `<div class="criterion-evidence">${escapeHtml(c.evidence)}</div>` : "");
    const del = document.createElement("button");
    del.type = "button";
    del.className = "icon-btn criterion-delete";
    del.textContent = "×";
    del.title = "Удалить критерий";
    del.addEventListener("click", () => removeCriterion(i));
    row.append(cb, text, del);
    list.appendChild(row);
  });
}

async function toggleCriterion(index, done) {
  if (!currentTaskId) return;
  let evidence = "";
  if (done) {
    evidence = prompt("Доказательство (вывод сборки, теста или ручной проверки):", "");
    if (evidence === null) { renderCriteria(currentTask); return; }
    evidence = evidence.trim();
    if (!evidence) {
      alert("Без доказательства критерий отметить нельзя");
      renderCriteria(currentTask);
      return;
    }
  }
  try {
    const t = await api(`/tasks/${currentTaskId}/criteria/${index}/check`, {
      method: "POST",
      body: JSON.stringify({ evidence, done }),
    });
    currentTask = t;
    renderCriteria(t);
    await loadTaskHistory(currentTaskId);
    await loadTasks();
  } catch (err) { alert(err.message); renderCriteria(currentTask); }
}

async function saveCriteriaTexts(texts) {
  if (currentTaskId) {
    const t = await api(`/tasks/${currentTaskId}/criteria`, {
      method: "PUT",
      body: JSON.stringify({ criteria: texts }),
    });
    currentTask = t;
    renderCriteria(t);
    await loadTasks();
  } else {
    pendingCriteria = texts.map(text => ({ text, done: false, evidence: "" }));
    renderCriteria(null);
  }
}

function removeCriterion(index) {
  const texts = criteriaItems()
    .filter((_, i) => i !== index)
    .map(c => c.text);
  saveCriteriaTexts(texts).catch(err => alert(err.message));
}

function addCriterion() {
  const input = $("#newCriterionText");
  const text = (input.value || "").trim();
  if (!text) return;
  const texts = criteriaItems().map(c => c.text).concat(text);
  saveCriteriaTexts(texts).then(() => {
    input.value = "";
  }).catch(err => alert(err.message));
}

function renderReviewCard(task) {
  const card = $("#reviewCard");
  if (!task) { card.style.display = "none"; return; }
  const position = columnPosition(task.column_id);
  const isReview = columns.length >= 3 && position === 2;
  card.style.display = isReview ? "" : "none";
  const parts = [];
  if (task.rejections) parts.push(`Отклонений: ${task.rejections}`);
  if (task.verified_at) parts.push(`Проверено: ${formatDateTime(task.verified_at)}`);
  const review = task.review_comment;
  if (review) {
    parts.push(review.kind === "reject"
      ? `Последнее решение: отклонено ${formatDateTime(review.created_at)}`
      : `Последнее решение: принято ${formatDateTime(review.created_at)}`);
  }
  $("#reviewMeta").textContent = parts.join(" · ") || "Ожидает проверки";
}

function updateArchiveButton(task) {
  const btn = $("#btnArchiveTask");
  if (!task) { btn.style.display = "none"; return; }
  btn.style.display = "";
  btn.dataset.archived = task.archived ? "1" : "0";
  btn.textContent = task.archived ? "Восстановить" : "В архив";
}

async function refreshTaskAfterAction(t) {
  currentTask = t;
  renderStatusButtons(t.column_id);
  renderReviewCard(t);
  renderCriteria(t);
  updateArchiveButton(t);
  $("#taskUpdated").textContent = formatDateTime(t.updated_at);
  await Promise.all([loadComments(t.id), loadTaskHistory(t.id)]);
  await loadTasks();
  await loadBoards();
}

async function verifyCurrentTask() {
  openReviewModal("verify");
}

async function rejectCurrentTask() {
  openReviewModal("reject");
}

const REVIEW_MODES = {
  verify: {
    title: "Проверить задачу",
    hint: "Задача будет отмечена как проверенная и перейдёт в следующую колонку.",
    label: "Комментарий к проверке (необязательно)",
    placeholder: "Что проверено, что стоит учесть дальше...",
    confirm: "Проверить",
    confirmClass: "btn btn-primary",
    optional: true,
  },
  reject: {
    title: "Отклонить задачу",
    hint: "Задача вернётся на предыдущий этап, а причина попадёт в комментарии.",
    label: "Причина отклонения (обязательно)",
    placeholder: "Что не так и что нужно исправить...",
    confirm: "Отклонить",
    confirmClass: "btn btn-danger",
    optional: false,
  },
};

let reviewMode = "verify";

function openReviewModal(mode) {
  if (!currentTaskId) return;
  reviewMode = REVIEW_MODES[mode] ? mode : "verify";
  const cfg = REVIEW_MODES[reviewMode];
  $("#reviewModalTitle").textContent = cfg.title;
  $("#reviewModalHint").textContent = cfg.hint;
  $("#reviewNoteLabel").textContent = cfg.label;
  const note = $("#reviewNote");
  note.value = "";
  note.placeholder = cfg.placeholder;
  $("#reviewModalError").textContent = "";
  reviewAttachments = [];
  renderReviewAttachments();
  const confirmBtn = $("#btnConfirmReview");
  confirmBtn.textContent = cfg.confirm;
  confirmBtn.className = cfg.confirmClass;
  $("#reviewModal").classList.add("active");
  note.style.height = "";
  requestAnimationFrame(() => autoResizeTextarea(note, window.innerHeight * 0.45));
  note.focus();
}

function closeReviewModal() {
  $("#reviewModal").classList.remove("active");
  $("#reviewNote").value = "";
  $("#reviewModalError").textContent = "";
  reviewAttachments = [];
  renderReviewAttachments();
}

async function submitReview() {
  if (!currentTaskId) return;
  const cfg = REVIEW_MODES[reviewMode];
  const note = ($("#reviewNote").value || "").trim();
  const errorBox = $("#reviewModalError");
  if (!cfg.optional && !note) {
    errorBox.textContent = "Укажите причину отклонения.";
    $("#reviewNote").focus();
    return;
  }
  errorBox.textContent = "";
  if (attachmentsUploading(reviewAttachments)) {
    errorBox.textContent = "Дождитесь загрузки изображений.";
    return;
  }
  if (attachmentsFailed(reviewAttachments)) {
    errorBox.textContent = "Уберите изображения, которые не удалось загрузить.";
    return;
  }
  const images = attachmentUrls(reviewAttachments);
  const confirmBtn = $("#btnConfirmReview");
  confirmBtn.disabled = true;
  try {
    const t = reviewMode === "verify"
      ? await api(`/tasks/${currentTaskId}/verify`, {
          method: "POST",
          body: JSON.stringify({ note, move_to_next: true, images }),
        })
      : await api(`/tasks/${currentTaskId}/reject`, {
          method: "POST",
          body: JSON.stringify({ reason: note, images }),
        });
    closeReviewModal();
    await refreshTaskAfterAction(t);
  } catch (err) {
    errorBox.textContent = err.message;
  } finally {
    confirmBtn.disabled = false;
  }
}

async function toggleArchiveCurrentTask() {
  if (!currentTaskId) return;
  const archived = $("#btnArchiveTask").dataset.archived === "1";
  try {
    const t = await api(`/tasks/${currentTaskId}/${archived ? "restore" : "archive"}`, {
      method: "POST",
    });
    await refreshTaskAfterAction(t);
  } catch (err) { alert(err.message); }
}

const TASK_TEMPLATE = [
  "**Цель:**",
  "",
  "**Требования:**",
  "1. ",
  "",
  "**Затронутые файлы:**",
  "",
  "**Как проверить:**",
  "1. ",
].join("\n");

function insertTaskTemplate() {
  const area = $("#taskDesc");
  if (area.value.trim() && !confirm("Заменить текущее описание шаблоном?")) return;
  area.value = TASK_TEMPLATE;
  renderPreview();
  dirty = true;
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
    currentTask = t;
    $("#taskTitle").value = t.title;
    $("#taskDesc").value = t.description || "";
    setSelectValue($("#taskPriority"), t.priority);
    populateCategorySelect(t.category, true);
    populateBoardSelect(t.board_id);
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
    renderCriteria(t);
    renderReviewCard(t);
    updateArchiveButton(t);
    await Promise.all([loadComments(taskId), loadTaskHistory(taskId)]);
  } else {
    currentTask = null;
    pendingCriteria = [];
    $("#taskTitle").value = "";
    $("#taskDesc").value = "";
    $("#taskPriority").value = "Medium";
    populateCategorySelect(null, false);
    const activeBoard = boards.find(b => b.id === currentBoardId);
    populateBoardSelect(activeBoard && !activeBoard.is_default ? currentBoardId : null, true);
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
    renderCriteria(null);
    renderReviewCard(null);
    updateArchiveButton(null);
  }

  $("#taskModal").classList.add("active");
}

function closeModal() {
  if (dirty && !confirm("Есть несохранённые изменения. Закрыть?")) return;
  $("#taskModal").classList.remove("active");
  if (fullscreen) toggleFullscreen(false);
  currentTaskId = null;
  currentTask = null;
  pendingCriteria = [];
  dirty = false;
  commentAttachments = [];
  renderCommentAttachments();
}

const COMMENT_KIND_LABELS = { verify: "Принято", reject: "Отклонено" };

/* ---------- Image attachments (comments / reviews) ---------- */
const IMAGE_MAX_DIMENSION = 2200;
const IMAGE_QUALITY = 0.88;
const IMAGE_SOURCE_LIMIT = 25 * 1024 * 1024;

function readFileAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Не удалось прочитать файл"));
    reader.readAsDataURL(file);
  });
}

function loadImageElement(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Не удалось открыть изображение"));
    img.src = src;
  });
}

// Shrinks large screenshots/photos in the browser before upload.
async function fileToDataURL(file) {
  if (!file.type.startsWith("image/")) throw new Error("Это не изображение");
  if (file.size > IMAGE_SOURCE_LIMIT) throw new Error("Файл больше 25 МБ");
  const original = await readFileAsDataURL(file);
  const keepOriginal = file.size <= 1.2 * 1024 * 1024
    && /^image\/(png|jpeg|gif|webp|bmp)$/i.test(file.type);
  if (keepOriginal) return original;
  try {
    const img = await loadImageElement(original);
    const scale = Math.min(1, IMAGE_MAX_DIMENSION / Math.max(img.width, img.height));
    const width = Math.max(1, Math.round(img.width * scale));
    const height = Math.max(1, Math.round(img.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return original;
    // JPEG has no alpha: paint a white background instead of black.
    if (file.type === "image/jpeg" || file.type === "image/jpg") {
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, width, height);
    }
    ctx.drawImage(img, 0, 0, width, height);
    let out = canvas.toDataURL("image/webp", IMAGE_QUALITY);
    if (!out.startsWith("data:image/webp")) {
      out = canvas.toDataURL("image/jpeg", IMAGE_QUALITY);
    }
    return out.length < original.length ? out : original;
  } catch (e) {
    return original;
  }
}

async function uploadImageFile(file) {
  const dataUrl = await fileToDataURL(file);
  const res = await api("/uploads", {
    method: "POST",
    body: JSON.stringify({ data_url: dataUrl, filename: file.name }),
  });
  return { url: res.url, preview: dataUrl };
}

function renderAttachments(selector, list, rerender) {
  const box = $(selector);
  if (!box) return;
  box.innerHTML = "";
  list.forEach((item) => {
    const el = document.createElement("div");
    el.className = `attach-preview ${item.status}`;

    if (item.preview || item.url) {
      const img = document.createElement("img");
      img.src = item.preview || item.url;
      img.alt = item.name || "Изображение";
      img.style.cursor = "zoom-in";
      img.title = "Открыть на весь экран";
      img.addEventListener("click", () => openImageViewer(item.url || item.preview));
      el.appendChild(img);
    }
    if (item.status === "uploading") {
      const bar = document.createElement("div");
      bar.className = "attach-progress";
      el.appendChild(bar);
      el.title = "Загрузка...";
    }
    if (item.status === "error") {
      const err = document.createElement("div");
      err.className = "attach-failed";
      err.textContent = item.error || "Ошибка";
      el.appendChild(err);
      el.title = item.error || "Не удалось загрузить";
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "attach-remove";
    remove.textContent = "×";
    remove.title = "Убрать изображение";
    remove.addEventListener("click", () => {
      const idx = list.findIndex(a => a.id === item.id);
      if (idx >= 0) list.splice(idx, 1);
      rerender();
    });
    el.appendChild(remove);
    box.appendChild(el);
  });
}

function renderCommentAttachments() {
  renderAttachments("#commentImagePreviews", commentAttachments, renderCommentAttachments);
}

function renderReviewAttachments() {
  renderAttachments("#reviewImagePreviews", reviewAttachments, renderReviewAttachments);
}

async function addAttachmentFiles(files, list, rerender) {
  const images = Array.from(files || []).filter(f => f && f.type.startsWith("image/"));
  for (const file of images) {
    const item = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      name: file.name,
      status: "uploading",
      preview: "",
      url: "",
    };
    list.push(item);
    rerender();
    try {
      const uploaded = await uploadImageFile(file);
      item.preview = uploaded.preview;
      item.url = uploaded.url;
      item.status = "ready";
    } catch (err) {
      item.status = "error";
      item.error = err.message;
    }
    rerender();
  }
}

function attachmentUrls(list) {
  return list.filter(a => a.status === "ready" && a.url).map(a => a.url);
}

function attachmentsUploading(list) {
  return list.some(a => a.status === "uploading");
}

function attachmentsFailed(list) {
  return list.some(a => a.status === "error");
}

function pasteImagesInto(e, list, rerender) {
  const clipboard = e.clipboardData;
  if (!clipboard) return;
  const items = Array.from(clipboard.items || []);
  let files = items
    .filter(it => it.kind === "file" && it.type.startsWith("image/"))
    .map(it => it.getAsFile())
    .filter(Boolean);
  if (!files.length && clipboard.files && clipboard.files.length) {
    files = Array.from(clipboard.files).filter(f => f && f.type.startsWith("image/"));
  }
  if (!files.length) return;
  e.preventDefault();
  addAttachmentFiles(files, list, rerender);
}

function autoResizeTextarea(el, maxHeight = 420) {
  if (!el) return;
  el.style.height = "auto";
  const height = Math.min(el.scrollHeight, maxHeight);
  el.style.height = `${height}px`;
  el.style.overflowY = el.scrollHeight > maxHeight ? "auto" : "hidden";
}

function commentImagesHtml(images) {
  const list = Array.isArray(images) ? images.filter(u => typeof u === "string" && u) : [];
  if (!list.length) return "";
  return `<div class="comment-images">${list.map(url =>
    `<a href="${escapeHtml(url)}" data-image-viewer="${escapeHtml(url)}" `
    + `title="Открыть на весь экран">`
    + `<img src="${escapeHtml(url)}" alt="Вложение" loading="lazy"></a>`).join("")}</div>`;
}

function openImageViewer(url) {
  if (!url) return;
  const viewer = $("#imageViewer");
  $("#imageViewerImg").src = url;
  viewer.classList.add("active");
  viewer.setAttribute("aria-hidden", "false");
  document.body.classList.add("image-viewer-open");
}

function closeImageViewer() {
  const viewer = $("#imageViewer");
  viewer.classList.remove("active");
  viewer.setAttribute("aria-hidden", "true");
  $("#imageViewerImg").removeAttribute("src");
  document.body.classList.remove("image-viewer-open");
}

async function loadComments(taskId) {
  const list = $("#commentsList");
  list.innerHTML = "Загрузка...";
  const comments = await api(`/tasks/${taskId}/comments`);
  list.innerHTML = "";
  comments.forEach(c => {
    const kind = c.kind || "message";
    const div = document.createElement("div");
    div.className = "comment-item" + (kind !== "message" ? ` comment-${kind}` : "");
    const label = COMMENT_KIND_LABELS[kind]
      ? `<span class="comment-kind comment-kind-${kind}">${COMMENT_KIND_LABELS[kind]}</span>`
      : "";
    div.innerHTML =
      `<div class="comment-head"><span class="comment-author">${escapeHtml(c.author)}</span>${label}</div>` +
      (c.text ? `<div class="comment-text">${escapeHtml(c.text)}</div>` : "") +
      commentImagesHtml(c.images);
    list.appendChild(div);
  });
  if (!comments.length) list.innerHTML = "<div class='muted'>Нет комментариев</div>";
  list.scrollTop = list.scrollHeight;
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
  const fields = ["#taskTitle","#taskDesc","#taskPriority","#taskCategory","#taskBoard","#taskAgent","#taskAffectedFiles"];
  fields.forEach(sel => {
    const el = $(sel);
    el.addEventListener("input", () => { dirty = true; }, { once: false });
    el.addEventListener("change", () => { dirty = true; });
  });
}

/* ---------- Markdown ---------- */
// Small dependency-free renderer: headings (ATX + setext), fenced code, lists
// (nested, ordered, task lists), tables, blockquotes, rules, links, images,
// autolinks and inline emphasis. Everything is HTML-escaped before rendering.
function renderMarkdown(text) {
  const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
  return renderBlocks(lines.map((t, idx) => ({ text: t, line: idx })));
}

function leadingIndent(s) {
  return (s.match(/^\s*/)[0] || "").replace(/\t/g, "    ").length;
}

function matchListItem(line) {
  const m = line.match(/^(\s*)([-+*]|\d{1,9}[.)])\s+([\s\S]*)$/);
  if (!m) return null;
  const indent = m[1].replace(/\t/g, "    ").length;
  const ordered = /\d/.test(m[2][0]);
  return {
    indent,
    ordered,
    marker: m[2],
    number: ordered ? parseInt(m[2], 10) : null,
    content: m[3],
  };
}

function splitTableRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const cells = [];
  let cur = "";
  for (let k = 0; k < s.length; k++) {
    if (s[k] === "\\" && s[k + 1] === "|") { cur += "|"; k++; continue; }
    if (s[k] === "|") { cells.push(cur.trim()); cur = ""; continue; }
    cur += s[k];
  }
  cells.push(cur.trim());
  return cells;
}

function isTableDelimiter(line) {
  if (!line || !line.includes("-") || !line.includes("|")) return false;
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every(c => /^:?-{1,}:?$/.test(c));
}

function tableAlign(cell) {
  const left = cell.startsWith(":");
  const right = cell.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  if (left) return "left";
  return "";
}

function alignAttr(align) {
  return align ? ` style="text-align:${align}"` : "";
}

function isTableStart(rows, i) {
  return i + 1 < rows.length
    && rows[i].text.includes("|")
    && isTableDelimiter(rows[i + 1].text);
}

function isBlockStart(rows, i, first) {
  const line = rows[i].text;
  const trimmed = line.trim();
  if (!trimmed) return true;
  if (/^\s{0,3}(`{3,}|~{3,})/.test(line)) return true;
  if (/^#{1,6}\s+/.test(trimmed)) return true;
  if (/^\s{0,3}>/.test(line)) return true;
  if (/^ {0,3}((\*\s*){3,}|(-\s*){3,}|(_\s*){3,})$/.test(line)) return true;
  if (matchListItem(line)) return true;
  if (isTableStart(rows, i)) return true;
  if (!first && i + 1 < rows.length && /^ {0,3}(=+|-+)\s*$/.test(rows[i + 1].text)) return true;
  return false;
}

function renderBlocks(rows) {
  let html = "";
  let i = 0;
  while (i < rows.length) {
    const line = rows[i].text;
    const trimmed = line.trim();

    if (trimmed === "") { i++; continue; }

    // Fenced code block (``` or ~~~ with optional language)
    const fence = line.match(/^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/);
    if (fence) {
      const marker = fence[1][0];
      const minLen = fence[1].length;
      const lang = fence[2] || "";
      const buf = [];
      i++;
      while (i < rows.length) {
        const closing = rows[i].text.trim();
        if (closing.length >= minLen && new RegExp(`^${marker}+$`).test(closing)) { i++; break; }
        buf.push(rows[i].text);
        i++;
      }
      const cls = lang ? ` class="language-${escapeHtml(lang)}"` : "";
      html += `<pre><code${cls}>${escapeHtml(buf.join("\n"))}</code></pre>`;
      continue;
    }

    // Setext heading (Title / ===== or -----)
    if (i + 1 < rows.length && /^ {0,3}(=+|-+)\s*$/.test(rows[i + 1].text)) {
      const level = rows[i + 1].text.trim()[0] === "=" ? 1 : 2;
      html += `<h${level}>${inlineMd(trimmed)}</h${level}>`;
      i += 2;
      continue;
    }

    // Horizontal rule
    if (/^ {0,3}((\*\s*){3,}|(-\s*){3,}|(_\s*){3,})$/.test(line)) {
      html += "<hr>";
      i++;
      continue;
    }

    // ATX heading (# .. ######)
    const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      const level = heading[1].length;
      html += `<h${level}>${inlineMd(heading[2])}</h${level}>`;
      i++;
      continue;
    }

    // Table
    if (isTableStart(rows, i)) {
      const header = splitTableRow(line);
      const aligns = splitTableRow(rows[i + 1].text).map(tableAlign);
      i += 2;
      let body = "";
      while (
        i < rows.length
        && rows[i].text.trim() !== ""
        && rows[i].text.includes("|")
        && !isBlockStart(rows, i, true)
      ) {
        const cells = splitTableRow(rows[i].text);
        body += `<tr>${header.map((_, k) =>
          `<td${alignAttr(aligns[k])}>${inlineMd(cells[k] ?? "")}</td>`).join("")}</tr>`;
        i++;
      }
      html += `<div class="md-table-wrap"><table><thead><tr>${header.map((c, k) =>
        `<th${alignAttr(aligns[k])}>${inlineMd(c)}</th>`).join("")}</tr></thead>`
        + `<tbody>${body}</tbody></table></div>`;
      continue;
    }

    // Blockquote
    if (/^\s{0,3}>/.test(line)) {
      const inner = [];
      while (i < rows.length) {
        if (/^\s{0,3}>/.test(rows[i].text)) {
          inner.push({ text: rows[i].text.replace(/^\s{0,3}>\s?/, ""), line: rows[i].line });
          i++;
          continue;
        }
        // keep blank lines that are still inside the quote
        if (rows[i].text.trim() === "" && i + 1 < rows.length && /^\s{0,3}>/.test(rows[i + 1].text)) {
          inner.push({ text: "", line: rows[i].line });
          i++;
          continue;
        }
        break;
      }
      html += `<blockquote>${renderBlocks(inner)}</blockquote>`;
      continue;
    }

    // Lists (bullet, ordered, nested, task lists)
    if (matchListItem(line)) {
      const list = parseList(rows, i);
      html += list.html;
      i = list.next;
      continue;
    }

    // Paragraph: consecutive plain lines, hard-wrapped with <br>
    const para = [];
    while (i < rows.length) {
      if (para.length && isBlockStart(rows, i, false)) break;
      para.push(rows[i].text);
      i++;
    }
    if (para.length) html += `<p>${inlineMd(para.join("\n"))}</p>`;
  }
  return html;
}

function parseList(rows, start) {
  const first = matchListItem(rows[start].text);
  const baseIndent = first.indent;
  const ordered = first.ordered;
  let i = start;
  const items = [];

  while (i < rows.length) {
    const m = matchListItem(rows[i].text);
    if (!m || m.indent !== baseIndent || m.ordered !== ordered) break;

    const itemRows = [{ text: m.content, line: rows[i].line }];
    i++;

    while (i < rows.length) {
      const raw = rows[i].text;
      if (raw.trim() === "") {
        let j = i + 1;
        while (j < rows.length && rows[j].text.trim() === "") j++;
        if (j < rows.length) {
          const nm = matchListItem(rows[j].text);
          const ind = leadingIndent(rows[j].text);
          if ((nm && nm.indent > baseIndent) || (!nm && ind > baseIndent)) {
            itemRows.push({ text: "", line: rows[i].line });
            i++;
            continue;
          }
        }
        break;
      }
      const nm = matchListItem(raw);
      const ind = leadingIndent(raw);
      if (nm && nm.indent > baseIndent) {
        itemRows.push({ text: raw.slice(Math.min(ind, baseIndent + 2)), line: rows[i].line });
        i++;
        continue;
      }
      if (!nm && ind > baseIndent) {
        itemRows.push({ text: raw.slice(Math.min(ind, baseIndent + 2)), line: rows[i].line });
        i++;
        continue;
      }
      break;
    }

    items.push(renderListItem(itemRows));
  }

  const tag = ordered ? "ol" : "ul";
  const startAttr = ordered && first.number > 1 ? ` start="${first.number}"` : "";
  return { html: `<${tag}${startAttr}>${items.join("")}</${tag}>`, next: i };
}

function renderListItem(itemRows) {
  const first = itemRows[0];
  const task = first.text.match(/^\[([ xX])\]\s+([\s\S]*)$/);
  if (task) {
    const checked = task[1].toLowerCase() === "x";
    let content;
    if (itemRows.length === 1) {
      content = inlineMd(task[2]);
    } else {
      const copy = itemRows.slice();
      copy[0] = { text: task[2], line: first.line };
      content = renderBlocks(copy);
    }
    return `<li class="md-task-item"><label class="md-checkbox">`
      + `<input type="checkbox" data-line="${first.line}"${checked ? " checked" : ""}>`
      + `<span>${content}</span></label></li>`;
  }
  if (itemRows.length === 1) return `<li>${inlineMd(first.text)}</li>`;
  return `<li>${renderBlocks(itemRows)}</li>`;
}

function safeUrl(url) {
  const raw = String(url ?? "")
    .trim()
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
  if (/^(?:javascript|vbscript|data):/i.test(raw)) return "#";
  return escapeHtml(raw);
}

function mdEmphasis(t) {
  t = t.replace(/\*\*\*([^*\n]+)\*\*\*/g, "<strong><em>$1</em></strong>");
  t = t.replace(/___([^_\n]+)___/g, "<strong><em>$1</em></strong>");
  t = t.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
  t = t.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
  t = t.replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s.,;:!?)\]])/g, "$1<em>$2</em>");
  t = t.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  t = t.replace(/==([^=\n]+)==/g, "<mark>$1</mark>");
  return t;
}

function inlineMd(src) {
  const tokens = [];
  const stash = (html) => `\u0001${tokens.push(html) - 1}\u0001`;
  let t = String(src ?? "");

  // Backslash escapes
  t = t.replace(/\\([\\`*_{}\[\]()#+\-.!>~|])/g, (m, ch) => stash(escapeHtml(ch)));

  // Inline code
  t = t.replace(/(`+)([\s\S]*?)\1/g, (m, ticks, code) =>
    stash(`<code>${escapeHtml(code.replace(/^ | $/g, ""))}</code>`));

  // Images (before links, so ![alt](src) is not treated as a link)
  t = t.replace(/!\[([^\]]*)\]\(\s*([^\s)]+)(?:\s+["']([^"']*)["'])?\s*\)/g,
    (m, alt, url) => stash(`<img src="${safeUrl(url)}" alt="${escapeHtml(alt)}">`));

  // Links
  t = t.replace(/\[([^\]]+)\]\(\s*([^\s)]+)(?:\s+["']([^"']*)["'])?\s*\)/g,
    (m, text, url, title) => {
      const ttl = title ? ` title="${escapeHtml(title)}"` : "";
      return stash(`<a href="${safeUrl(url)}"${ttl} target="_blank" rel="noopener noreferrer">`
        + `${mdEmphasis(escapeHtml(text))}</a>`);
    });

  // Escape the remaining source, then format
  t = escapeHtml(t);
  t = mdEmphasis(t);

  // Autolinks <https://...> / <mailto:...>
  t = t.replace(/&lt;((?:https?:\/\/|mailto:)[^\s&<>]+)&gt;/g, (m, url) =>
    `<a href="${safeUrl(url)}" target="_blank" rel="noopener noreferrer">${url}</a>`);

  // Bare URLs
  t = t.replace(/(^|[\s(])(https?:\/\/[^\s<"']+)/g, (m, pre, url) => {
    let trail = "";
    const trailMatch = url.match(/[.,;:!?)]+$/);
    if (trailMatch) {
      trail = trailMatch[0];
      url = url.slice(0, -trail.length);
    }
    return `${pre}<a href="${safeUrl(url)}" target="_blank" rel="noopener noreferrer">${url}</a>${trail}`;
  });

  t = t.replace(/\n/g, "<br>");
  return t.replace(/\u0001(\d+)\u0001/g, (m, idx) => tokens[Number(idx)] ?? "");
}

function toggleCheckboxInDesc(lineIndex) {
  const ta = $("#taskDesc");
  const lines = ta.value.split("\n");
  const line = lines[lineIndex];
  const m = line.match(/^(\s*(?:[-*+]|\d{1,9}[.)])\s*\[)([ xX])(\]\s*[\s\S]*)$/);
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
  const boardValue = $("#taskBoard").value;
  const payload = {
    title: $("#taskTitle").value.trim(),
    description: $("#taskDesc").value,
    priority: $("#taskPriority").value,
    category: $("#taskCategory").value,
    board_id: boardValue ? parseInt(boardValue, 10) : null,
    column_id: columnId,
    agent_id: $("#taskAgent").value.trim() || null,
    affected_files: $("#taskAffectedFiles").value,
  };
  if (!id) {
    payload.acceptance_criteria = pendingCriteria.map(c => ({
      text: c.text,
      done: Boolean(c.done),
      evidence: c.evidence || "",
    }));
  }
  if (!payload.title) { alert("Укажите название"); return; }
  if (!payload.board_id) { alert("Выберите доску для задачи"); return; }
  try {
    if (id) {
      await api(`/tasks/${id}`, { method: "PUT", body: JSON.stringify(payload) });
      dirty = false;
      await loadTasks();
      await loadBoards();
      closeModal();
    } else {
      await api("/tasks", { method: "POST", body: JSON.stringify({ ...payload, project_id: currentProjectId }) });
      dirty = false;
      await loadTasks();
      await loadBoards();
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

/* ---------- Bulk selection ---------- */
function renderBulkColumns() {
  const sel = $("#bulkColumn");
  if (!sel) return;
  const previous = sel.value;
  sel.innerHTML = "";
  columns.forEach(col => {
    const opt = document.createElement("option");
    opt.value = String(col.id);
    opt.textContent = col.name;
    sel.appendChild(opt);
  });
  if (previous && Array.from(sel.options).some(o => o.value === previous)) {
    sel.value = previous;
  }
}

function updateBulkBar() {
  const bar = $("#bulkBar");
  if (!bar) return;
  const count = selectedTaskIds.size;
  bar.style.display = count ? "flex" : "none";
  const label = $("#bulkCount");
  if (label) label.textContent = `Выбрано: ${count}`;
}

function toggleTaskSelection(id, selected) {
  if (selected) selectedTaskIds.add(id);
  else selectedTaskIds.delete(id);
  const card = document.querySelector(`.card[data-task-id="${id}"]`);
  if (card) {
    card.classList.toggle("selected", selected);
    const box = card.querySelector(".card-select");
    if (box) box.checked = selected;
  }
  updateBulkBar();
}

function clearSelection() {
  selectedTaskIds.clear();
  document.querySelectorAll(".card.selected").forEach(card => card.classList.remove("selected"));
  document.querySelectorAll(".card-select").forEach(box => { box.checked = false; });
  updateBulkBar();
}

async function bulkMoveSelected() {
  const columnId = parseInt($("#bulkColumn").value, 10);
  if (!columnId || !selectedTaskIds.size) return;
  const ids = Array.from(selectedTaskIds);
  let moved = 0;
  const failed = [];
  for (const id of ids) {
    try {
      await api(`/tasks/${id}/move`, {
        method: "POST",
        body: JSON.stringify({ column_id: columnId }),
      });
      moved += 1;
    } catch (err) {
      failed.push(`#${id}: ${err.message}`);
    }
  }
  clearSelection();
  await loadTasks();
  alert(
    `Перенесено: ${moved}` +
    (failed.length ? `\nНе удалось (${failed.length}):\n${failed.slice(0, 8).join("\n")}` : "")
  );
}

async function bulkArchiveSelected() {
  if (!selectedTaskIds.size) return;
  if (!confirm(`Архивировать выбранные задачи (${selectedTaskIds.size})?`)) return;
  const ids = Array.from(selectedTaskIds);
  let archived = 0;
  const failed = [];
  for (const id of ids) {
    try {
      await api(`/tasks/${id}/archive`, { method: "POST" });
      archived += 1;
    } catch (err) {
      failed.push(`#${id}: ${err.message}`);
    }
  }
  clearSelection();
  await loadTasks();
  alert(
    `В архив: ${archived}` +
    (failed.length ? `\nНе удалось (${failed.length}):\n${failed.slice(0, 8).join("\n")}` : "")
  );
}

/* ---------- Saved views ---------- */
function viewsKey() {
  return `kanbanViews:${currentProjectId}`;
}

function loadViews() {
  try {
    const raw = localStorage.getItem(viewsKey());
    const data = raw ? JSON.parse(raw) : [];
    return Array.isArray(data) ? data : [];
  } catch (err) { return []; }
}

function saveViews(views) {
  localStorage.setItem(viewsKey(), JSON.stringify(views));
}

function renderViews() {
  const sel = $("#viewSelect");
  if (!sel) return;
  const views = loadViews();
  const previous = sel.value;
  sel.innerHTML = '<option value="">Все задачи</option>';
  views.forEach((view, index) => {
    const opt = document.createElement("option");
    opt.value = String(index);
    opt.textContent = view.name;
    sel.appendChild(opt);
  });
  if (previous && views[Number(previous)]) sel.value = previous;
}

function setMultiSelected(id, values) {
  const panel = $(`#${id} .ms-panel`);
  if (!panel) return;
  const wanted = new Set(values || []);
  panel.querySelectorAll("input").forEach(input => {
    input.checked = wanted.has(input.value);
  });
  if (typeof panel.onchange === "function") panel.onchange();
}

function currentView(name) {
  return {
    name,
    q: $("#searchInput").value || "",
    cats: getMultiSelected("catFilter"),
    prios: getMultiSelected("prioFilter"),
    archived: Boolean($("#showArchived") && $("#showArchived").checked),
  };
}

function applyView(view) {
  $("#searchInput").value = view.q || "";
  setMultiSelected("catFilter", view.cats || []);
  setMultiSelected("prioFilter", view.prios || []);
  if ($("#showArchived")) $("#showArchived").checked = Boolean(view.archived);
  loadTasks();
}

function saveCurrentView() {
  const name = (prompt("Название вида:") || "").trim();
  if (!name) return;
  const views = loadViews().filter(view => view.name !== name);
  views.push(currentView(name));
  saveViews(views);
  renderViews();
  $("#viewSelect").value = String(views.length - 1);
}

function deleteSelectedView() {
  const sel = $("#viewSelect");
  const index = Number(sel.value);
  if (!sel.value) { alert("Выберите сохранённый вид"); return; }
  const views = loadViews();
  const view = views[index];
  if (!view) return;
  if (!confirm(`Удалить вид «${view.name}»?`)) return;
  views.splice(index, 1);
  saveViews(views);
  sel.value = "";
  renderViews();
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
  try {
    const res = await api(`/analyze?project_id=${currentProjectId}`);
    const s = res.suggestion || {};
    let msg;
    if (s.recommendation) {
      msg = `Рекомендация: #${s.recommendation.id} ${s.recommendation.title}\n`
        + `Причина: ${s.reason || "приоритет и стадия"}`;
    } else {
      msg = `Рекомендация: ${s.reason || "подходящих задач нет"}`;
    }
    if (res.stalled_tasks && res.stalled_tasks.length) {
      msg += `\n\nЗависших задач без активности >24ч (High/Critical): ${res.stalled_tasks.length}`;
      const names = res.stalled_tasks.slice(0, 5)
        .map(t => `#${t.id} ${t.title}`)
        .join("\n");
      if (names) msg += `\n${names}`;
      if (res.stalled_tasks.length > 5) msg += "\n…";
    }
    alert(msg);
  } catch (err) {
    alert(`Не удалось выполнить анализ: ${err.message}`);
  }
}

/* ---------- Init ---------- */
document.addEventListener("DOMContentLoaded", async () => {
  try {
    columnSorts = JSON.parse(localStorage.getItem("kanbanColumnSorts") || "{}") || {};
  } catch (e) {
    columnSorts = {};
  }
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
  $("#btnArchiveTask").addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleArchiveCurrentTask();
  });
  $("#btnVerifyTask").addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    verifyCurrentTask();
  });
  $("#btnRejectTask").addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    rejectCurrentTask();
  });
  $("#btnConfirmReview").addEventListener("click", (e) => {
    e.preventDefault();
    submitReview();
  });
  $("#btnCancelReview").addEventListener("click", (e) => {
    e.preventDefault();
    closeReviewModal();
  });
  $("#reviewModalClose").addEventListener("click", (e) => {
    e.preventDefault();
    closeReviewModal();
  });
  $("#reviewNote").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submitReview();
    }
  });
  $("#btnAttachReview").addEventListener("click", () => $("#reviewImageInput").click());
  $("#reviewImageInput").addEventListener("change", (e) => {
    addAttachmentFiles(e.target.files, reviewAttachments, renderReviewAttachments);
    e.target.value = "";
  });
  $("#reviewNote").addEventListener("input", () =>
    autoResizeTextarea($("#reviewNote"), window.innerHeight * 0.45));
  $("#btnTaskTemplate").addEventListener("click", (e) => {
    e.preventDefault();
    insertTaskTemplate();
  });
  $("#btnAddCriterion").addEventListener("click", (e) => {
    e.preventDefault();
    addCriterion();
  });
  $("#newCriterionText").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addCriterion();
    }
  });
  $("#showArchived").addEventListener("change", () => { loadTasks(); });
  $("#viewSelect").addEventListener("change", (e) => {
    const value = e.target.value;
    if (value === "") {
      applyView({ q: "", cats: [], prios: [], archived: false });
      return;
    }
    const view = loadViews()[Number(value)];
    if (view) applyView(view);
  });
  $("#btnSaveView").addEventListener("click", saveCurrentView);
  $("#btnDeleteView").addEventListener("click", deleteSelectedView);
  $("#btnBulkMove").addEventListener("click", bulkMoveSelected);
  $("#btnBulkArchive").addEventListener("click", bulkArchiveSelected);
  $("#btnBulkClear").addEventListener("click", clearSelection);
  $("#btnFullscreen").addEventListener("click", () => toggleFullscreen());

  $("#btnTabDesc").addEventListener("click", () => setTaskTab("desc"));
  $("#btnTabCriteria").addEventListener("click", () => setTaskTab("criteria"));
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
    if (attachmentsUploading(commentAttachments)) {
      alert("Дождитесь загрузки изображений");
      return;
    }
    if (attachmentsFailed(commentAttachments)) {
      alert("Уберите изображения, которые не удалось загрузить");
      return;
    }
    const images = attachmentUrls(commentAttachments);
    if (!text && !images.length) return;
    try {
      await api(`/tasks/${id}/comments`, {
        method: "POST",
        body: JSON.stringify({ author: "User", text, images }),
      });
    } catch (err) { alert(err.message); return; }
    $("#commentText").value = "";
    $("#commentText").style.height = "auto";
    commentAttachments = [];
    renderCommentAttachments();
    await loadComments(id);
    await loadTasks();
  });

  // Enter sends; Shift+Enter inserts a newline (the input is a textarea).
  $("#commentText").addEventListener("input", () => {
    autoResizeTextarea($("#commentText"), 140);
  });
  $("#commentText").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("#btnAddComment").click();
    }
  });

  $("#btnAttachComment").addEventListener("click", () => $("#commentImageInput").click());
  $("#commentImageInput").addEventListener("change", (e) => {
    addAttachmentFiles(e.target.files, commentAttachments, renderCommentAttachments);
    e.target.value = "";
  });

  // Fullscreen image viewer (comment photos and attachment previews)
  $("#imageViewerClose").addEventListener("click", closeImageViewer);
  $("#imageViewer").addEventListener("click", (e) => {
    if (e.target.id === "imageViewer") closeImageViewer();
  });
  document.addEventListener("click", (e) => {
    const trigger = e.target.closest("[data-image-viewer]");
    if (!trigger) return;
    e.preventDefault();
    openImageViewer(trigger.dataset.imageViewer);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("#imageViewer").classList.contains("active")) {
      e.preventDefault();
      closeImageViewer();
    }
  });

  // Ctrl+V anywhere in an open window attaches the clipboard image:
  // review modal -> review attachments, task modal -> comment attachments.
  document.addEventListener("paste", (e) => {
    if ($("#reviewModal").classList.contains("active")) {
      pasteImagesInto(e, reviewAttachments, renderReviewAttachments);
    } else if ($("#taskModal").classList.contains("active")) {
      pasteImagesInto(e, commentAttachments, renderCommentAttachments);
    }
  });

  // column modal
  $("#columnModalClose").addEventListener("click", closeColumnModal);
  $("#btnSaveColumn").addEventListener("click", saveColumn);
  $("#btnDeleteColumn").addEventListener("click", deleteColumn);
  // Modals close ONLY via their explicit buttons (✕ / Save / Delete / Cancel ...).
  // Clicking the dimmed backdrop must never discard the user's work.

  $("#settingsClose").addEventListener("click", closeSettings);
  // Let clicks inside the dialog stay inside; the backdrop is inert.
  $("#taskModalDialog").addEventListener("click", (e) => e.stopPropagation());

  trackDirty();

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".column-menu") && !e.target.closest(".ms")) closeMenus();
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
