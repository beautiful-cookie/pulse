"use strict";

const view = document.getElementById("view");
const SOURCE_TYPES = { rss: "Новости", tg_public: "Каналы Telegram", tg_private: "Чаты Telegram" };
const ICONS = {
  today: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  feed: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  chart: '<path d="M5 20V11M12 20V4M19 20v-7"/>',
  spark: '<path d="M12 4l1.8 5.2L19 11l-5.2 1.8L12 18l-1.8-5.2L5 11l5.2-1.8z"/>',
  sliders: '<path d="M4 7h9M19 7h1M4 17h1M11 17h9"/><circle cx="16" cy="7" r="2.5"/><circle cx="8" cy="17" r="2.5"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 6.3"/><path d="M20 5v6h-6"/>',
  check: '<path d="M5 13l4 4L19 7"/>',
  open: '<path d="M14 5h5v5M19 5l-9 9M11 5H6a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5"/>',
  up: '<path d="M12 19V6M6 12l6-6 6 6"/>',
  down: '<path d="M12 5v13M6 12l6 6 6-6"/>',
  star: '<path d="M12 4l2.4 5 5.6.8-4 3.9.9 5.5-4.9-2.6-4.9 2.6.9-5.5-4-3.9 5.6-.8z"/>',
  shield: '<path d="M12 3l7 3v6c0 5-3 8-7 9-4-1-7-4-7-9V6z"/>',
  moon: '<path d="M20 14A8 8 0 1 1 10 4a7 7 0 0 0 10 10z"/>',
  chev: '<path d="M9 6l6 6-6 6"/>',
  note: '<path d="M7 3h8l4 4v14H7zM15 3v4h4"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/>',
  task: '<path d="M9 7h11M9 12h11M9 17h11M4 7h.01M4 12h.01M4 17h.01"/>',
  repeat: '<path d="M4 11a8 8 0 0 1 14-5l2 2M20 13a8 8 0 0 1-14 5l-2-2"/><path d="M20 4v4h-4M4 20v-4h4"/>',
  layers: '<path d="M12 4l9 5-9 5-9-5zM3 14l9 5 9-5"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  trash: '<path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><path d="M12 12h.01"/>',
  calendar: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
  left: '<path d="M15 6l-6 6 6 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
};
let topicNames = {};
let topicOrder = [];
let today = null;
let activeTopic = "";
let routeToken = 0;
let openBrief = false;
let freshShown = false;
let userRun = false;
const taskIndex = {};
const doneIndex = {};

const calmMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

function vanish(el) {
  // Строка не исчезает рывком: сжимается, и соседние плавно поднимаются на её место
  if (!el) return Promise.resolve();
  if (calmMotion()) { el.remove(); return Promise.resolve(); }
  return new Promise((done) => {
    el.style.height = `${el.offsetHeight}px`;
    el.classList.add("leaving");
    requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add("gone")));
    setTimeout(() => { el.remove(); done(); }, 340);
  });
}

function enter() {
  // Появление только при переходе между разделами: обновление данных не должно дёргать страницу
  if (calmMotion()) return;
  view.classList.remove("enter");
  void view.offsetWidth;
  view.classList.add("enter");
  clearTimeout(enter.timer);
  enter.timer = setTimeout(() => view.classList.remove("enter"), 900);
}

const icon = (name, cls = "") => `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^(https?:|obsidian:|tg:)/i.test(u || "") ? u : "");
const topicColor = (id) => { const i = topicOrder.indexOf(id); return i >= 0 && i < 8 ? `var(--s${i})` : "var(--line-2)"; };

document.querySelectorAll("[data-icon]").forEach((el) => el.insertAdjacentHTML("afterbegin", icon(el.dataset.icon)));

async function api(path, body) {
  const options = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let response;
  try { response = await fetch(path, options); } catch {
    throw new Error("Пульс не отвечает. Если окно Пульса закрыто, запустите start.bat и обновите страницу");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Ошибка ${response.status}`);
  return data;
}

function toast(text, actionLabel, action) {
  const el = document.getElementById("toast");
  el.textContent = text;
  if (actionLabel) {
    const button = document.createElement("button");
    button.textContent = actionLabel;
    button.onclick = () => { el.classList.remove("on"); action(); };
    el.append(button);
  }
  el.classList.add("on");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("on"), actionLabel ? 7000 : 2600);
}

function ago(iso) {
  const minutes = Math.round((Date.now() - new Date(iso)) / 60000);
  if (minutes < 60) return `${Math.max(minutes, 1)} мин`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ч`;
  return `${Math.round(hours / 24)} дн`;
}

function dayDiff(iso) {
  const start = new Date(); start.setHours(0, 0, 0, 0);
  return Math.round((new Date(`${iso}T00:00:00`) - start) / 864e5);
}

function whenLabel(iso) {
  const diff = dayDiff(iso);
  if (diff < 0) return { text: `${diff} дн`.replace("-", "−"), tone: "bad" };
  if (diff === 0) return { text: "сегодня", tone: "bad" };
  if (diff === 1) return { text: "завтра", tone: "bad" };
  const date = new Date(`${iso}T00:00:00`);
  if (diff < 7) return { text: date.toLocaleDateString("ru-RU", { weekday: "short" }), tone: "warn" };
  return { text: date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", ""), tone: "" };
}

function newsRow(item) {
  const url = safeUrl(item.url);
  const topic = item.topics[0];
  const flags = [
    item.meta?.primary ? `<span class="flag" title="Первоисточник">${icon("star")}</span>` : "",
    item.cluster_size > 1 ? `<span class="flag" title="Пишут ${item.cluster_size} источника">${icon("layers")}${item.cluster_size}</span>` : "",
    item.links?.length ? `<span class="flag" title="Есть заметка">${icon("note")}</span>` : "",
  ].join("");
  const notes = (item.links || []).map((n) => `<a class="link" href="obsidian://search?query=${encodeURIComponent(n)}">${esc(n)}</a>`).join(", ");
  const topics = item.topics.map((t) => topicNames[t]).filter(Boolean);
  return `<article class="news ${esc(item.tier)}" data-id="${item.id}" title="">
    <span class="dot" style="--dot:${topic ? topicColor(topic) : "var(--line-2)"}" title="${esc(topicNames[topic] || "")}"></span>
    <div>
      <button class="news-title" data-expand aria-expanded="false">${esc(item.title)}</button>
      <div class="news-meta"><span>${esc(item.source_name)}</span><span>${esc(ago(item.published))}</span>${flags}</div>
    </div>
    <div class="news-tools">
      ${url ? `<a class="tool" href="${esc(url)}" target="_blank" rel="noreferrer noopener" title="Открыть">${icon("open")}</a>` : ""}
      <button class="tool" data-act="down" title="Меньше такого" aria-label="Меньше такого">${icon("down")}</button>
      <button class="tool" data-act="seen" title="Прочитано" aria-label="Прочитано">${icon("check")}</button>
    </div>
    <div class="news-more" hidden>
      ${item.body ? `<p>${esc(item.body.slice(0, 420))}</p>` : ""}
      ${notes ? `<p>Заметки: ${notes}</p>` : ""}
      <div class="why">${[...topics, ...item.reasons].map((r) => `<span>${esc(r)}</span>`).join("")}</div>
      <div class="summary-box" hidden></div>
      <div class="acts">
        ${item.source_type === "tg_private" || /^https:\/\/t\.me\/c\//.test(item.url || "") ? "" : `<button data-summary>${icon("note")}<span>Саммари</span></button>
        <button data-summary="claude" title="Саммари делает Claude: читает статью и находит её сам">${icon("spark")}<span>Claude</span></button>`}
        <button data-act="up" class="${item.feedback > 0 ? "on" : ""}">${icon("up")}Важно</button>
        <button data-act="down" class="${item.feedback < 0 ? "on" : ""}">${icon("down")}Не важно</button>
        <button data-act="queue" class="${item.queued ? "on" : ""}">${icon("star")}<span>${item.queued ? "В разборе" : "Разобрать"}</span></button>
      </div>
    </div>
  </article>`;
}

const isFromChat = (task) => task.meta.heading === "Сроки из чатов";
const taskTitle = (task) => task.title.replace(/\s*\(до \d{1,2}\.\d{2}(\.\d{4})?\)\s*$/, "");
const isEditable = (task) => task.editable ?? Boolean(task.meta.agenda);

function remember(task) {
  taskIndex[task.id] = task;
}

const BASIS = { article: "по полному тексту статьи", feed: "только по анонсу: статью открыть не удалось", search: "статью нашёл в интернете" };

function summaryHtml(summary) {
  if (summary.method === "none" && summary.engine === "claude") return '<p class="empty">Claude не смог прочитать или найти эту статью. Читайте по ссылке.</p>';
  if (summary.method === "none") return '<p class="empty">Здесь слишком мало текста, а статью открыть не удалось. Попробуйте кнопку Claude.</p>';
  const how = { model: "Модель на этом компьютере", claude: "Claude", extract: "Выдержка из статьи, без ИИ" }[summary.method];
  return `<p class="summary-gist">${esc(summary.gist)}</p>
    ${summary.points.length ? `<ul>${summary.points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}
    ${summary.limits ? `<p class="summary-limits">Оговорки: ${esc(summary.limits)}</p>` : ""}
    <p class="summary-source">${esc(how)}, ${esc(BASIS[summary.basis] || "")}</p>`;
}

async function openSummary(button) {
  const card = button.closest("[data-id]");
  const box = card.querySelector(".summary-box");
  const engine = button.dataset.summary || "";
  if (!box.hidden && box.dataset.engine === engine) { box.hidden = true; return; }
  box.hidden = false;
  box.dataset.engine = engine;
  box.innerHTML = `<p class="empty">${engine ? "Claude читает статью, обычно до минуты…" : "Читаю статью, с моделью это около минуты…"}</p>`;
  try {
    for (let attempt = 0; attempt < 120; attempt++) {
      const data = await api(`/api/item/${card.dataset.id}/summary`, engine ? { engine } : {});
      if (data.status === "ready") { box.innerHTML = summaryHtml(data.summary); return; }
      await new Promise((resolve) => setTimeout(resolve, 2500));
      if (!box.isConnected) return;
    }
    box.innerHTML = '<p class="empty">Не дождался ответа. Попробуйте ещё раз.</p>';
  } catch (error) {
    box.innerHTML = `<p class="empty">${esc(error.message)}</p>`;
  }
}

function todoRow(task) {
  remember(task);
  const when = task.due ? whenLabel(task.due) : null;
  const check = isEditable(task)
    ? `<input type="checkbox" class="task-check" data-done="${task.id}" aria-label="Готово: ${esc(task.title)}">`
    : "<span></span>";
  return `<div class="todo">${check}
    <a href="${esc(safeUrl(task.url))}" title="${esc(task.meta.context || task.source_name)}">${isFromChat(task) ? icon("chat", "from-chat") : ""}${esc(taskTitle(task))}</a>
    ${when ? `<span class="when ${when.tone}">${esc(when.text)}</span>` : "<span></span>"}</div>`;
}

async function taskRequest(body) {
  return api("/api/tasks", body);
}

async function completeTask(id, row) {
  const task = taskIndex[id];
  try {
    const result = await taskRequest({ action: "done", line: task.meta.line, raw: task.meta.raw, done: true });
    await vanish(row);
    if (row?.classList.contains("kcard")) route();
    toast("Готово и отмечено в Obsidian", "Отменить", async () => {
      await taskRequest({ action: "done", line: result.line, raw: result.raw, done: false });
      route();
    });
  } catch (error) {
    toast(error.message);
    route();
  }
}

async function addTask(input, dateInput, status) {
  const text = input.value.trim();
  if (!text) return;
  try {
    const result = await taskRequest({ action: "add", text, due: dateInput?.value || null, parse: true, status: status || " " });
    input.value = "";
    if (dateInput) dateInput.value = "";
    toast(result.due ? `Добавлено, срок: ${dueText(result.due).text}` : "Добавлено в «Актуальное»");
    route();
  } catch (error) { toast(error.message); }
}

function editableText(task) {
  return (task.meta.markdown || task.title).replace(/\s*\(до \d{1,2}\.\d{2}(\.\d{4})?\)\s*$/, "");
}

const COLUMNS = [
  { status: " ", key: "todo", title: "Сделать" },
  { status: "/", key: "/", title: "В работе" },
  { status: "w", key: "w", title: "Жду" },
  { status: "x", key: "x", title: "Готово" },
];
const BUCKETS = [["overdue", "Просрочено"], ["today", "Сегодня"], ["week", "На неделе"], ["later", "Позже"], ["none", "Без срока"]];
const FILTERS = [["all", "Все"], ["soon", "Срочные"], ["chat", "Из чатов"], ["self", "Себе"], ["none", "Без срока"]];
const DONE_PREVIEW = 4;
const board = { data: null, q: "", only: "all", doneOpen: false, adding: null };

function dueRank(task) {
  return task.due || "9999";
}

function bucketOf(task) {
  if (!task.due) return "none";
  const diff = dayDiff(task.due);
  if (diff < 0) return "overdue";
  if (diff === 0) return "today";
  return diff <= 7 ? "week" : "later";
}

function dueText(iso) {
  const diff = dayDiff(iso);
  const date = new Date(`${iso}T00:00:00`);
  const short = date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }).replace(".", "");
  if (diff < 0) return { text: `просрочено ${-diff} дн`, tone: "bad" };
  if (diff === 0) return { text: "сегодня", tone: "bad" };
  if (diff === 1) return { text: "завтра", tone: "warn" };
  if (diff < 7) return { text: `${date.toLocaleDateString("ru-RU", { weekday: "short" })}, ${short}`, tone: "warn" };
  return { text: short, tone: "" };
}

const isSelfNote = (task) => /^(Себе|Обещание):/.test(task.title);

function matchesBoard(task) {
  const q = board.q.trim().toLowerCase();
  if (q && !`${task.title} ${task.meta.context || ""} ${task.source_name || ""}`.toLowerCase().includes(q)) return false;
  if (board.only === "soon") return ["overdue", "today", "week"].includes(bucketOf(task));
  if (board.only === "chat") return isFromChat(task);
  if (board.only === "self") return isSelfNote(task);
  if (board.only === "none") return !task.due;
  return true;
}

function dueChip(task) {
  const when = task.due ? dueText(task.due) : null;
  return `<button class="due-chip ${when ? when.tone : "empty"}" data-due-menu="${task.id}" title="Срок" aria-haspopup="menu">
    ${icon("calendar")}<span>${when ? esc(when.text) : "срок"}</span></button>`;
}

function card(task) {
  remember(task);
  const when = task.due ? dueText(task.due) : null;
  const tag = isFromChat(task) ? `<span class="tag reply">${icon("chat")}из чата</span>` : "";
  if (!task.editable) {
    return `<article class="kcard locked"><span class="kcheck ghost" title="Эта задача в другой заметке, отметить её можно в Obsidian"></span>
      <div class="kcard-main"><a class="kcard-title" href="${esc(safeUrl(task.url))}">${esc(taskTitle(task))}</a>
      <div class="kcard-meta">${when ? `<span class="due-chip ${when.tone}">${icon("calendar")}<span>${esc(when.text)}</span></span>` : ""}<span class="kcard-sub">${esc(task.source_name)}</span></div></div></article>`;
  }
  const status = task.meta.status || " ";
  const index = COLUMNS.findIndex((c) => c.status === status);
  const prev = COLUMNS[index - 1];
  const next = COLUMNS[index + 1];
  const remove = isFromChat(task)
    ? `<button class="tool" data-reject="${task.id}" title="Не дело: убрать и больше не добавлять" aria-label="Убрать">${icon("trash")}</button>`
    : `<button class="tool" data-delete="${task.id}" title="Удалить из «Актуального»" aria-label="Удалить">${icon("trash")}</button>`;
  return `<article class="kcard ${when?.tone === "bad" ? "urgent" : ""}" draggable="true" data-card="${task.id}">
    <button class="kcheck" data-kdone="${task.id}" title="Готово" aria-label="Готово: ${esc(taskTitle(task))}">${icon("check")}</button>
    <div class="kcard-main">
      <button class="task-text kcard-title" data-edit="${task.id}" title="Изменить текст">${esc(taskTitle(task))}</button>
      ${task.meta.context ? `<div class="kcard-sub">${esc(task.meta.context)}</div>` : ""}
      <div class="kcard-meta">${dueChip(task)}${tag}</div>
    </div>
    <div class="kcard-tools">
      ${prev ? `<button class="tool" data-shift="${task.id}" data-to="${prev.key}" title="В «${prev.title}»" aria-label="В колонку ${prev.title}">${icon("left")}</button>` : ""}
      ${next ? `<button class="tool" data-shift="${task.id}" data-to="${next.key}" title="В «${next.title}»" aria-label="В колонку ${next.title}">${icon("chev")}</button>` : ""}
      ${remove}
    </div>
  </article>`;
}

function doneCard(task, index) {
  doneIndex[index] = task;
  return `<article class="kcard done" draggable="true" data-done-card="${index}">
    <button class="kcheck on" data-reopen="${index}" title="Вернуть в «Сделать»" aria-label="Вернуть: ${esc(task.title)}">${icon("check")}</button>
    <div class="kcard-main"><span class="kcard-title">${esc(task.title)}</span></div></article>`;
}

function columnBody(column, rows) {
  if (column.status === "x") {
    const shown = board.doneOpen ? rows : rows.slice(0, DONE_PREVIEW);
    const more = rows.length - shown.length;
    return shown.map((t) => doneCard(t, rows.indexOf(t))).join("")
      + (more > 0 ? `<button class="kcol-more" data-done-more>Ещё ${more}</button>` : "")
      + (board.doneOpen && rows.length > DONE_PREVIEW ? '<button class="kcol-more" data-done-more>Свернуть</button>' : "");
  }
  if (column.status !== " " || rows.length < 4) return rows.map(card).join("");
  return BUCKETS.map(([key, title]) => {
    const part = rows.filter((t) => bucketOf(t) === key);
    return part.length ? `<div class="kgroup is-${key}"><span>${title}</span><span>${part.length}</span></div>${part.map(card).join("")}` : "";
  }).join("");
}

function addForm(column) {
  return board.adding === column.key ? `<form class="kcol-add" data-col-form="${column.key}">
    <input placeholder="Задача, срок можно в конце: «в пятницу»" aria-label="Новая задача в колонку ${column.title}" autocomplete="off">
  </form>` : "";
}

function kanban() {
  const data = board.data;
  const open = data.tasks.filter(matchesBoard).sort((x, y) => dueRank(x).localeCompare(dueRank(y)));
  const done = board.q ? data.done.filter((t) => t.title.toLowerCase().includes(board.q.trim().toLowerCase())) : data.done;
  return `<div class="kanban">${COLUMNS.map((column) => {
    const rows = column.status === "x" ? done : open.filter((t) => (t.meta.status || " ") === column.status);
    const body = columnBody(column, rows);
    const empty = column.status === "x" ? "Отметьте задачу кружком, и она окажется здесь"
      : board.q || board.only !== "all" ? "Ничего не подошло" : "Перетащите сюда карточку";
    return `<section class="kcol ${column.key === "x" ? "kcol-done" : ""}" data-column="${column.key}" style="--col:var(--col-${column.key === "todo" ? "todo" : column.key === "/" ? "doing" : column.key === "w" ? "wait" : "done"})">
      <div class="kcol-head"><span class="kcol-dot"></span><span class="kcol-title">${esc(column.title)}</span><span class="kcol-count">${rows.length}</span>
        ${column.status === "x" ? "" : `<button class="tool kcol-plus" data-col-add="${column.key}" title="Добавить в «${column.title}»" aria-label="Добавить в колонку ${column.title}">${icon("plus")}</button>`}</div>
      ${addForm(column)}
      <div class="kcol-body">${body || `<p class="kcol-empty">${empty}</p>`}</div></section>`;
  }).join("")}</div>`;
}

function boardFilters() {
  const count = (key) => board.data.tasks.filter((t) => {
    const saved = board.only;
    board.only = key;
    const ok = matchesBoard(t);
    board.only = saved;
    return ok;
  }).length;
  return FILTERS.map(([key, title]) => `<button class="kfilter" data-board-only="${key}" aria-pressed="${board.only === key}">${title}<span>${count(key)}</span></button>`).join("");
}

function paintBoard() {
  document.getElementById("board-filters").innerHTML = boardFilters();
  document.getElementById("board").innerHTML = kanban();
  const form = view.querySelector("[data-col-form] input");
  if (form) form.focus();
}

async function renderTasks() {
  const token = routeToken;
  const data = await api("/api/tasks");
  if (token !== routeToken) return;
  board.data = data;
  const stale = data.stale.length ? `<details class="group"><summary>Возможно, устарело<span class="count">${data.stale.length}</span>${icon("chev", "chev")}</summary>
    <div class="group-body"><p class="empty">Эти строки давно не менялись. Удалите лишнее или верните в работу, поставив срок в Obsidian.</p>${data.stale.map((t) => { remember(t); return `<div class="stale-row"><span>${esc(taskTitle(t))}</span><small>${esc(t.reasons[0] || "")}</small><button class="tool" data-delete="${t.id}" title="Удалить" aria-label="Удалить">${icon("trash")}</button></div>`; }).join("")}</div></details>` : "";
  const keepQuery = document.getElementById("board-search")?.value ?? board.q;
  view.innerHTML = `
    <header class="top"><h1>Дела</h1><div class="top-right"><span>«Актуальное» в Obsidian</span></div></header>
    <form class="task-add" id="task-add">
      ${icon("plus")}
      <input id="task-new" placeholder="Новая задача, например «Сдать эссе в пятницу»" title="Срок можно написать в конце: «завтра», «в пятницу», «до 15.11», «через 3 дня»" aria-label="Новая задача" autocomplete="off">
      <input id="task-new-date" type="date" aria-label="Срок" title="Или выберите дату">
      <button class="btn primary" type="submit">Добавить</button>
    </form>
    <div class="board-bar">
      <div class="kfilters" id="board-filters" role="group" aria-label="Показать"></div>
      <label class="board-search">${icon("search")}<input id="board-search" type="search" placeholder="Найти" value="${esc(keepQuery)}" aria-label="Найти задачу"></label>
    </div>
    <div id="board"></div>
    <div class="stack">${stale}</div>`;
  board.q = keepQuery;
  paintBoard();
  document.getElementById("task-add").onsubmit = (event) => {
    event.preventDefault();
    addTask(document.getElementById("task-new"), document.getElementById("task-new-date"));
  };
  document.getElementById("board-search").oninput = (event) => {
    board.q = event.target.value;
    paintBoard();
  };
}

function nextWeekday(target) {
  const day = new Date(); day.setHours(0, 0, 0, 0);
  const shift = ((target - day.getDay() + 7) % 7) || 7;
  day.setDate(day.getDate() + shift);
  return day;
}

function isoDay(day) {
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
}

function plusDays(n) {
  const day = new Date(); day.setHours(0, 0, 0, 0);
  day.setDate(day.getDate() + n);
  return day;
}

function closeDueMenu() {
  document.getElementById("due-menu")?.remove();
}

function openDueMenu(button) {
  closeDueMenu();
  const task = taskIndex[button.dataset.dueMenu];
  const options = [
    ["Сегодня", isoDay(plusDays(0))], ["Завтра", isoDay(plusDays(1))],
    ["В пятницу", isoDay(nextWeekday(5))], ["В понедельник", isoDay(nextWeekday(1))], ["Через неделю", isoDay(plusDays(7))],
  ];
  const menu = document.createElement("div");
  menu.id = "due-menu";
  menu.className = "due-menu";
  menu.setAttribute("role", "menu");
  menu.innerHTML = options.map(([label, iso]) => `<button role="menuitem" data-set-due="${iso}"><span>${label}</span><small>${esc(new Date(`${iso}T00:00:00`).toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "short" }).replace(".", ""))}</small></button>`).join("")
    + `<label class="due-pick">Дата<input type="date" value="${esc(task.due || "")}" data-set-due-input></label>`
    + (task.due ? '<button role="menuitem" class="due-clear" data-set-due="">Убрать срок</button>' : "");
  menu.dataset.task = task.id;
  document.body.append(menu);
  const rect = button.getBoundingClientRect();
  const left = Math.min(rect.left, innerWidth - menu.offsetWidth - 12);
  const below = rect.bottom + 6 + menu.offsetHeight < innerHeight;
  menu.style.left = `${Math.max(12, left)}px`;
  menu.style.top = `${below ? rect.bottom + 6 : rect.top - menu.offsetHeight - 6}px`;
  menu.querySelector("button")?.focus();
}

async function setDue(taskId, due) {
  const task = taskIndex[taskId];
  closeDueMenu();
  try {
    await taskRequest({ action: "edit", line: task.meta.line, raw: task.meta.raw, text: editableText(task), due: due || null });
    toast(due ? `Срок: ${dueText(due).text}` : "Срок убран");
    route();
  } catch (error) { toast(error.message); route(); }
}

document.addEventListener("click", (event) => {
  const menu = document.getElementById("due-menu");
  if (!menu) return;
  const choice = event.target.closest("[data-set-due]");
  if (choice) return setDue(menu.dataset.task, choice.dataset.setDue);
  if (!event.target.closest("#due-menu, [data-due-menu]")) closeDueMenu();
});
document.addEventListener("change", (event) => {
  const input = event.target.closest("[data-set-due-input]");
  if (input && input.value) setDue(document.getElementById("due-menu").dataset.task, input.value);
});
addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDueMenu();
  const typing = event.target.closest("input, textarea, select, [contenteditable]");
  if (!typing && event.key.toLowerCase() === "n" && !event.ctrlKey && !event.metaKey && location.hash.startsWith("#tasks")) {
    event.preventDefault();
    document.getElementById("task-new")?.focus();
  }
});
addEventListener("scroll", closeDueMenu, { passive: true });

const statusOf = (value) => (value === "todo" ? " " : value);

async function moveTask(source, status) {
  try {
    await taskRequest({ action: "status", line: source.line, raw: source.raw, status });
    route();
  } catch (error) { toast(error.message); route(); }
}

let dragged = null;

function dragSource(el) {
  if (el.dataset.card) return taskIndex[el.dataset.card].meta;
  return doneIndex[el.dataset.doneCard];
}

view.addEventListener("dragstart", (event) => {
  const el = event.target.closest("[data-card], [data-done-card]");
  if (!el) return;
  dragged = dragSource(el);
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", "task");
  el.classList.add("dragging");
});

view.addEventListener("dragend", () => {
  dragged = null;
  view.querySelectorAll(".dragging, .drop").forEach((el) => el.classList.remove("dragging", "drop"));
});

view.addEventListener("dragover", (event) => {
  const column = event.target.closest("[data-column]");
  if (!column || !dragged) return;
  event.preventDefault();
  view.querySelectorAll(".drop").forEach((el) => el !== column && el.classList.remove("drop"));
  column.classList.add("drop");
});

view.addEventListener("drop", (event) => {
  const column = event.target.closest("[data-column]");
  if (!column || !dragged) return;
  event.preventDefault();
  moveTask(dragged, statusOf(column.dataset.column));
});

function startEdit(button) {
  const task = taskIndex[button.dataset.edit];
  const input = document.createElement("input");
  input.className = "task-input";
  input.value = editableText(task);
  button.replaceWith(input);
  input.focus();
  let finished = false;
  const finish = async (save) => {
    if (finished) return;
    finished = true;
    const text = input.value.trim();
    if (!save || !text || text === editableText(task)) { input.replaceWith(button); return; }
    try {
      await taskRequest({ action: "edit", line: task.meta.line, raw: task.meta.raw, text, due: task.due });
      route();
    } catch (error) { toast(error.message); route(); }
  };
  input.onkeydown = (event) => {
    if (event.key === "Enter") finish(true);
    if (event.key === "Escape") finish(false);
  };
  input.onblur = () => finish(true);
}

function chatRow(chat) {
  return `<a class="quiet-row" href="#feed/${encodeURIComponent(chat.name)}"><span>${esc(chat.name)}</span><span>${chat.count}</span></a>`;
}

function doRow(row, lead) {
  const cls = lead ? "do-row lead" : "do-row";
  if (row.task) {
    remember(row.task);
    const when = whenLabel(row.task.due);
    const remove = isFromChat(row.task)
      ? `<button class="tool" data-reject="${row.task.id}" title="Удалить: не дело, больше не добавлять" aria-label="Удалить: ${esc(row.task.title)}">${icon("trash")}</button>`
      : `<button class="tool" data-delete="${row.task.id}" title="Удалить из «Актуального»" aria-label="Удалить: ${esc(row.task.title)}">${icon("trash")}</button>`;
    return `<div class="${cls}">
      <input type="checkbox" class="task-check" data-done="${row.task.id}" aria-label="Готово: ${esc(row.task.title)}">
      <a class="do-text" href="${esc(safeUrl(row.task.url))}"><span class="do-title">${esc(taskTitle(row.task))}</span></a>
      <span class="do-side"><span class="do-tools">${remove}</span><button class="when when-btn ${when.tone}" data-due-menu="${row.task.id}" title="Изменить срок">${esc(when.text)}</button></span></div>`;
  }
  const { chat } = row;
  const when = chat.due ? whenLabel(chat.due) : null;
  const title = chat.reply ? `Ответить: ${chat.name}` : chat.name;
  const note = chat.action || chat.preview || chat.summary || "";
  const link = safeUrl(chat.link);
  const external = link.startsWith("http") ? 'target="_blank" rel="noreferrer noopener"' : "";
  return `<div class="${cls}">
    <button class="do-mark" data-chat-done="${esc(chat.name)}" title="Уже сделано" aria-label="Уже сделано: ${esc(chat.name)}">${icon("check")}</button>
    <a class="do-text" href="#feed/${encodeURIComponent(chat.name)}"><span class="do-title">${esc(title)}</span><span class="do-note">${esc(note)}</span></a>
    <span class="do-side">
      <span class="do-tools">
        ${link ? `<a class="tool" href="${esc(link)}" ${external} title="Открыть сообщение в Telegram" aria-label="Открыть в Telegram">${icon("open")}</a>` : ""}
        <button class="tool" data-chat-skip="${esc(chat.name)}" title="Удалить: не важно, Пульс учтёт это для чата" aria-label="Удалить: ${esc(chat.name)}">${icon("trash")}</button>
      </span>${when ? `<span class="when ${when.tone}">${esc(when.text)}</span>` : ""}</span></div>`;
}

function laterRow(task) {
  remember(task);
  const remove = isFromChat(task)
    ? `<button class="tool" data-reject="${task.id}" title="Удалить: не дело, больше не добавлять" aria-label="Удалить: ${esc(task.title)}">${icon("trash")}</button>`
    : `<button class="tool" data-delete="${task.id}" title="Удалить из «Актуального»" aria-label="Удалить: ${esc(task.title)}">${icon("trash")}</button>`;
  return `<div class="do-row later">
    <input type="checkbox" class="task-check" data-done="${task.id}" aria-label="Готово: ${esc(task.title)}">
    <a class="do-text" href="${esc(safeUrl(task.url))}"><span class="do-title">${esc(taskTitle(task))}</span></a>
    <span class="do-side"><span class="do-tools">
      <button class="tool" data-due-menu="${task.id}" title="Поставить срок" aria-label="Поставить срок: ${esc(task.title)}">${icon("calendar")}</button>${remove}
    </span></span></div>`;
}

function doColumn(data) {
  const rows = data.queue;
  const shown = rows.slice(0, 7).map((row, index) => doRow(row, index === 0)).join("");
  const extra = rows.length > 7 ? `<details class="mini"><summary>Ещё ${rows.length - 7}</summary>${rows.slice(7).map((r) => doRow(r, false)).join("")}</details>` : "";
  const later = data.agenda.length ? `<div class="do-later"><div class="sub-label"><span>Без срока</span><a class="link" href="#tasks">Все дела</a></div>
    ${data.agenda.map(laterRow).join("")}</div>` : "";
  return `<section class="col" id="do">
    <h2 class="sec">Сделать<span>${rows.length || ""}</span></h2>
    ${shown || '<p class="calm">Срочного нет. Можно заняться своим.</p>'}${extra}
    <form class="quick-add" id="quick-add">${icon("plus")}<input id="quick-task" placeholder="Добавить дело: «позвонить завтра»" title="Срок можно написать словами в конце: «завтра», «в пятницу», «до 20.11»" aria-label="Новая задача" autocomplete="off"></form>
    ${later}
  </section>`;
}

function glance(data) {
  const t = data.tiles;
  const items = [
    t.overdue && `<a class="glance-item bad" href="#tasks" data-glance="soon">Просрочено<b>${t.overdue}</b></a>`,
    t.week && `<a class="glance-item warn" href="#tasks" data-glance="soon">На неделе<b>${t.week}</b></a>`,
    t.reply && `<button class="glance-item accent" data-scroll="do">Ждут ответа<b>${t.reply}</b></button>`,
    t.news && `<button class="glance-item" data-scroll="news-panel">Новости по темам<b>${t.news}</b></button>`,
  ].filter(Boolean);
  const calm = t.overdue || t.week || t.reply ? "" : '<span class="glance-calm">Срочного нет</span>';
  return `<nav class="glance" aria-label="Коротко о дне">${calm}${items.join("")}</nav>`;
}

const ASK_HINTS = {
  local: ["Что интересного?", "Что важного в чатах?", "Кто ждёт моего ответа?", "За чем ты следишь?"],
  claude: ["Найди фриланс-заказы под мой профиль", "Что обсуждают в открытых чатах?", "Какие вакансии и стажировки появились?"],
};
const ASK_ENGINE_KEY = "pulse-ask-engine";
let askEngine = "local";
let localReady = false;
let claudeReady = false;
const askLog = [];

const askPanel = document.getElementById("ask-panel");
const ASK_POSITION_KEY = "pulse-ask-position";

function paintAsk() {
  const box = document.getElementById("ask-log");
  if (!askLog.length) {
    const intro = askEngine === "claude" ? "Claude видит только открытые группы, посты каналов и новости за неделю. Личные и закрытые чаты к нему не уходят."
      : "Что вас интересует?";
    box.innerHTML = `<p class="empty">${esc(intro)}</p><div class="ask-hints">${ASK_HINTS[askEngine].map((h) => `<button type="button" class="chip" data-hint="${esc(h)}">${esc(h)}</button>`).join("")}</div>`;
    return;
  }
  const text = (m) => (m.pending ? `<span class="typing" aria-label="Думает"><i></i><i></i><i></i></span>${m.engine === "claude" ? '<span class="typing-note">Claude читает открытые чаты, до пары минут</span>' : ""}` : esc(m.text));
  const engine = (m) => (m.role === "bot" && m.engine === "claude" && !m.pending ? `<div class="bubble-engine">Claude${m.read ? ` · прочитал ${m.read} сообщений` : ""}</div>` : "");
  box.innerHTML = askLog.map((m) => `<div class="bubble ${m.role}">${engine(m)}${text(m)}${quotesHtml(m.quotes)}${!m.quotes?.length && m.sources?.length ? `<div class="bubble-source">${esc(m.sources.join(", "))}</div>` : ""}</div>`).join("");
  box.scrollTop = box.scrollHeight;
}

function quotesHtml(quotes) {
  if (!quotes?.length) return "";
  return `<div class="quotes">${quotes.map((q) => {
    const link = safeUrl(q.link);
    const external = link.startsWith("http") ? 'target="_blank" rel="noreferrer noopener"' : "";
    const head = [q.source, q.author, q.day].filter(Boolean).map(esc).join(" · ");
    return `<blockquote class="quote"><div class="quote-head"><span>${head}</span>${link ? `<a href="${esc(link)}" ${external} title="Открыть сообщение">${icon("open")}</a>` : ""}</div>${esc(q.text)}</blockquote>`;
  }).join("")}</div>`;
}

async function askClaude(question, history) {
  const job = await api("/api/ask-claude", { question, history });
  for (let attempt = 0; attempt < 160; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    const state = await api(`/api/ask-claude?id=${encodeURIComponent(job.id)}`);
    if (state.status === "done") return state;
    if (state.status === "error") throw new Error(state.error);
  }
  throw new Error("Claude не ответил за шесть минут");
}

async function sendQuestion(question) {
  const text = question.trim();
  if (!text) return;
  const engine = askEngine;
  // У каждого свой разговор: локальная модель не видит ответов Claude, и наоборот
  const history = askLog.filter((m) => !m.pending && (m.engine || "local") === engine).map(({ role, text: t }) => ({ role, text: t }));
  askLog.push({ role: "user", text, engine }, { role: "bot", text: "Думаю…", pending: true, engine });
  paintAsk();
  try {
    const data = engine === "claude" ? await askClaude(text, history) : await api("/api/ask", { question: text, history });
    askLog[askLog.length - 1] = { role: "bot", text: data.answer, sources: data.sources, quotes: data.quotes, engine, read: data.read };
  } catch (error) {
    askLog.pop();
    askLog.pop();
    toast(error.message);
  }
  paintAsk();
}

function setAskPanel(open) {
  askPanel.hidden = !open;
  document.getElementById("ask-open").hidden = open;
  if (open) {
    keepPanelOnScreen();
    paintAsk();
    document.getElementById("ask-input").focus();
  }
}

function placePanel(left, top) {
  askPanel.style.left = `${left}px`;
  askPanel.style.top = `${top}px`;
  askPanel.style.right = "auto";
  askPanel.style.bottom = "auto";
}

function keepPanelOnScreen() {
  if (!askPanel.style.left) return;
  const box = askPanel.getBoundingClientRect();
  placePanel(Math.max(0, Math.min(box.left, innerWidth - box.width)), Math.max(0, Math.min(box.top, innerHeight - 48)));
}

function restorePanel() {
  try {
    const saved = JSON.parse(localStorage.getItem(ASK_POSITION_KEY));
    if (saved && innerWidth > 720) placePanel(saved.left, saved.top);
  } catch { /* окно откроется в углу */ }
}

function enableDrag() {
  const head = document.getElementById("ask-head");
  let start = null;
  head.addEventListener("pointerdown", (event) => {
    if (innerWidth <= 720 || event.target.closest("button")) return;
    const box = askPanel.getBoundingClientRect();
    start = { x: event.clientX - box.left, y: event.clientY - box.top };
    placePanel(box.left, box.top);
    head.setPointerCapture(event.pointerId);
    head.classList.add("dragging");
  });
  head.addEventListener("pointermove", (event) => {
    if (!start) return;
    const box = askPanel.getBoundingClientRect();
    placePanel(Math.max(0, Math.min(event.clientX - start.x, innerWidth - box.width)), Math.max(0, Math.min(event.clientY - start.y, innerHeight - 48)));
  });
  const finish = () => {
    if (!start) return;
    start = null;
    head.classList.remove("dragging");
    const box = askPanel.getBoundingClientRect();
    try { localStorage.setItem(ASK_POSITION_KEY, JSON.stringify({ left: box.left, top: box.top })); } catch { /* место не запомнится */ }
  };
  head.addEventListener("pointerup", finish);
  head.addEventListener("pointercancel", finish);
}

function setupAsk() {
  document.getElementById("ask-open").onclick = () => setAskPanel(true);
  document.getElementById("ask-close").onclick = () => setAskPanel(false);
  document.getElementById("ask-clear").onclick = () => { askLog.length = 0; paintAsk(); };
  document.getElementById("ask-form").onsubmit = (event) => {
    event.preventDefault();
    const input = document.getElementById("ask-input");
    sendQuestion(input.value);
    input.value = "";
  };
  document.querySelectorAll("[data-engine]").forEach((button) => { button.onclick = () => setAskEngine(button.dataset.engine); });
  try { askEngine = localStorage.getItem(ASK_ENGINE_KEY) === "claude" ? "claude" : "local"; } catch { /* по умолчанию локально */ }
  document.getElementById("ask-log").addEventListener("click", (event) => {
    const hint = event.target.closest("[data-hint]");
    if (hint) sendQuestion(hint.dataset.hint);
  });
  addEventListener("keydown", (event) => { if (event.key === "Escape" && !askPanel.hidden) setAskPanel(false); });
  addEventListener("resize", keepPanelOnScreen);
  enableDrag();
  restorePanel();
}

function setAskReady(model, claude) {
  localReady = Boolean(model);
  claudeReady = Boolean(claude);
  setAskEngine(askEngine);
}

function setAskEngine(engine) {
  askEngine = engine;
  try { localStorage.setItem(ASK_ENGINE_KEY, engine); } catch { /* выбор не запомнится */ }
  document.querySelectorAll("[data-engine]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.engine === engine)));
  askPanel.classList.toggle("cloud", engine === "claude");
  const input = document.getElementById("ask-input");
  const ready = engine === "claude" ? claudeReady : localReady;
  input.disabled = !ready;
  input.placeholder = engine === "claude"
    ? (ready ? "Спросить Claude по открытым чатам и новостям" : "Claude не подключён: claude_login.bat")
    : (ready ? "Спросить о чатах и делах" : "Локальная модель не установлена");
  if (!askPanel.hidden) paintAsk();
}

function newsList() {
  if (activeTopic) {
    const pool = [...today.main, ...today.rest.flatMap((g) => g.items)].filter((i) => i.topics.includes(activeTopic));
    return pool.map(newsRow).join("") || '<p class="calm">Сейчас ничего важного.</p>';
  }
  const fresh = today.fresh.length ? `<div class="news-label">Только что</div>${today.fresh.map(newsRow).join("")}<div class="news-label">Главное</div>` : "";
  return fresh + (today.main.map(newsRow).join("") || '<p class="calm">Сейчас ничего важного.</p>');
}

function topicPicker() {
  const options = today.topics.map((t) => `<option value="${esc(t.id)}" ${activeTopic === t.id ? "selected" : ""}>${esc(t.name)} (${t.count})</option>`).join("");
  return `<select class="topic-pick" id="topic-pick" aria-label="Тема новостей"><option value="">Главное</option>${options}</select>`;
}

function pickRow(pick) {
  const link = safeUrl(pick.link);
  const external = link.startsWith("http") ? 'target="_blank" rel="noreferrer noopener"' : "";
  const head = [pick.source, pick.author].filter(Boolean).join(" · ");
  return `<article class="pick" data-pick="${esc(pick.uid)}" data-id="${pick.id}">
    <div class="pick-head"><span>${esc(head)}</span><span>${esc(ago(pick.published))}</span></div>
    <button class="pick-gist" data-pick-open aria-expanded="false">${esc(pick.gist)}</button>
    ${pick.why ? `<div class="pick-why">${esc(pick.why)}</div>` : ""}
    <div class="pick-full" hidden>${esc(pick.body)}</div>
    <div class="pick-tools">
      ${link ? `<a class="tool" href="${esc(link)}" ${external} title="Открыть в Telegram" aria-label="Открыть в Telegram">${icon("open")}</a>` : ""}
      <button class="tool ${pick.liked ? "on" : ""}" data-pick-rate="1" title="Интересно: похожее будет чаще" aria-label="Интересно">${icon("up")}</button>
      <button class="tool" data-pick-rate="-1" title="Не то: похожее будет реже" aria-label="Не то">${icon("down")}</button>
    </div>
  </article>`;
}

const SUGGEST_BY = { claude: "отобрал Claude", model: "проверила модель", rules: "по правилам" };
let suggestKind = "";
let suggestData = null;

function suggestCard(item, compact = false) {
  const link = safeUrl(item.link);
  const external = link.startsWith("http") ? 'target="_blank" rel="noreferrer noopener"' : "";
  const kind = suggestData?.kinds.find((k) => k.id === item.kind) || SUGGEST_KINDS[item.kind];
  const when = item.when ? whenLabel(item.when) : null;
  const head = [kind?.name, item.source, ago(item.published)].filter(Boolean).join(" · ");
  return `<article class="pick sg ${compact ? "compact" : ""}" data-sg="${esc(item.uid)}">
    <div class="pick-head"><span>${esc(head)}</span>${when ? `<span class="when ${when.tone}">${item.kind === "events" ? "" : "до "}${esc(when.text)}</span>` : ""}</div>
    <button class="pick-gist" data-pick-open aria-expanded="false">${esc(item.title)}</button>
    <div class="pick-why">${esc(item.why)}${compact ? "" : `<span class="sg-by"> · ${esc(SUGGEST_BY[item.by] || "")}</span>`}</div>
    <div class="pick-full" hidden>${esc(item.body)}</div>
    <div class="pick-tools">
      ${link ? `<a class="tool" href="${esc(link)}" ${external} title="Открыть" aria-label="Открыть">${icon("open")}</a>` : ""}
      <button class="tool" data-sg-task title="В дела: добавить в «Актуальное»${item.when ? " со сроком" : ""}" aria-label="В дела">${icon("task")}</button>
      <button class="tool ${item.liked ? "on" : ""}" data-sg-rate="1" title="Подходит: похожее будет выше" aria-label="Подходит">${icon("up")}</button>
      <button class="tool" data-sg-rate="-1" title="Не то: убрать, похожее будет ниже" aria-label="Не то">${icon("down")}</button>
    </div>
  </article>`;
}

const SUGGEST_KINDS = { gigs: { name: "Заказы" }, jobs: { name: "Вакансии" }, events: { name: "События" }, reads: { name: "Материалы" } };

async function loadHomeSuggest() {
  const box = document.getElementById("home-suggest");
  if (!box) return;
  const data = await api("/api/suggest?home=1").catch(() => null);
  if (!data?.items.length || !document.body.contains(box)) return;
  box.innerHTML = `<div class="news-label sg-label"><span>Предложка</span><a class="link" href="#suggest">Вся предложка · ${data.total}</a></div>
    ${data.items.map((item) => suggestCard(item, true)).join("")}`;
}

function suggestJobLine(job) {
  if (job.status === "working") return `<span class="busy">${job.engine === "claude" ? "Claude подбирает…" : "Модель проверяет…"}</span>`;
  if (job.status === "error") return `<span class="bad-text">${esc(job.error)}</span>`;
  if (job.status === "done") return job.engine === "claude" ? `Claude отобрал ${job.added} из ${job.read} открытых материалов` : `Модель проверила ${job.added}`;
  return "";
}

function renderSuggest(data) {
  suggestData = data;
  if (suggestKind && !data.kinds.some((k) => k.id === suggestKind && k.on)) suggestKind = "";
  const items = data.items.filter((item) => !suggestKind || item.kind === suggestKind);
  const working = data.job.status === "working";
  const chips = [`<button class="chip" data-sg-kind="" aria-pressed="${!suggestKind}">Все<span>${data.total}</span></button>`,
    ...data.kinds.filter((k) => k.on).map((k) => `<button class="chip" data-sg-kind="${k.id}" aria-pressed="${suggestKind === k.id}">${esc(k.name)}<span>${k.count}</span></button>`)].join("");
  const mode = data.modes.find((m) => m.id === data.mode.id);
  const toggles = data.kinds.map((k) => `<label class="sg-toggle"><input type="checkbox" data-sg-show="${k.id}" ${mode.kinds.includes(k.id) ? "checked" : ""}>${esc(k.name)}</label>`).join("");
  view.innerHTML = `
    <header class="top"><h1>Предложка</h1>
      <div class="modes" role="group" aria-label="Режим внимания">
        ${data.modes.map((m) => `<button data-mode="${esc(m.id)}" aria-pressed="${m.id === data.mode.id}">${esc(m.name)}</button>`).join("")}
      </div></header>
    <div class="sg-bar">
      <button class="btn" data-sg-run="local" ${working ? "disabled" : ""} aria-label="Подобрать локально" title="Локальная модель проверит лучших кандидатов, включая закрытые группы. Всё остаётся на компьютере">Подобрать локально</button>
      <button class="btn" data-sg-run="claude" ${working ? "disabled" : ""} aria-label="Подобрать с Claude" title="Claude получит открытые группы, посты каналов и новости за неделю и ваш профиль. Закрытые и личные чаты не уходят">${icon("spark")} Подобрать с Claude</button>
      <span class="sg-job" id="sg-job">${suggestJobLine(data.job)}</span>
    </div>
    <details class="sg-about" ${data.profile ? "" : "open"}>
      <summary>О себе${data.profile ? "" : ": заполните, и подбор станет точнее"}</summary>
      <form id="sg-about"><textarea id="sg-about-text" rows="4" placeholder="Навыки, опыт, что ищете: например, «пишу тексты и делаю исследования, ищу удалённые заказы и стажировки»">${esc(data.about)}</textarea>
      <div class="sg-about-foot"><small class="soft">Правила и локальная модель читают это на компьютере. Claude получает этот текст только по кнопке «Подобрать с Claude» и в окне «Спросить».</small>
      <button class="btn primary" type="submit">Сохранить</button></div></form>
    </details>
    <div class="sg-show"><span>В режиме «${esc(data.mode.name)}» показывать:</span>${toggles}</div>
    <div class="chips">${chips}</div>
    <section class="sg-list">${items.map((item) => suggestCard(item)).join("") || `<p class="empty">${data.kinds.some((k) => k.on) ? "Пока ничего подходящего. Выберите больше чатов и каналов или расскажите о себе." : "В этом режиме предложка выключена: отметьте виды выше."}</p>`}</section>`;
  document.getElementById("sg-about").onsubmit = async (event) => {
    event.preventDefault();
    try {
      await api("/api/suggest-about", { about: document.getElementById("sg-about-text").value });
      toast("Сохранено. Предложка пересчитана");
      route();
    } catch (error) { toast(error.message); }
  };
  if (working) waitForSuggest();
}

async function renderSuggestPage() {
  const token = routeToken;
  const data = await api("/api/suggest");
  if (token === routeToken) renderSuggest(data);
}

function waitForSuggest() {
  clearTimeout(waitForSuggest.timer);
  waitForSuggest.timer = setTimeout(async () => {
    const job = await api("/api/suggest-run").catch(() => null);
    if (!job) return;
    if (job.status === "working") return waitForSuggest();
    if (job.status === "error") toast(job.error);
    else toast(job.engine === "claude" ? `Claude добавил в предложку ${job.added}` : `Модель проверила ${job.added}`);
    if (location.hash.startsWith("#suggest") && isCalm()) route();
  }, 2000);
}

function picksBlock(chosen) {
  if (!chosen?.length) return "";
  return `<section class="picks"><div class="news-label">Для вас</div>${chosen.map(pickRow).join("")}</section>`;
}

function briefBlock(brief) {
  if (brief.author !== "claude") return "";
  const sourceLinks = (s) => (s.sources || []).filter((l) => safeUrl(l.url))
    .map((l) => `<a class="link" href="${esc(safeUrl(l.url))}" target="_blank" rel="noreferrer noopener">${esc(l.title)}</a>`).join(" · ");
  const sections = (brief.sections || []).map((s) => `<h3>${esc(s.title)}</h3><p>${esc(s.text)}</p><div>${sourceLinks(s)}</div>`).join("");
  const opened = openBrief;
  openBrief = false;
  return `<details class="brief" ${opened ? "open" : ""}>
    <summary><span class="headline">${esc(brief.headline)}</span>${icon("chev", "chev")}</summary>
    <div class="brief-body"><p>${esc(brief.summary)}</p>${sections}<div class="by">Сводку написал Claude</div></div>
  </details>`;
}

function moreItem(title, count, body) {
  return count ? `<details class="more-item"><summary>${esc(title)}<span>${count}</span></summary><div>${body}</div></details>` : "";
}

function moreBlock(data) {
  const { quiet, signals } = data.chats;
  const others = [...signals.filter((c) => !(c.reply || c.due || c.action)), ...quiet];
  const repeat = data.reviews.map((r) => `<a class="quiet-row" href="${esc(safeUrl(r.url))}"><span>${esc(r.title)}</span></a>`).join("");
  const stale = data.stale.map((s) => `<a class="quiet-row" href="${esc(safeUrl(s.url))}" title="${esc(s.reasons[0] || "")}"><span>${esc(s.title)}</span></a>`).join("");
  const failing = data.failing.map((f) => `<p class="note">${esc(f.name)}</p>`).join("");
  return `<section class="more">
    ${moreItem("Остальные чаты", others.length, others.map(chatRow).join(""))}
    ${moreItem("Повторить", data.reviews_total, repeat)}
    ${moreItem("Возможно, устарело", data.stale_total, stale)}
    ${moreItem("Не отвечают источники", data.failing.length, failing)}
  </section>`;
}

function renderToday(data) {
  today = data;
  topicNames = data.topic_names;
  topicOrder = Object.keys(data.topic_names);
  const date = new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  const last = data.last_run ? `${ago(data.last_run.at)} назад` : "ещё не обновлялось";
  const when = (mark) => (mark ? `${ago(mark)} назад` : "ещё нет");
  const updated = `Чаты: ${when(data.marks.chats)}. Новости: ${when(data.marks.news)}. Полное обновление: ${when(data.marks.full)}`;

  view.innerHTML = `
    <header class="top">
      <h1>${esc(date[0].toUpperCase() + date.slice(1))}</h1>
      <div class="modes" role="group" aria-label="Режим внимания">
        ${data.modes.map((m) => `<button data-mode="${esc(m.id)}" aria-pressed="${m.id === data.active_mode}">${esc(m.name)}</button>`).join("")}
      </div>
      <div class="top-right"><span id="run-stage" class="${data.running ? "busy" : ""}" title="${esc(updated)}">${esc(data.running ? `${data.stage.text}…` : last)}</span>
        <button class="icon-btn ${data.running ? "spin" : ""}" id="refresh" ${data.running ? "disabled" : ""} title="Обновить" aria-label="Обновить">${icon("refresh")}</button></div>
    </header>
    ${glance(data)}
    <div class="home">
      ${doColumn(data)}
      <section class="col" id="news-panel">
        <h2 class="sec">Знать<button class="digest-btn" id="digest-btn" type="button" title="Claude соберёт сводку новостей. Чаты в неё не попадают">${icon("spark")}<span>Сводка от Claude</span></button></h2>
        ${picksBlock(data.picks)}
        <section class="picks" id="home-suggest"></section>
        ${briefBlock(data.brief)}
        <div class="news-head"><span>Новости</span>${topicPicker()}</div>
        <div id="news"></div>
      </section>
    </div>
    ${moreBlock(data)}`;
  document.getElementById("news").innerHTML = newsList();
  const doCol = document.getElementById("do");
  doCol.classList.toggle("pin", doCol.offsetHeight < innerHeight - 60);  // короткий список дел остаётся на виду, пока читаются новости
  document.getElementById("topic-pick").onchange = (event) => {
    activeTopic = event.target.value;
    document.getElementById("news").innerHTML = newsList();
  };
  setAskReady(data.local_model, data.claude_ready);
  document.getElementById("quick-add").onsubmit = (event) => {
    event.preventDefault();
    addTask(document.getElementById("quick-task"));
  };
  document.getElementById("refresh").onclick = startCollect;
  document.getElementById("digest-btn").onclick = startDigest;
  if (data.running) waitForRun();
  loadHomeSuggest();
  api("/api/news-digest").then((state) => { if (state.status === "working") watchDigest(); }).catch(() => {});
}

function digestBusy(busy) {
  const button = document.getElementById("digest-btn");
  if (!button) return;
  button.disabled = busy;
  button.querySelector("span").textContent = busy ? "Claude читает новости…" : "Сводка от Claude";
}

async function startDigest() {
  digestBusy(true);
  try {
    await api("/api/news-digest", {});
    watchDigest();
  } catch (error) { digestBusy(false); toast(error.message); }
}

function watchDigest() {
  digestBusy(true);
  clearTimeout(watchDigest.timer);
  watchDigest.timer = setTimeout(async () => {
    const state = await api("/api/news-digest");
    if (state.status === "working") return watchDigest();
    digestBusy(false);
    if (state.status === "error") return toast(state.error);
    openBrief = true;
    route();
  }, 3000);
}

async function startCollect() {
  const button = document.getElementById("refresh");
  if (button) { button.disabled = true; button.classList.add("spin"); }
  const label = document.getElementById("run-stage");
  if (label) { label.textContent = "Обновляю новости и чаты…"; label.classList.add("busy"); }
  freshShown = false;
  userRun = true;
  await api("/api/collect", {});
  waitForRun();
}

const isCalm = () => !document.hidden && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName)
  && !view.querySelector(".news-more:not([hidden]), .pick-full:not([hidden]), details[open]");

function watchUpdates() {
  // Сбор идёт сам: страница подхватывает новое, если вы ничего не читаете и не вводите
  setInterval(async () => {
    if ((location.hash.slice(1) || "today") !== "today" || !today) return;
    const status = await api("/api/status").catch(() => null);
    if (!status) return;
    const label = document.getElementById("run-stage");
    if (label && !status.running && status.last_run) label.textContent = `${ago(status.last_run.at)} назад`;
    if (status.marks.data && status.marks.data !== today.marks.data && isCalm()) route();
  }, 60000);
}

function waitForRescore() {
  // Режим включается сразу, а оценки новостей пересчитываются в фоне за несколько секунд
  clearTimeout(waitForRescore.timer);
  waitForRescore.timer = setTimeout(async () => {
    const status = await api("/api/status").catch(() => null);
    if (status?.rescoring) return waitForRescore();
    if (["today", "suggest"].includes(location.hash.slice(1) || "today") && isCalm()) route();
  }, 1000);
}

function waitForRun() {
  clearTimeout(waitForRun.timer);
  waitForRun.timer = setTimeout(async () => {
    const status = await api("/api/status");
    if (status.running) {
      const label = document.getElementById("run-stage");
      if (label && status.stage.text) label.textContent = `${status.stage.text}…`;
      if (status.stage.data_ready && !freshShown) {  // сообщения уже свежие, разбор моделью продолжается
        freshShown = true;
        route();
      }
      return waitForRun();
    }
    if (userRun || status.error) toast(status.error ? `Не обновилось: ${status.error}` : "Обновлено");
    userRun = false;
    route();
  }, 2500);
}

const feedState = { q: "", topic: "", type: "", tier: "important", days: "7", offset: 0, source: "" };

const LEVELS = ["всегда", "обычно", "тише", "никогда"];
const LEVEL_HINTS = { всегда: "показывать любое новое", обычно: "ответы и сроки", тише: "только обращения ко мне", никогда: "скрыть совсем" };

function levelOptions(current) {
  return LEVELS.map((l) => `<option value="${l}" ${l === current ? "selected" : ""}>${l}: ${LEVEL_HINTS[l]}</option>`).join("");
}

function levelPicker(source, level) {
  if (!source || !level) return "";
  return `<select class="level" data-level-for="${esc(source)}" aria-label="Важность чата">${levelOptions(level)}</select>`;
}

async function renderFeed(source) {
  const token = routeToken;
  const data = await api("/api/today");
  if (token !== routeToken) return;
  topicNames = data.topic_names;
  topicOrder = Object.keys(data.topic_names);
  feedState.source = source || "";
  feedState.offset = 0;
  if (source) feedState.tier = "";
  const topicOptions = Object.entries(topicNames).map(([id, name]) => `<option value="${esc(id)}">${esc(name)}</option>`).join("");
  const typeOptions = Object.entries(SOURCE_TYPES).map(([id, name]) => `<option value="${id}">${esc(name)}</option>`).join("");
  view.innerHTML = `
    <header class="top"><h1>${esc(source || "Лента")}</h1>${levelPicker(source, data.chat_levels[source])}${source ? '<a class="link" href="#feed">Вся лента</a>' : ""}</header>
    <div class="filters">
      <input id="f-q" type="search" placeholder="Поиск" aria-label="Поиск" value="${esc(feedState.q)}">
      <select id="f-topic" aria-label="Тема"><option value="">Все темы</option>${topicOptions}</select>
      <select id="f-type" aria-label="Источник"><option value="">Все источники</option>${typeOptions}</select>
      <select id="f-tier" aria-label="Важность"><option value="important">Важное</option><option value="">Всё</option><option value="hidden">Отсеянное</option></select>
      <select id="f-days" aria-label="Период"><option value="3">3 дня</option><option value="7">7 дней</option><option value="21">21 день</option></select>
    </div>
    <section class="panel"><div id="feed-list"></div><div id="feed-more"></div></section>`;
  for (const [id, key] of [["f-topic", "topic"], ["f-type", "type"], ["f-tier", "tier"], ["f-days", "days"]]) {
    const el = document.getElementById(id);
    el.value = feedState[key];
    el.onchange = () => { feedState[key] = el.value; feedState.offset = 0; loadFeed(); };
  }
  const search = document.getElementById("f-q");
  search.oninput = () => {
    clearTimeout(search.timer);
    search.timer = setTimeout(() => { feedState.q = search.value; feedState.offset = 0; loadFeed(); }, 250);
  };
  loadFeed();
}

async function loadFeed() {
  const params = new URLSearchParams(feedState);
  const data = await api(`/api/feed?${params}`);
  const list = document.getElementById("feed-list");
  const html = data.items.map(newsRow).join("");
  list.innerHTML = feedState.offset ? list.innerHTML + html : html || '<p class="empty">Ничего не нашлось.</p>';
  document.getElementById("feed-more").innerHTML = data.more ? '<button class="btn" id="more">Показать ещё</button>' : "";
  if (data.more) document.getElementById("more").onclick = () => { feedState.offset += 40; loadFeed(); };
}

function weekCard(title, count, rows, empty) {
  return `<div class="week-card"><div class="week-num">${count}</div><div class="week-title">${esc(title)}</div>
    ${rows.length ? `<ul>${rows.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : `<p class="note">${esc(empty)}</p>`}</div>`;
}

function weekBlock(week) {
  const late = week.overdue.map((t) => `${taskTitle(t)}, ${whenLabel(t.due).text}`);
  const people = week.unanswered.map((c) => (c.preview ? `${c.name}: ${c.preview}` : c.name));
  const topics = week.topics.map((t) => `${t.name}: ${t.notes}`);
  const extras = [week.in_work ? `В работе: ${week.in_work}` : "", week.waiting_for ? `Жду: ${week.waiting_for}` : "",
    week.from_chats ? `Новых дел из чатов: ${week.from_chats}` : ""].filter(Boolean).join(" · ");
  return `<section class="panel">
    <div class="panel-head">${icon("today")}Неделя</div>
    <div class="week">
      ${weekCard("Сделано", week.done_total, week.done, "Отметки считаются с момента, как Пульс начал их вести.")}
      ${weekCard("Зависло", week.overdue_total, late, "Просроченных дел нет.")}
      ${weekCard("Ждут ответа", week.unanswered_total, people, "Никто не ждёт.")}
      ${weekCard("Темы заметок", week.topics.length, topics, "На этой неделе заметки не менялись.")}
    </div>
    ${extras ? `<p class="note">${esc(extras)}</p>` : ""}
  </section>`;
}

function sparkline(row, days) {
  const height = 24, barWidth = 6, gap = 2;
  const max = Math.max(...row.days, 1);
  const bars = row.days.map((value, index) => {
    const barHeight = value ? Math.max(3, Math.round((value / max) * height)) : 1;
    const x = index * (barWidth + gap);
    const label = `${new Date(days[index]).toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}: ${value}`;
    return `<rect class="hit" data-tip="${esc(label)}" x="${x - 1}" y="0" width="${barWidth + gap}" height="${height}"></rect>
      <rect class="${value ? "" : "zero"}" x="${x}" y="${height - barHeight}" width="${barWidth}" height="${barHeight}" rx="${value ? 2 : 0}" pointer-events="none"></rect>`;
  }).join("");
  const width = days.length * (barWidth + gap);
  return `<svg class="spark" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">${bars}</svg>`;
}

function delta(row, enoughHistory) {
  if (!enoughHistory || !row.prev) return "—";
  const change = Math.round(((row.week - row.prev) / row.prev) * 100);
  return `<span class="${change > 0 ? "delta-up" : "delta-down"}">${change > 0 ? "+" : ""}${change}%</span>`;
}

async function renderAnalytics() {
  const token = routeToken;
  const data = await api("/api/analytics");
  if (token !== routeToken) return;
  const totalNews = data.topics.reduce((sum, t) => sum + t.week, 0) || 1;
  const totalNotes = data.topics.reduce((sum, t) => sum + t.notes, 0) || 1;
  const maxShare = Math.max(...data.topics.map((t) => Math.max(t.week / totalNews, t.notes / totalNotes)), 0.01);
  const enoughHistory = data.tracked_since && Date.now() - new Date(data.tracked_since) >= 14 * 864e5;
  const rows = [...data.topics].sort((a, b) => b.week - a.week).map((t) => `<tr>
      <td>${esc(t.name)}</td>
      <td>${sparkline(t, data.days)}</td>
      <td class="num">${t.week}</td>
      <td class="num">${t.important}</td>
      <td class="num">${delta(t, enoughHistory)}</td>
      <td class="share" title="Новости ${Math.round((t.week / totalNews) * 100)}%, заметки ${Math.round((t.notes / totalNotes) * 100)}%">
        <div class="news-bar" style="width:${(t.week / totalNews / maxShare) * 100}%"></div>
        <div class="work-bar" style="width:${(t.notes / totalNotes / maxShare) * 100}%"></div>
      </td></tr>`).join("");
  const sources = data.sources.filter((s) => s.type !== "tg_private").map((s) => `<tr>
      <td>${esc(s.name)}</td>
      <td class="num">${s.total}</td>
      <td class="num">${s.useful}</td>
      <td class="num">${s.total ? Math.round((s.useful / s.total) * 100) + "%" : "—"}</td>
      <td>${s.last_error ? '<span class="when bad">ошибка</span>' : ""}</td></tr>`).join("");
  view.innerHTML = `
    <header class="top"><h1>Аналитика</h1></header>
    <div class="stack">
      ${weekBlock(data.week)}
      <section class="panel">
        <div class="panel-head">${icon("chart")}Мир и вы</div>
        <div class="legend"><span><i style="background:var(--series-1)"></i>Новости за 7 дней</span><span><i style="background:var(--series-2)"></i>Ваши заметки за 14 дней</span></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Тема</th><th>14 дней</th><th class="num">Неделя</th><th class="num">Важных</th><th class="num">К прошлой</th><th>Доли</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
      </section>
      <details class="group">
        <summary>${icon("layers")}Польза источников${icon("chev", "chev")}</summary>
        <div class="group-body table-wrap"><table>
          <thead><tr><th>Источник</th><th class="num">Всего</th><th class="num">Важных</th><th class="num">Доля</th><th></th></tr></thead>
          <tbody>${sources}</tbody></table></div>
      </details>
    </div>`;
}

let draft = null;

async function renderSettings() {
  const token = routeToken;
  const data = await api("/api/settings");
  if (token !== routeToken) return;
  draft = data.config;
  topicOrder = draft.topics.map((t) => t.id);
  paintSettings(data);
}

function group(iconName, title, count, body, open = false) {
  return `<details class="group" data-key="${esc(title)}" ${open ? "open" : ""}><summary>${icon(iconName)}${esc(title)}<span class="count">${esc(count)}</span>${icon("chev", "chev")}</summary><div class="group-body">${body}</div></details>`;
}

function openChatsList(data) {
  if (!data.public_chats.length) return '<p class="note">Среди выбранных чатов открытых групп пока нет. Добавьте нужные в разделе «Чаты Telegram».</p>';
  const closed = new Set(draft.privacy.claude_closed_chats || []);
  return `<div class="open-chats">${data.public_chats.map((name) => `<label class="src-row"><input type="checkbox" data-claude-chat="${esc(name)}" ${closed.has(name) ? "" : "checked"}><span class="grow">${esc(name)}</span></label>`).join("")}</div>`;
}

function privacyBody(data) {
  const model = data.local_model;
  return `<div class="status-row">${icon("check")}Текст чатов хранится только на этом компьютере</div>
    <div class="status-row">${icon("check")}В сводку Claude попадают только числа</div>
    <div class="status-row">${icon("check")}Claude может читать новости, посты каналов и открытые группы. Закрытые группы и личные чаты нет</div>
    <div class="status-row ${model ? "" : "off"}">${icon(model ? "check" : "shield")}${model ? `Локальная модель: ${esc(model)}` : "Локальная модель не установлена, работают правила"}</div>
    <label class="switch plain"><input type="checkbox" id="claude-enabled" ${draft.claude.enabled ? "checked" : ""}>
      <span>Просить Claude делать саммари, если локальная модель не справилась<small class="soft">Только публичные статьи. Нужен разовый вход: claude_login.bat</small></span></label>
    <label class="switch plain"><input type="checkbox" id="auto-deadlines" ${draft.deadlines.auto_add ? "checked" : ""}>
      <span>Добавлять сроки из чатов в «Актуальное»<small class="soft">Раздел «Сроки из чатов». Его могут прочитать сессии Claude, которые открывают хранилище</small></span></label>
    <label class="switch plain"><input type="checkbox" id="auto-promises" ${draft.deadlines.promises !== false ? "checked" : ""}>
      <span>Находить дела в моих сообщениях<small class="soft">«Скину завтра» в чатах и «купить билеты» в Избранном попадают в дела. Раздел тот же, сообщения хранятся только на компьютере</small></span></label>
    <label class="switch plain"><input type="checkbox" id="saved-messages" ${draft.deadlines.saved !== false ? "checked" : ""}>
      <span>Читать Избранное Telegram<small class="soft">Записи себе становятся делами, если похожи на них. Отдельно выбирать Избранное в списке чатов не нужно</small></span></label>
    <label class="switch plain"><input type="checkbox" id="claude-public" ${draft.privacy.claude_public_chats !== false ? "checked" : ""}>
      <span>Отвечать Claude по открытым группам<small class="soft">Только группы с публичной ссылкой t.me: их читает любой без вступления. Галочкой ниже можно закрыть любую</small></span></label>
    ${openChatsList(data)}
    <label class="field">О себе для Claude: навыки, опыт, что ищете<textarea id="claude-profile" placeholder="Например: студент, 3 курс; пишу тексты, делаю опросы и анализ данных в Python; ищу удалённые заказы до 10 часов в неделю">${esc(draft.claude.profile || "")}</textarea><small class="soft">Уходит в Claude вместе с вопросом. Локальной модели не нужно</small></label>
    <label class="switch"><input type="checkbox" id="claude-chats" ${draft.privacy.claude_reads_chats ? "checked" : ""}>
      <span>Разрешить Claude читать отмеченные чаты<small>Текст будет уходить на серверы Anthropic</small></span></label>`;
}

let chatGroups = [];
let chatSnapshot = null;  // выбор до флажка «Выбрать все чаты»: снятие флажка возвращает его

function chatBulk(tg) {
  const chosen = new Set(draft.tg_private.map((c) => c.id));
  const picked = tg.dialogs.filter((d) => chosen.has(d.id)).length;
  const all = tg.dialogs.length > 0 && picked === tg.dialogs.length;
  if (!all) chatSnapshot = null;
  return `<div class="chat-bulk">
      <label class="chat-all"><input type="checkbox" id="chat-all" ${all ? "checked" : ""}><span>Выбрать все чаты</span></label>
      <button class="btn" id="chat-none" type="button" ${picked ? "" : "disabled"}>Снять все чаты</button>
      <span class="soft" id="chat-picked">Выбрано ${picked} из ${tg.dialogs.length}</span></div>`;
}

function syncChatBulk(tg) {
  const box = document.getElementById("chat-all");
  if (!box) return;
  const chosen = new Set(draft.tg_private.map((c) => c.id));
  const picked = tg.dialogs.filter((d) => chosen.has(d.id)).length;
  const all = picked === tg.dialogs.length;
  if (!all) chatSnapshot = null;  // после ручной правки «до флажка» уже нет: остаётся то, что выбрано сейчас
  box.checked = all;
  document.getElementById("chat-none").disabled = !picked;
  document.getElementById("chat-picked").textContent = `Выбрано ${picked} из ${tg.dialogs.length}`;
}

function telegramBody(tg) {
  if (!tg.connected) return '<p class="note">Запустите <code>telegram_login.bat</code>. Подключение только читает и не помечает сообщения прочитанными.</p>';
  const chosen = new Set(draft.tg_private.map((c) => c.id));
  const levelOf = (id) => draft.tg_private.find((c) => c.id === id)?.level || "обычно";
  const row = (d) => `<label class="src-row" data-chat-name="${esc(d.name.toLowerCase())}">
      <input type="checkbox" data-chat="${d.id}" ${chosen.has(d.id) ? "checked" : ""}>
      <span class="grow">${esc(d.name)} <small>${esc(d.kind)}${d.archived ? " · архив" : ""}</small></span>
      ${chosen.has(d.id) ? `<select class="level" data-draft-level="${d.id}" aria-label="Важность">${LEVELS.map((l) => `<option ${l === levelOf(d.id) ? "selected" : ""}>${l}</option>`).join("")}</select>` : ""}</label>`;
  // Как в Telegram: папки по порядку, чат из нескольких папок виден в каждой
  chatGroups = (tg.folders || []).map((title) => ({ title, items: tg.dialogs.filter((d) => (d.folders || []).includes(title)) }));
  chatGroups.push({ title: tg.folders?.length ? "Без папки" : "Все чаты", items: tg.dialogs.filter((d) => !(d.folders || []).length) });
  const groups = chatGroups.map((g, i) => (g.items.length ? `<details class="folder" data-key="папка:${esc(g.title)}" ${chatGroups.length === 1 ? "open" : ""}>
      <summary>${esc(g.title)}<span class="count">${g.items.filter((d) => chosen.has(d.id)).length} из ${g.items.length}</span>${icon("chev", "chev")}</summary>
      <div class="folder-tools"><button type="button" class="linkish" data-folder-all="${i}">Выбрать все</button><button type="button" class="linkish" data-folder-none="${i}">Снять все</button></div>
      <div class="chatlist">${g.items.map(row).join("")}</div></details>` : "")).join("");
  return `<div class="tg-tools"><input id="chat-filter" class="grow-input" placeholder="Найти чат" aria-label="Найти чат">
      <button class="btn" id="tg-refresh" type="button">Обновить список</button></div>
    ${chatBulk(tg)}
    <p class="note">Всего чатов: ${tg.dialogs.length}, папок: ${(tg.folders || []).length}. Архивные тоже здесь. Выбор сохраняется кнопкой «Сохранить» внизу.</p>
    ${groups}`;
}

function setChat(tg, id, on) {
  draft.tg_private = draft.tg_private.filter((c) => c.id !== id);
  if (on) {
    const dialog = tg.dialogs.find((d) => d.id === id);
    draft.tg_private.push({ id, name: dialog.name, kind: dialog.kind, level: "обычно" });
  }
  view.querySelectorAll(`[data-chat="${id}"]`).forEach((box) => { box.checked = on; });
}

function modesBody() {
  return draft.modes.map((m, i) => `<details class="topic-row">
      <summary>${esc(m.name)}<small>новостей ${m.budget}</small></summary>
      <div class="two">
        <label class="field">Название<input data-mode-field="name" data-index="${i}" value="${esc(m.name)}"></label>
        <label class="field">Новостей на экране<input type="number" min="1" max="20" data-mode-field="budget" data-index="${i}" value="${m.budget}"></label>
      </div>
      <label class="field">Чаты<select data-mode-field="chats" data-index="${i}">
        <option value="all" ${m.chats === "all" ? "selected" : ""}>Все по их важности</option>
        <option value="important" ${m.chats === "important" ? "selected" : ""}>Только «всегда»</option></select></label>
      ${draft.topics.map((t) => `<label class="mode-topic"><span>${esc(t.name)}</span>
        <input type="range" min="0" max="2" step="0.1" data-mode-topic="${esc(t.id)}" data-index="${i}" value="${m.topics[t.id] ?? 1}">
        <output>${m.topics[t.id] ?? 1}</output></label>`).join("")}
    </details>`).join("");
}

function obsidianBody(data) {
  const state = data.obsidian;
  const status = state.vault_found
    ? `<div class="status-row">${icon("check")}Папка найдена${state.note_found ? ", заметка для дел есть" : ". Заметку для дел Пульс создаст сам, когда вы добавите первую задачу"}</div>`
    : `<div class="status-row off">${icon("shield")}Такой папки нет. Если Obsidian у вас не стоит, ничего страшного: Пульс создаст папку и будет хранить дела в обычном файле</div>`;
  return `${status}
    <label class="field">Папка хранилища Obsidian<input id="vault" value="${esc(draft.vault)}" placeholder="C:\\Users\\Имя\\Documents\\Obsidian Vault" autocomplete="off">
      <small class="soft">Откройте папку хранилища в Проводнике, щёлкните по адресной строке сверху, скопируйте адрес и вставьте сюда</small></label>
    <label class="field">Заметка для дел<input id="agenda-note" value="${esc(draft.agenda_note)}" placeholder="Задачи.md" autocomplete="off">
      <small class="soft">Путь внутри хранилища, например Задачи.md. Пульс пишет только в эту заметку, остальные лишь читает</small></label>`;
}

function updatesBody(data) {
  return `<div class="two">
      <label class="field">Чаты, раз в минут<input type="number" min="1" id="chat-interval" value="${draft.chat_interval_min}"></label>
      <label class="field">Новости, раз в минут<input type="number" min="5" id="news-interval" value="${draft.news_interval_min}"></label>
      <label class="field">Разбор моделью, раз в минут<input type="number" min="5" id="model-interval" value="${draft.model_interval_min}"></label>
      <label class="field">Полное обновление, час дня<input type="number" min="0" max="23" id="full-hour" value="${draft.full_collect_hour}"></label>
    </div>
    <p class="note">Обычная проверка смотрит один список диалогов и читает только чаты, где появилось новое. Полное обновление раз в сутки перечитывает все чаты за три дня.</p>
    <button class="btn" id="full-run" type="button">Полное обновление сейчас</button>
    <label class="switch plain first"><input type="checkbox" id="notify-on" ${draft.notify.enabled !== false ? "checked" : ""}>
      <span>Уведомления Windows<small class="soft">Срок завтра и люди, которые ждут ответа больше суток. Ночью молчат</small></span></label>
    <label class="switch plain"><input type="checkbox" id="autostart-on" ${data.autostart ? "checked" : ""}>
      <span>Запускать Пульс при входе в Windows<small class="soft">Работает без окна, адрес тот же. Применяется сразу</small></span></label>`;
}

function interestsBody(data) {
  const prefs = draft.interests;
  const chips = prefs.watch.map((name, i) => `<span class="watch-chip">${esc(name)}<button type="button" class="tool" data-unwatch="${i}" aria-label="Не следить за ${esc(name)}">${icon("close")}</button></span>`).join("");
  return `<p class="note">За кем следить. Локальная модель прочитает их сообщения и посты и коротко перескажет интересное в блоке «Для вас». То же можно сказать в окне «Спросить»: «Следи за …», «Не следи за …».</p>
    <div class="watch-list">${chips || '<span class="note">Пока ни за кем</span>'}</div>
    <div class="adder"><input id="watch-add" list="known-names" placeholder="Канал, чат или человек" aria-label="За кем следить" autocomplete="off">
      <datalist id="known-names">${data.known_names.map((n) => `<option value="${esc(n)}">`).join("")}</datalist>
      <button class="btn" id="watch-add-btn" type="button">Следить</button></div>
    <label class="field">Что мне интересно, по теме на строку<textarea id="about" placeholder="философия сознания&#10;нейроинтерфейсы">${esc(prefs.about.join("\n"))}</textarea></label>`;
}

let settingsData = null;
function renderSettingsAgain() {
  if (settingsData) paintSettings(settingsData);
}

function learnedList(title, rows) {
  if (!rows.length) return "";
  const line = (r) => `<span class="${r.score > 0 ? "learned-up" : "learned-down"}">${esc(r.name)} ${r.score > 0 ? "+" : "−"}${Math.abs(r.score)}</span>`;
  return `<p class="note">${esc(title)}: ${rows.map(line).join(", ")}</p>`;
}

function learnedBody(learned) {
  const chats = learned.chats.map((c) => `${esc(c.name)}: ${c.count}${c.quiet ? ", стал тише" : ""}`).join("; ");
  const empty = !learned.marks && !learned.chats.length;
  if (empty) return '<p class="note">Пока нечему. Отмечайте новости кнопкой «Меньше такого», а чаты кнопкой «Не важно».</p>';
  return `${learnedList("Темы", learned.topics)}${learnedList("Источники", learned.sources)}
    ${chats ? `<p class="note">Чаты, отмеченные неважными: ${chats}</p>` : ""}
    <button class="btn" id="forget" type="button">Забыть всё и начать заново</button>`;
}

async function paintRemote() {
  const box = document.getElementById("remote-box");
  if (!box) return;
  const state = await api("/api/remote");
  box.innerHTML = `<label class="switch plain first"><input type="checkbox" id="remote-on" ${state.enabled ? "checked" : ""}>
      <span>Открыть Пульс для телефона в этой сети<small>Чаты будут видны по ссылке с ключом. Соединение не шифруется: в общем Wi-Fi выключайте</small></span></label>
    ${state.enabled && state.qr ? `<div class="remote-qr">${state.qr}<div><p class="note">Отсканируйте камерой телефона. Телефон и компьютер должны быть в одной сети.</p>
      <p class="note"><code>${esc(state.address)}</code></p><button class="btn" id="remote-reset" type="button">Сбросить доступ всех телефонов</button></div></div>` : ""}
    ${state.enabled && !state.qr ? '<p class="note">Компьютер не подключён к сети.</p>' : ""}`;
  document.getElementById("remote-on").onchange = async (event) => {
    try { await api("/api/remote", { enabled: event.target.checked }); } catch (error) { toast(error.message); }
    paintRemote();
  };
  const reset = document.getElementById("remote-reset");
  if (reset) reset.onclick = async () => { await api("/api/remote", { reset: true }); toast("Старые ссылки больше не работают"); paintRemote(); };
}

function paintSettings(data) {
  settingsData = data;
  // Перерисовка после «Выбрать все» не должна сворачивать то, что вы открыли
  const opened = [...view.querySelectorAll("details[data-key]")].map((d) => [d.dataset.key, d.open]);
  const scroll = scrollY;
  const topics = draft.topics.map((t, i) => `<details class="topic-row">
      <summary><span class="dot" style="--dot:${topicColor(t.id)}"></span>${esc(t.name)}<small>вес ${t.weight}</small></summary>
      <div class="two">
        <label class="field">Название<input data-topic="${i}" data-key="name" value="${esc(t.name)}"></label>
        <label class="field">Вес<input type="range" min="0" max="2" step="0.1" data-topic="${i}" data-key="weight" value="${t.weight}"></label>
      </div>
      <label class="field">Ключевые слова через запятую, * значит любое продолжение<textarea data-topic="${i}" data-key="keywords">${esc(t.keywords.join(", "))}</textarea></label>
      <label class="field">Исключить<input data-topic="${i}" data-key="exclude" value="${esc((t.exclude || []).join(", "))}"></label>
      <button class="btn" data-remove-topic="${i}">Удалить тему</button>
    </details>`).join("");
  const feeds = draft.feeds.map((f, i) => `<div class="src-row">
      <input type="checkbox" aria-label="Включить ${esc(f.name)}" data-feed-on="${i}" ${f.enabled === false ? "" : "checked"}>
      <span class="grow">${esc(f.name)}</span>
      <button class="btn" data-remove-feed="${i}">Убрать</button></div>`).join("");
  const channels = draft.tg_public.map((c, i) => `<div class="src-row"><span class="grow">@${esc(c.username)}</span>
      <button class="btn" data-remove-channel="${i}">Убрать</button></div>`).join("");
  const topicSelect = draft.topics.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
  view.innerHTML = `
    <header class="top"><h1>Настройки</h1></header>
    ${group("shield", "Приватность", "", privacyBody(data), true)}
    ${group("chat", "Чаты Telegram", data.telegram.connected ? `${draft.tg_private.length} выбрано` : "не подключён", telegramBody(data.telegram))}
    ${group("sliders", "Режимы внимания", String(draft.modes.length), `<p class="note">Множитель темы: 1 как обычно, 2 вдвое важнее, 0 скрыть.</p>${modesBody()}`)}
    ${group("star", "Мои интересы", String(draft.interests.watch.length), interestsBody(data), true)}
    ${group("note", "Obsidian и дела", data.obsidian.vault_found ? "" : "папки нет", obsidianBody(data))}
    ${group("refresh", "Обновление и уведомления", "", updatesBody(data))}
    ${group("star", "Чему научился Пульс", String(data.learned.marks + data.learned.chats.length), learnedBody(data.learned))}
    ${group("today", "Телефон", "", '<div id="remote-box"></div>')}
    ${group("layers", "Темы", String(draft.topics.length), `${topics}
      <div class="adder"><input id="add-topic" placeholder="Новая тема" aria-label="Новая тема"><button class="btn" id="add-topic-btn">Добавить</button></div>`)}
    ${group("feed", "Источники", String(draft.feeds.length + draft.tg_public.length), `${feeds}${channels}
      <div class="adder"><input id="add-channel" placeholder="Публичный канал, например durov" aria-label="Имя канала"><button class="btn" id="add-channel-btn">Добавить</button></div>
      <div class="adder"><input id="add-query" placeholder="Новости по запросу" aria-label="Запрос"><select id="add-query-topic" aria-label="Тема запроса">${topicSelect}</select></div>
      <div class="adder"><input id="add-feed" placeholder="Или адрес RSS" aria-label="Адрес ленты"><button class="btn" id="add-feed-btn">Добавить</button></div>`)}
    ${group("sliders", "Отбор", "", `<label class="field">Новостей на экране<input type="number" min="3" max="20" id="budget" value="${draft.budget_main}"></label>
      <label class="field">Как вас называют в чатах<input id="names" value="${esc(draft.me.names.join(", "))}"></label>
      <label class="field">Чёрный список<textarea id="blacklist">${esc(draft.blacklist.join(", "))}</textarea></label>`)}
    <div class="save-bar"><button class="btn primary" id="save">Сохранить</button></div>`;
  for (const [key, open] of opened) {
    const details = [...view.querySelectorAll("details[data-key]")].find((d) => d.dataset.key === key);
    if (details) details.open = open;
  }
  if (opened.length) scrollTo(0, scroll);
  bindSettings(data);
  paintRemote().catch(() => {});
}

const splitList = (value) => value.split(",").map((s) => s.trim()).filter(Boolean);

function readSimpleFields() {
  draft.budget_main = Number(document.getElementById("budget").value);
  draft.chat_interval_min = Number(document.getElementById("chat-interval").value);
  draft.news_interval_min = Number(document.getElementById("news-interval").value);
  draft.model_interval_min = Number(document.getElementById("model-interval").value);
  draft.full_collect_hour = Number(document.getElementById("full-hour").value);
  draft.notify.enabled = document.getElementById("notify-on").checked;
  draft.deadlines.promises = document.getElementById("auto-promises").checked;
  draft.deadlines.saved = document.getElementById("saved-messages").checked;
  draft.interests.about = document.getElementById("about").value.split("\n").map((s) => s.trim()).filter(Boolean);
  draft.me.names = splitList(document.getElementById("names").value);
  draft.vault = document.getElementById("vault").value.trim();
  draft.agenda_note = document.getElementById("agenda-note").value.trim();
  draft.blacklist = splitList(document.getElementById("blacklist").value);
  draft.privacy.claude_reads_chats = document.getElementById("claude-chats").checked;
  draft.privacy.claude_public_chats = document.getElementById("claude-public").checked;
  draft.privacy.claude_closed_chats = [...view.querySelectorAll("[data-claude-chat]")].filter((box) => !box.checked).map((box) => box.dataset.claudeChat);
  draft.claude.profile = document.getElementById("claude-profile").value;
  draft.deadlines.auto_add = document.getElementById("auto-deadlines").checked;
  draft.claude.enabled = document.getElementById("claude-enabled").checked;
}

function bindSettings(data) {
  const tg = data.telegram;
  const repaint = () => { readSimpleFields(); paintSettings(data); };
  view.querySelectorAll("[data-topic]").forEach((el) => {
    el.oninput = () => {
      const topic = draft.topics[Number(el.dataset.topic)];
      const key = el.dataset.key;
      if (key === "weight") topic.weight = Number(el.value);
      else if (key === "name") topic.name = el.value;
      else topic[key] = splitList(el.value);
    };
  });
  view.querySelectorAll("[data-mode-field]").forEach((el) => {
    el.oninput = () => {
      const mode = draft.modes[Number(el.dataset.index)];
      mode[el.dataset.modeField] = el.dataset.modeField === "budget" ? Number(el.value) : el.value;
    };
  });
  view.querySelectorAll("[data-mode-topic]").forEach((el) => {
    el.oninput = () => {
      draft.modes[Number(el.dataset.index)].topics[el.dataset.modeTopic] = Number(el.value);
      el.nextElementSibling.textContent = el.value;
    };
  });
  view.querySelectorAll("[data-draft-level]").forEach((el) => {
    el.onchange = () => { draft.tg_private.find((c) => c.id === Number(el.dataset.draftLevel)).level = el.value; };
  });
  view.querySelectorAll("[data-remove-topic]").forEach((el) => { el.onclick = () => { draft.topics.splice(Number(el.dataset.removeTopic), 1); repaint(); }; });
  view.querySelectorAll("[data-remove-feed]").forEach((el) => { el.onclick = () => { draft.feeds.splice(Number(el.dataset.removeFeed), 1); repaint(); }; });
  view.querySelectorAll("[data-remove-channel]").forEach((el) => { el.onclick = () => { draft.tg_public.splice(Number(el.dataset.removeChannel), 1); repaint(); }; });
  view.querySelectorAll("[data-feed-on]").forEach((el) => { el.onchange = () => { draft.feeds[Number(el.dataset.feedOn)].enabled = el.checked; }; });
  view.querySelectorAll("[data-chat]").forEach((el) => {
    el.onchange = () => { setChat(tg, Number(el.dataset.chat), el.checked); syncChatBulk(tg); };
  });
  const folderPick = (index, on) => {
    chatGroups[index].items.forEach((d) => setChat(tg, d.id, on));
    readSimpleFields();
    paintSettings(data);
    toast(on ? `Выбрано: ${chatGroups[index].items.length}. Нажмите «Сохранить»` : "Снято. Нажмите «Сохранить»");
  };
  view.querySelectorAll("[data-folder-all]").forEach((el) => { el.onclick = () => folderPick(Number(el.dataset.folderAll), true); });
  view.querySelectorAll("[data-folder-none]").forEach((el) => { el.onclick = () => folderPick(Number(el.dataset.folderNone), false); });
  const allChats = document.getElementById("chat-all");
  if (allChats) allChats.onchange = () => {
    readSimpleFields();
    if (allChats.checked) {
      chatSnapshot = draft.tg_private.map((c) => c.id);
      const have = new Set(chatSnapshot);
      tg.dialogs.forEach((d) => { if (!have.has(d.id)) draft.tg_private.push({ id: d.id, name: d.name, kind: d.kind, level: "обычно" }); });
      paintSettings(data);
      chatSnapshot = chatSnapshot || [];
      toast(`Выбраны все чаты: ${tg.dialogs.length}. Нажмите «Сохранить»`);
    } else if (chatSnapshot) {
      const before = new Set(chatSnapshot);
      draft.tg_private = draft.tg_private.filter((c) => before.has(c.id));  // важность, заданную за это время, не теряем
      chatSnapshot = null;
      paintSettings(data);
      toast(`Вернул прежний выбор: ${draft.tg_private.length}. Нажмите «Сохранить»`);
    } else {
      allChats.checked = true;  // все чаты были выбраны ещё до флажка, возвращать нечего
      toast("Все чаты были выбраны и раньше. Снять их можно кнопкой «Снять все чаты»");
    }
  };
  const noChats = document.getElementById("chat-none");
  if (noChats) noChats.onclick = () => {
    readSimpleFields();
    const before = draft.tg_private;
    draft.tg_private = [];
    chatSnapshot = null;
    paintSettings(data);
    toast(`Сняты все чаты: ${before.length}. Нажмите «Сохранить»`, "Вернуть", () => {
      draft.tg_private = before;
      paintSettings(data);
    });
  };
  const refreshChats = document.getElementById("tg-refresh");
  if (refreshChats) refreshChats.onclick = async () => {
    refreshChats.disabled = true;
    refreshChats.textContent = "Обновляю…";
    try {
      const state = await api("/api/tg-dialogs", {});
      readSimpleFields();
      const fresh = await api("/api/settings");
      data.telegram = fresh.telegram;
      paintSettings(data);
      toast(`Чатов: ${state.dialogs}, папок: ${state.folders}`);
    } catch (error) { toast(error.message); refreshChats.disabled = false; refreshChats.textContent = "Обновить список"; }
  };
  document.getElementById("watch-add-btn").onclick = () => {
    const name = document.getElementById("watch-add").value.trim();
    if (!name) return;
    readSimpleFields();
    if (!draft.interests.watch.some((w) => w.toLowerCase() === name.toLowerCase())) draft.interests.watch.push(name);
    paintSettings(data);
    toast("Добавлено. Нажмите «Сохранить»");
  };
  document.getElementById("full-run").onclick = async () => {
    try { await api("/api/collect", { full: true }); toast("Полное обновление идёт, это пара минут"); } catch (error) { toast(error.message); }
  };
  document.getElementById("autostart-on").onchange = async (event) => {
    try {
      const state = await api("/api/autostart", { enabled: event.target.checked });
      toast(state.enabled ? "Пульс будет запускаться при входе в Windows" : "Автозапуск выключен");
    } catch (error) { toast(error.message); event.target.checked = !event.target.checked; }
  };
  const forget = document.getElementById("forget");
  if (forget) forget.onclick = async () => {
    try { await api("/api/learned", {}); toast("Отметки сброшены"); renderSettings(); } catch (error) { toast(error.message); }
  };
  const chatFilter = document.getElementById("chat-filter");
  if (chatFilter) chatFilter.oninput = () => {
    const query = chatFilter.value.toLowerCase();
    view.querySelectorAll("[data-chat-name]").forEach((row) => { row.style.display = row.dataset.chatName.includes(query) ? "" : "none"; });
    view.querySelectorAll("details.folder").forEach((folder) => {
      const shown = [...folder.querySelectorAll("[data-chat-name]")].some((row) => row.style.display !== "none");
      folder.style.display = shown ? "" : "none";
      folder.open = Boolean(query) && shown;
    });
  };
  document.getElementById("add-channel-btn").onclick = () => {
    const name = document.getElementById("add-channel").value.trim().replace(/^@|^https?:\/\/t\.me\/(s\/)?/g, "");
    if (name) { draft.tg_public.push({ username: name }); repaint(); }
  };
  document.getElementById("add-topic-btn").onclick = () => {
    const name = document.getElementById("add-topic").value.trim();
    if (name) { draft.topics.push({ id: "t" + Date.now().toString(36), name, weight: 1, keywords: [name], exclude: [] }); repaint(); }
  };
  document.getElementById("add-feed-btn").onclick = () => {
    const query = document.getElementById("add-query").value.trim();
    const url = document.getElementById("add-feed").value.trim();
    const topic = document.getElementById("add-query-topic").value;
    if (query) {
      const search = encodeURIComponent(`${query} when:3d`);
      draft.feeds.push({ name: `Поиск: ${query}`, url: `https://news.google.com/rss/search?q=${search}&hl=ru&gl=RU&ceid=RU:ru`, topic });
    } else if (url) {
      draft.feeds.push({ name: new URL(url).hostname.replace(/^www\./, ""), url, topic });
    } else return;
    repaint();
  };
  document.getElementById("save").onclick = async () => {
    const before = `${draft.vault}|${draft.agenda_note}`;
    readSimpleFields();
    try {
      await api("/api/settings", draft);
      toast("Сохранено");
      if (before !== `${draft.vault}|${draft.agenda_note}`) renderSettings();  // показать, нашлась ли новая папка
    } catch (error) { toast(error.message); }
  };
}

view.addEventListener("change", async (event) => {
  if (event.target.closest("[data-sg-show]")) {
    const kinds = [...document.querySelectorAll("[data-sg-show]:checked")].map((box) => box.dataset.sgShow);
    try { await api("/api/suggest-kinds", { mode: suggestData.mode.id, kinds }); route(); } catch (error) { toast(error.message); }
    return;
  }
  const levelField = event.target.closest("[data-level-for]");
  if (levelField) {
    try { await api("/api/chat-level", { name: levelField.dataset.levelFor, level: levelField.value }); toast("Важность чата сохранена"); }
    catch (error) { toast(error.message); }
    return;
  }
  const done = event.target.closest("[data-done]");
  if (done) return completeTask(done.dataset.done, done.closest(".todo, .task-row, .do-row, .kcard"));
  const dateField = event.target.closest("[data-due]");
  if (dateField) {
    const task = taskIndex[dateField.dataset.due];
    try {
      await taskRequest({ action: "edit", line: task.meta.line, raw: task.meta.raw, text: editableText(task), due: dateField.value || null });
      route();
    } catch (error) { toast(error.message); route(); }
  }
});

view.addEventListener("submit", (event) => {
  const form = event.target.closest("[data-col-form]");
  if (!form) return;
  event.preventDefault();
  board.adding = null;
  addTask(form.querySelector("input"), null, statusOf(form.dataset.colForm));
});

view.addEventListener("focusout", (event) => {
  const form = event.target.closest("[data-col-form]");
  if (form && !event.target.value.trim()) setTimeout(() => { if (board.adding === form.dataset.colForm) { board.adding = null; paintBoard(); } }, 150);
});

view.addEventListener("click", async (event) => {
  const glanceLink = event.target.closest("[data-glance]");
  if (glanceLink) board.only = glanceLink.dataset.glance;
  const scrollTo = event.target.closest("[data-scroll]");
  if (scrollTo) return document.getElementById(scrollTo.dataset.scroll)?.scrollIntoView({ behavior: calmMotion() ? "auto" : "smooth" });
  const kdone = event.target.closest("[data-kdone]");
  if (kdone) { kdone.classList.add("on"); return completeTask(kdone.dataset.kdone, kdone.closest(".kcard")); }
  const shift = event.target.closest("[data-shift]");
  if (shift) return moveTask(taskIndex[shift.dataset.shift].meta, statusOf(shift.dataset.to));
  const dueMenu = event.target.closest("[data-due-menu]");
  if (dueMenu) return document.getElementById("due-menu")?.dataset.task === dueMenu.dataset.dueMenu ? closeDueMenu() : openDueMenu(dueMenu);
  if (event.target.closest("[data-done-more]")) { board.doneOpen = !board.doneOpen; return paintBoard(); }
  const only = event.target.closest("[data-board-only]");
  if (only) { board.only = only.dataset.boardOnly; return paintBoard(); }
  const colAdd = event.target.closest("[data-col-add]");
  if (colAdd) { board.adding = board.adding === colAdd.dataset.colAdd ? null : colAdd.dataset.colAdd; return paintBoard(); }
  const chatDone = event.target.closest("[data-chat-done]");
  if (chatDone) {
    event.preventDefault();
    try {
      await api("/api/chat-done", { name: chatDone.dataset.chatDone, why: "done" });
      toast("Убрано из списка");
      await vanish(chatDone.closest(".do-row"));
      route();
    } catch (error) { toast(error.message); }
    return;
  }
  const chatSkip = event.target.closest("[data-chat-skip]");
  if (chatSkip) {
    event.preventDefault();
    const name = chatSkip.dataset.chatSkip;
    try {
      const state = await api("/api/chat-done", { name, why: "unimportant" });
      if (state.quiet) toast(`Чат «${name}» теперь «тише»`, "Вернуть", async () => { await api("/api/chat-level", { name, level: "обычно" }); route(); });
      else toast("Учту. После трёх таких отметок чат станет тише");
      await vanish(chatSkip.closest(".do-row"));
      route();
    } catch (error) { toast(error.message); }
    return;
  }
  const sgRate = event.target.closest("[data-sg-rate]");
  if (sgRate) {
    const card = sgRate.closest("[data-sg]");
    const value = Number(sgRate.dataset.sgRate);
    const liked = value > 0 && !sgRate.classList.contains("on");
    try {
      await api("/api/suggest-rate", { uid: card.dataset.sg, feedback: value < 0 ? -1 : liked ? 1 : 0 });
      if (value < 0) { toast("Убрано. Похожее будет ниже"); await vanish(card); }
      else { sgRate.classList.toggle("on", liked); if (liked) toast("Учту: похожее будет выше"); }
    } catch (error) { toast(error.message); }
    return;
  }
  const sgTask = event.target.closest("[data-sg-task]");
  if (sgTask) {
    try {
      await api("/api/suggest-task", { uid: sgTask.closest("[data-sg]").dataset.sg });
      toast("Добавлено в «Актуальное»");
      sgTask.closest("[data-sg]").querySelector("[data-sg-rate='1']")?.classList.add("on");
    } catch (error) { toast(error.message); }
    return;
  }
  const sgRun = event.target.closest("[data-sg-run]");
  if (sgRun) {
    try {
      const job = await api("/api/suggest-run", { engine: sgRun.dataset.sgRun });
      document.querySelectorAll("[data-sg-run]").forEach((b) => { b.disabled = true; });
      document.getElementById("sg-job").innerHTML = suggestJobLine(job);
      waitForSuggest();
    } catch (error) { toast(error.message); }
    return;
  }
  const sgKind = event.target.closest("[data-sg-kind]");
  if (sgKind && suggestData) {
    suggestKind = sgKind.dataset.sgKind;
    renderSuggest(suggestData);
    return;
  }
  const pickOpen = event.target.closest("[data-pick-open]");
  if (pickOpen) {
    const full = pickOpen.closest(".pick").querySelector(".pick-full");
    full.hidden = !full.hidden;
    pickOpen.setAttribute("aria-expanded", String(!full.hidden));
    return;
  }
  const pickRate = event.target.closest("[data-pick-rate]");
  if (pickRate) {
    const card = pickRate.closest(".pick");
    const value = Number(pickRate.dataset.pickRate);
    const liked = value > 0 && !pickRate.classList.contains("on");
    try {
      await api("/api/pick", { uid: card.dataset.pick, feedback: value < 0 ? -1 : liked ? 1 : 0 });
      if (value < 0) { toast("Учту: похожее будет реже"); vanish(card); }
      else { pickRate.classList.toggle("on", liked); if (liked) toast("Учту: похожее будет чаще"); }
    } catch (error) { toast(error.message); }
    return;
  }
  const unwatch = event.target.closest("[data-unwatch]");
  if (unwatch) {
    readSimpleFields();
    draft.interests.watch.splice(Number(unwatch.dataset.unwatch), 1);
    renderSettingsAgain();
    return;
  }
  const reopen = event.target.closest("[data-reopen]");
  if (reopen) return moveTask(doneIndex[reopen.dataset.reopen], " ");
  const modeButton = event.target.closest("[data-mode]");
  if (modeButton) {
    document.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b === modeButton)));
    try { await api("/api/mode", { id: modeButton.dataset.mode }); route(); waitForRescore(); } catch (error) { toast(error.message); route(); }
    return;
  }
  const summaryButton = event.target.closest("[data-summary]");
  if (summaryButton) return openSummary(summaryButton);
  const editButton = event.target.closest("[data-edit]");
  if (editButton) return startEdit(editButton);
  const removeButton = event.target.closest("[data-delete], [data-reject]");
  if (removeButton) {
    const rejecting = "reject" in removeButton.dataset;
    const task = taskIndex[removeButton.dataset.reject || removeButton.dataset.delete];
    try {
      await taskRequest({ action: rejecting ? "reject" : "delete", line: task.meta.line, raw: task.meta.raw });
      toast(rejecting ? "Убрано, больше не добавлю" : "Удалено из «Актуального»");
      const row = removeButton.closest(".do-row");
      if (row) await vanish(row);
      route();
    } catch (error) { toast(error.message); route(); }
    return;
  }
  const anchor = event.target.closest('a[href$="-anchor"]');
  if (anchor) {
    event.preventDefault();
    const target = { "#tasks-anchor": "tasks", "#chats-anchor": "chats", "#news-anchor": "news-panel" }[anchor.getAttribute("href")];
    document.getElementById(target)?.scrollIntoView();
    return;
  }
  const chip = event.target.closest("[data-topic]");
  if (chip && today && chip.classList.contains("chip")) {
    activeTopic = chip.dataset.topic;
    repaintNews();
    return;
  }
  const expand = event.target.closest("[data-expand]");
  if (expand) {
    const more = expand.closest(".news").querySelector(".news-more");
    more.hidden = !more.hidden;
    expand.setAttribute("aria-expanded", String(!more.hidden));
    return;
  }
  const button = event.target.closest("[data-act]");
  if (!button) return;
  const card = button.closest("[data-id]");
  const act = button.dataset.act;
  const toggled = button.classList.contains("on");
  const body = act === "seen" ? { state: "seen" }
    : act === "up" ? { feedback: toggled ? 0 : 1 }
    : act === "down" ? { feedback: toggled ? 0 : -1 }
    : { queued: toggled ? 0 : 1 };
  try {
    await api(`/api/item/${card.dataset.id}`, body);
  } catch (error) { return toast(error.message); }
  if (act === "seen" || (act === "down" && !toggled)) {
    vanish(card);
    if (act === "down") toast("Учту: похожее будет ниже");
    return;
  }
  if (act === "queue") {
    button.querySelector("span").textContent = toggled ? "Разобрать" : "В разборе";
    if (!toggled) toast("Попадёт в следующую сводку");
  }
  button.classList.toggle("on");
  if (act === "up") card.querySelector('[data-act="down"]').classList.remove("on");
});

const tooltip = document.getElementById("tooltip");
view.addEventListener("mousemove", (event) => {
  const target = event.target.closest("[data-tip]");
  if (!target) { tooltip.style.display = "none"; return; }
  tooltip.textContent = target.dataset.tip;
  tooltip.style.display = "block";
  tooltip.style.left = `${Math.min(event.clientX + 12, innerWidth - 110)}px`;
  tooltip.style.top = `${event.clientY - 30}px`;
});

function storedTheme() {
  try { return localStorage.getItem("pulse-theme"); } catch { return null; }
}

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
}

document.getElementById("theme").onclick = () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  document.documentElement.classList.add("theme-fade");
  setTimeout(() => document.documentElement.classList.remove("theme-fade"), 450);
  applyTheme(next);
  try { localStorage.setItem("pulse-theme", next); } catch { /* тема просто не запомнится */ }
};

async function route(animate = false) {
  const token = ++routeToken;  // ответ для уже покинутого экрана не должен его перерисовать
  const [name, param] = (location.hash.slice(1) || "today").split("/");
  document.querySelectorAll(".rail a").forEach((a) => {
    if (a.dataset.view === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  try {
    if (name === "feed") await renderFeed(param ? decodeURIComponent(param) : "");
    else if (name === "tasks") await renderTasks();
    else if (name === "analytics") await renderAnalytics();
    else if (name === "suggest") await renderSuggestPage();
    else if (name === "settings") await renderSettings();
    else {
      const data = await api("/api/today");
      if (token === routeToken) renderToday(data);
    }
  } catch (error) {
    view.innerHTML = `<p class="empty">Не удалось загрузить: ${esc(error.message)}</p>`;
  }
  if (animate && token === routeToken) enter();
  view.focus({ preventScroll: true });
}

applyTheme(storedTheme());
setupAsk();
watchUpdates();
addEventListener("hashchange", () => route(true));
route(true);
