"""Правка «Актуального» из приложения. Единственное место, где Пульс пишет в хранилище.

Файл могут менять и в Obsidian, поэтому перед записью строка сверяется с текущим
текстом, а перед каждым изменением делается резервная копия.
"""
import os
import re
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import DATA
from .obsidian import HEADING_RE, TASK_RE
from .text import strip_markdown

CHAT_SECTION = "Сроки из чатов"
STATUSES = " /wx"  # сделать, в работе, жду, готово
DONE_SHOWN = 12
BACKUPS_KEPT = 40
MAX_TEXT = 300
BULLET_LINE_RE = re.compile(r"^(\s*)[-*] (?!\[)(.*)$")
DUE_SUFFIX_RE = re.compile(r"\s*\(до \d{1,2}\.\d{2}(?:\.\d{4})?\)\s*$")
ADD_ANCHOR_RE = re.compile(r"^Сделать сейчас")
WEEKDAYS = {"пн": 0, "понедельник": 0, "вт": 1, "вторник": 1, "ср": 2, "среда": 2, "среду": 2, "чт": 3, "четверг": 3,
            "пт": 4, "пятница": 4, "пятницу": 4, "сб": 5, "суббота": 5, "субботу": 5, "вс": 6, "воскресенье": 6}
MONTH_STEMS = ("январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр")
_LEAD = r"(?:^|\s+)(?:(?:до|к|в|во|на|срок)\s+)?"
QUICK_DUE = [
    ("rel", re.compile(_LEAD + r"(сегодня|завтра|послезавтра)\s*$", re.I)),
    ("days", re.compile(_LEAD + r"через\s+(\d{1,3}|неделю|месяц)\s*(дн[яеий]*|день|недел[юиь]*)?\s*$", re.I)),
    ("weekday", re.compile(_LEAD + r"(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\.?\s*$", re.I)),
    ("dm", re.compile(_LEAD + r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s*$", re.I)),
    ("month", re.compile(_LEAD + r"(\d{1,2})\s+(" + "|".join(MONTH_STEMS) + r")[а-я]*\s*$", re.I)),
]


class Conflict(Exception):
    """Строка изменилась в Obsidian после того, как её показали на экране."""


def agenda_path(cfg):
    return Path(cfg["vault"]) / cfg["obsidian"]["agenda_notes"][0]


def backups_dir():
    return DATA / "obsidian_backups"


def read_lines(path):
    text = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.split(newline), newline


def write_lines(path, lines, newline):
    backup(path)
    tmp = path.with_name(f".{path.name}.pulse-tmp")
    tmp.write_bytes(newline.join(lines).encode("utf-8"))
    for attempt in range(5):  # Obsidian может ненадолго держать файл открытым
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.2)


def backup(path):
    folder = backups_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    shutil.copy2(path, folder / f"{stamp} {path.name}")
    for old in sorted(folder.iterdir())[:-BACKUPS_KEPT]:
        old.unlink()


def clean_text(text):
    """Одна строка без управляющих символов: разметку файла нельзя сломать через поле ввода."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(#+\s+|[-*+]\s+(\[.?\]\s+)?)+", "", text)[:MAX_TEXT].strip()
    if not text:
        raise ValueError("Задача не может быть пустой")
    return text


def with_due(text, due):
    """Срок пишется в формате из «Дел»: в скобках в конце строки."""
    text = DUE_SUFFIX_RE.sub("", text)
    if not due:
        return text
    day = date.fromisoformat(due)
    label = day.strftime("%d.%m") if day.year == date.today().year else day.strftime("%d.%m.%Y")
    return f"{text} (до {label})"


def locate(lines, line, raw):
    if 0 <= line < len(lines) and lines[line] == raw:
        return line
    matches = [i for i, value in enumerate(lines) if value == raw]
    if len(matches) == 1:
        return matches[0]  # строка сдвинулась, но текст тот же
    raise Conflict("Задачу изменили в Obsidian. Обновите страницу и попробуйте снова.")


def split_line(value):
    """Отступ, отметка и текст строки-задачи."""
    task = TASK_RE.match(value)
    if task:
        return task[1], task[2].lower(), task[3].strip()
    bullet = BULLET_LINE_RE.match(value)
    if bullet:
        return bullet[1], " ", bullet[2].strip()
    raise Conflict("Эта строка больше не похожа на задачу.")


def task_line(indent, status, text):
    return f"{indent}- [{status}] {text}"


def set_status(cfg, line, raw, status):
    """Колонка канбана хранится в самой строке, поэтому Obsidian и Пульс не расходятся."""
    if status not in STATUSES:
        raise ValueError("Нет такой колонки")
    path = agenda_path(cfg)
    lines, newline = read_lines(path)
    index = locate(lines, line, raw)
    indent, _, text = split_line(lines[index])
    lines[index] = task_line(indent, status, text)
    write_lines(path, lines, newline)
    return {"line": index, "raw": lines[index]}


def set_done(cfg, line, raw, done):
    return set_status(cfg, line, raw, "x" if done else " ")


def done_tasks(cfg, limit=DONE_SHOWN):
    """Последние выполненные для колонки «Готово»: в файле они лежат отмеченными."""
    path = agenda_path(cfg)
    if not path.exists():
        return []
    lines, _ = read_lines(path)
    found = []
    for index, value in enumerate(lines):
        task = TASK_RE.match(value)
        if task and task[2].lower() == "x" and task[3].strip():
            found.append({"line": index, "raw": value, "title": strip_markdown(task[3])})
    return found[-limit:][::-1]


def edit(cfg, line, raw, text, due):
    path = agenda_path(cfg)
    lines, newline = read_lines(path)
    index = locate(lines, line, raw)
    indent, status, _ = split_line(lines[index])
    lines[index] = task_line(indent, status, with_due(clean_text(text), due))
    write_lines(path, lines, newline)
    return {"line": index, "raw": lines[index]}


def delete(cfg, line, raw):
    path = agenda_path(cfg)
    lines, newline = read_lines(path)
    index = locate(lines, line, raw)
    split_line(lines[index])
    del lines[index]
    write_lines(path, lines, newline)


def default_slot(lines):
    """Новая задача встаёт в начало списка под «Сделать сейчас»."""
    for index, value in enumerate(lines):
        if ADD_ANCHOR_RE.match(value):
            slot = index + 1
            while slot < len(lines) and not lines[slot].strip():
                slot += 1
            return slot
    return next((i for i, value in enumerate(lines) if HEADING_RE.match(value)), len(lines))


def chat_slot(lines):
    """Конец раздела «Сроки из чатов». Раздел создаётся перед первым заголовком."""
    title = f"### {CHAT_SECTION}"
    if title not in lines:
        first = next((i for i, value in enumerate(lines) if HEADING_RE.match(value)), len(lines))
        lines[first:first] = [title, "", ""]
        return first + 1 + 1
    start = lines.index(title) + 1
    end = next((i for i in range(start, len(lines)) if HEADING_RE.match(lines[i])), len(lines))
    last = max((i for i in range(start, end) if lines[i].strip()), default=start)
    return last + 1 if lines[last].strip() else start + 1


def split_due(text, today=None):
    """Срок в конце новой задачи: «Сдать эссе в пятницу» даёт текст «Сдать эссе» и дату пятницы."""
    today = today or date.today()
    text = (text or "").strip()
    for kind, pattern in QUICK_DUE:
        match = pattern.search(text)
        if not match or not text[:match.start()].strip():
            continue
        word = match[1].lower()
        day = None
        if kind == "rel":
            day = today + timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[word])
        elif kind == "days":
            unit = (match[2] or "").lower()
            if word == "неделю":
                day = today + timedelta(days=7)
            elif word == "месяц":
                day = today + timedelta(days=30)
            else:
                day = today + timedelta(days=int(word) * (7 if unit.startswith("недел") else 1))
        elif kind == "weekday":
            day = today + timedelta(days=(WEEKDAYS[word] - today.weekday() - 1) % 7 + 1)
        else:
            if kind == "dm":
                d, m, y = int(match[1]), int(match[2]), match[3]
            else:
                d, m, y = int(match[1]), MONTH_STEMS.index(match[2].lower()) + 1, None
            year = (int(y) + (2000 if len(y) == 2 else 0)) if y else today.year
            try:
                day = date(year, m, d)
            except ValueError:
                continue
            if not y and day < today:
                day = date(year + 1, m, d)
        return text[:match.start()].rstrip(" ,.-—"), day.isoformat()
    return text, None


def add(cfg, text, due=None, section=None, status=" "):
    if status not in STATUSES:
        raise ValueError("Нет такой колонки")
    path = agenda_path(cfg)
    lines, newline = read_lines(path)
    slot = chat_slot(lines) if section == CHAT_SECTION else default_slot(lines)
    value = task_line("", status, with_due(clean_text(text), due))
    lines.insert(slot, value)
    write_lines(path, lines, newline)
    return {"line": slot, "raw": value}
