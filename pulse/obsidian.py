"""Сбор из Obsidian. Здесь хранилище только читается, запись живёт в agenda.py."""
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import db
from .text import extract_due, first_line_title, strip_markdown

TASK_RE = re.compile(r"^(\s*)[-*] \[( |x|X|/|w)\]\s*(.*)$")
OPEN_MARKS = " /w"
BULLET_RE = re.compile(r"^[-*] (?!\[)(.+)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
SKIP_DIRS = {".claude", ".claudian", ".obsidian", ".smart-env", ".git", ".trash", "Картинки"}
RELATIVE_STALE_DAYS = 3
ABSOLUTE_STALE_DAYS = 14


def uid_for(*parts):
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]


def rel(path, vault):
    return path.relative_to(vault).as_posix()


def is_included(relpath, opts):
    if any(relpath == e or relpath.startswith(e + "/") for e in opts["exclude"]):
        return False
    return any(relpath == i or relpath.startswith(i + "/") for i in opts["include"])


def iter_notes(vault):
    for path in vault.rglob("*.md"):
        if not SKIP_DIRS.intersection(path.relative_to(vault).parts):
            yield path


def obsidian_url(vault, relpath):
    from urllib.parse import quote
    return f"obsidian://open?vault={quote(vault.name)}&file={quote(relpath[:-3] if relpath.endswith('.md') else relpath)}"


def read_tags(text):
    if not text.startswith("---"):
        return []
    head = text[:1500].split("\n---", 1)[0]
    match = re.search(r"^tags:\s*\[(.*?)\]\s*$", head, re.M)
    if match:
        return [t.strip(" '\"") for t in match[1].split(",") if t.strip()]
    return re.findall(r"^\s+-\s+(.+?)\s*$", head.split("tags:", 1)[1], re.M) if "tags:" in head else []


def parse_tasks(text, is_agenda=False):
    """Открытые листовые задачи с цепочкой родителей и ближайшим заголовком."""
    lines = text.splitlines()
    found = []
    stack = []  # (отступ, текст)
    heading = ""
    seen_heading = False
    for index, line in enumerate(lines):
        head = HEADING_RE.match(line)
        if head:
            heading = strip_markdown(head[2])
            seen_heading = True
            stack.clear()
            continue
        task = TASK_RE.match(line)
        if task:
            indent = len(task[1].replace("\t", "    "))
            content = strip_markdown(task[3])
            while stack and stack[-1][0] >= indent:
                stack.pop()
            nxt = next((l for l in lines[index + 1:] if l.strip()), "")
            nxt_task = TASK_RE.match(nxt)
            is_parent = bool(nxt_task and len(nxt_task[1].replace("\t", "    ")) > indent)
            if task[2] in OPEN_MARKS and content and not is_parent:
                found.append({"text": content, "parents": [s[1] for s in stack], "heading": heading,
                              "line": index, "raw": line, "markdown": task[3].strip(), "status": task[2]})
            stack.append((indent, content.rstrip(":")))
            continue
        if is_agenda and not seen_heading:
            bullet = BULLET_RE.match(line)
            if bullet:
                content = strip_markdown(bullet[1])
                if content:
                    found.append({"text": content, "parents": [], "heading": "", "status": " ",
                                  "line": index, "raw": line, "markdown": bullet[1].strip()})
    return found


def known_anchors(conn):
    """Дата первого появления задачи. Запись в файл меняет его время, а смысл «до 29» не меняет."""
    anchors = {}
    for row in conn.execute("SELECT uid, meta FROM items WHERE kind='task'").fetchall():
        anchor = json.loads(row["meta"]).get("anchor")
        if anchor:
            anchors[row["uid"]] = date.fromisoformat(anchor)
    return anchors


def collect_tasks(conn, vault, opts, today):
    seen = set()
    anchors = known_anchors(conn)
    for path in iter_notes(vault):
        relpath = rel(path, vault)
        if not is_included(relpath, opts) and relpath not in opts["agenda_notes"]:
            continue
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError:
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        file_date = mtime.astimezone().date()
        is_agenda = relpath in opts["agenda_notes"]
        for task in parse_tasks(text, is_agenda):
            uid = uid_for("task", relpath, task["text"])
            anchor = anchors.get(uid, file_date)
            context = " ".join(task["parents"] + [task["heading"]])
            due, relative = extract_due(task["text"], anchor)
            if due is None:
                due, relative = extract_due(context, anchor)
            age = (today - anchor).days
            stale_reason = None
            if due and relative and (today - due).days > RELATIVE_STALE_DAYS:
                stale_reason = f"«{_relative_word(task['text'] + ' ' + context)}» написано {anchor.strftime('%d.%m')}"
                due = None
            elif due and (today - due).days > ABSOLUTE_STALE_DAYS:
                stale_reason = f"срок {due.strftime('%d.%m')} давно прошёл"
                due = None
            elif not due and age > opts["stale_days"]:
                stale_reason = f"без движения {age} дн."
            seen.add(uid)
            db.upsert_item(conn, {
                "uid": uid, "kind": "task", "source_type": "obsidian", "source_name": path.stem,
                "title": first_line_title(task["text"], 160), "body": task["text"],
                "url": obsidian_url(vault, relpath), "published": mtime.isoformat(timespec="seconds"),
                "due": due.isoformat() if due else None,
                "meta": {"path": relpath, "context": " › ".join(task["parents"]), "heading": task["heading"],
                         "agenda": is_agenda, "stale": stale_reason, "age_days": age, "anchor": anchor.isoformat(),
                         "line": task["line"], "raw": task["raw"], "markdown": task["markdown"], "status": task["status"]},
            })
    _retire(conn, "task", seen)
    return len(seen)


def _relative_word(text):
    match = re.search(r"сегодня|завтра|послезавтра|понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье", text.lower())
    return match[0] if match else "сегодня"


def _retire(conn, kind, seen):
    """Задача исчезла из заметки или отмечена выполненной: убираем её и из Пульса."""
    for row in conn.execute("SELECT uid FROM items WHERE kind=? AND state!='gone'", (kind,)).fetchall():
        if row["uid"] not in seen:
            conn.execute("UPDATE items SET state='gone' WHERE uid=?", (row["uid"],))


def collect_reviews(conn, vault, opts):
    seen = set()
    registry = vault / opts["registry"]
    if registry.exists():
        for line in registry.read_text("utf-8", errors="replace").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 4 and cells[0].startswith("[[") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[3]):
                name = strip_markdown(cells[0])
                uid = uid_for("review", name)
                seen.add(uid)
                db.upsert_item(conn, {
                    "uid": uid, "kind": "review", "source_type": "obsidian", "source_name": "Реестр повторений",
                    "title": name, "url": obsidian_url(vault, opts["registry"]),
                    "published": cells[3] + "T00:00:00+00:00", "due": cells[3], "meta": {"track": "registry"},
                })
    journal = vault / opts["journal"]
    if journal.exists():
        for line in journal.read_text("utf-8", errors="replace").splitlines():
            if not re.match(r"^\| \d{4}-\d{2}-\d{2} \|", line):
                continue
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
            due = next((c for c in reversed(cells) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c)), None)
            if len(cells) < 6 or due is None:
                continue
            summary = strip_markdown(cells[3])
            uid = uid_for("review", "journal", cells[1], summary[:80])
            seen.add(uid)
            db.upsert_item(conn, {
                "uid": uid, "kind": "review", "source_type": "obsidian", "source_name": "Журнал углублений",
                "title": first_line_title(summary.split(";")[0], 120), "body": summary,
                "url": obsidian_url(vault, opts["journal"]),
                "published": due + "T00:00:00+00:00", "due": due, "meta": {"track": "journal", "topic": cells[1]},
            })
    _retire(conn, "review", seen)
    return len(seen)


def index_notes(conn, vault):
    conn.execute("DELETE FROM notes")
    count = 0
    for path in iter_notes(vault):
        relpath = rel(path, vault)
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(1500)
            mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        except OSError:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO notes (path, title, zone, mtime, tags) VALUES (?,?,?,?,?)",
            (relpath, path.stem, relpath.split("/")[0], mtime, json.dumps(read_tags(head), ensure_ascii=False)),
        )
        count += 1
    return count


def collect(conn, cfg):
    vault = Path(cfg["vault"])
    key = "obsidian"
    if not vault.is_dir():
        db.record_source(conn, key, "Obsidian", "obsidian", error=f"Нет папки хранилища: {vault}")
        return 0
    opts = cfg["obsidian"]
    today = date.today()
    tasks = collect_tasks(conn, vault, opts, today)
    reviews = collect_reviews(conn, vault, opts)
    notes = index_notes(conn, vault)
    db.record_source(conn, key, "Obsidian", "obsidian", count=tasks + reviews)
    db.kv_set(conn, "obsidian_stats", {"tasks": tasks, "reviews": reviews, "notes": notes})
    return tasks + reviews
