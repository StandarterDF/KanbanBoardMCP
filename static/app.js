const API = "/api";
let columns = [];
let tasks = [];
let pollTimer = null;
let draggedTaskId = null;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || res.statusText);
  }
  return res.json();
}

async function loadColumns() {
  columns = await api("/columns");
}

async function loadTasks() {
  tasks = await api("/tasks");
  renderBoard();
}

function renderBoard() {
  const board = $("#board");
  board.innerHTML = "";
  columns.forEach(col => {
    const colEl = document.createElement("div");
    colEl.className = "column";
    colEl.dataset.columnId = col.id;
    colEl.innerHTML = `
      <div class="column-header">
        <span><span class="column-color" style="background:${col.color}"></span>${col.name}</span>
        <span class="muted">${tasks.filter(t=>t.column_id===col.id).length}</span>
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
        await api(`/tasks/${draggedTaskId}/move`, {
          method: "POST",
          body: JSON.stringify({ column_id: targetCol }),
        });
        await loadTasks();
      } catch (err) { alert(err.message); }
      draggedTaskId = null;
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
      card.addEventListener("dragstart", () => { draggedTaskId = t.id; });
      card.addEventListener("dragend", () => { draggedTaskId = null; });
      card.addEventListener("click", () => openTaskModal(t.id));
      body.appendChild(card);
    });
    board.appendChild(colEl);
  });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

async function openTaskModal(taskId) {
  // taskId null => new
  $("#taskId").value = taskId ?? "";
  if (taskId) {
    const t = await api(`/tasks/${taskId}`);
    $("#taskTitle").value = t.title;
    $("#taskDesc").value = t.description || "";
    $("#taskPriority").value = t.priority;
    $("#taskCategory").value = t.category;
    $("#modalTitle").textContent = `Задача #${t.id}`;
    // comments
    await loadComments(taskId);
  } else {
    $("#taskTitle").value = "";
    $("#taskDesc").value = "";
    $("#taskPriority").value = "Medium";
    $("#taskCategory").value = "Feature";
    $("#modalTitle").textContent = "Новая задача";
    $("#commentsList").innerHTML = "";
  }
  // columns select
  const sel = $("#taskColumn");
  sel.innerHTML = "";
  columns.forEach(c => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.name;
    sel.appendChild(opt);
  });
  if (taskId) {
    const t = tasks.find(x => x.id === taskId);
    if (t) sel.value = t.column_id;
  } else {
    sel.value = columns[0]?.id;
  }
  $("#taskModal").classList.add("active");
}

function closeModal() {
  $("#taskModal").classList.remove("active");
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

async function saveTask() {
  const id = $("#taskId").value;
  const payload = {
    title: $("#taskTitle").value.trim(),
    description: $("#taskDesc").value.trim(),
    priority: $("#taskPriority").value,
    category: $("#taskCategory").value,
    column_id: parseInt($("#taskColumn").value),
  };
  if (!payload.title) { alert("Укажите название"); return; }
  try {
    if (id) {
      await api(`/tasks/${id}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/tasks", { method: "POST", body: JSON.stringify(payload) });
    }
    closeModal();
    await loadTasks();
  } catch (err) { alert(err.message); }
}

async function deleteTask() {
  const id = $("#taskId").value;
  if (!id) { closeModal(); return; }
  if (!confirm("Удалить задачу?")) return;
  try {
    await api(`/tasks/${id}`, { method: "DELETE" });
    closeModal();
    await loadTasks();
  } catch (err) { alert(err.message); }
}

// Settings
async function openSettings() {
  $("#settingsDrawer").classList.add("open");
  $("#settingsOverlay").classList.add("active");
  const settings = await api("/settings");
  const body = $("#settingsBody");
  body.innerHTML = "";
  settings.forEach(s => {
    const row = document.createElement("div");
    row.className = "setting-row";
    row.innerHTML = `
      <div>
        <div><strong>${s.key}</strong></div>
        <div class="desc">${s.description}</div>
      </div>
      <div class="toggle ${s.is_enabled ? "on" : ""}" data-key="${s.key}"></div>
    `;
    row.querySelector(".toggle").addEventListener("click", async (e) => {
      const key = e.target.dataset.key;
      const enabled = !e.target.classList.contains("on");
      await api(`/settings/${key}`, { method: "PUT", body: JSON.stringify({ is_enabled: enabled }) });
      e.target.classList.toggle("on", enabled);
    });
    body.appendChild(row);
  });
}
function closeSettings() {
  $("#settingsDrawer").classList.remove("open");
  $("#settingsOverlay").classList.remove("active");
}

// Analyze
async function analyze() {
  const res = await api("/analyze");
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

// Events
document.addEventListener("DOMContentLoaded", async () => {
  await loadColumns();
  await loadTasks();
  $("#btnNewTask").addEventListener("click", () => openTaskModal(null));
  $("#btnSettings").addEventListener("click", openSettings);
  $("#btnAnalyze").addEventListener("click", analyze);
  $("#modalClose").addEventListener("click", closeModal);
  $("#btnSaveTask").addEventListener("click", saveTask);
  $("#btnDeleteTask").addEventListener("click", deleteTask);
  $("#btnAddComment").addEventListener("click", async () => {
    const id = $("#taskId").value;
    if (!id) return;
    const text = $("#commentText").value.trim();
    if (!text) return;
    await api(`/tasks/${id}/comments`, { method: "POST", body: JSON.stringify({ author: "User", text }) });
    $("#commentText").value = "";
    await loadComments(id);
  });
  $("#settingsClose").addEventListener("click", closeSettings);
  $("#settingsOverlay").addEventListener("click", closeSettings);
  $("#taskModal").addEventListener("click", (e) => { if (e.target.id === "taskModal") closeModal(); });

  // polling
  pollTimer = setInterval(async () => {
    try { await loadTasks(); } catch (e) {}
  }, 2500);
});
