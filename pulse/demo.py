"""Демо-копия для проверки интерфейса: настоящие новости и дела, вымышленные чаты.

Скриншоты интерфейса уходят в облако, поэтому в этой копии нет ни текста настоящих
чатов, ни их названий, ни ключей Telegram. Запуск из корня проекта:
    python -m pulse demo
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .agenda import CHAT_SECTION

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
PORT = "8766"
FAKE_MODEL_PORT = 8798  # не порт настоящего Пульса: иначе он отправит туда настоящие чаты

CHATS = [
    ("Поток: психология, 3 курс", "группа", -1001, "Анна", "Семинар в среду перенесли, дедлайн по эссе 6 ноября", False, 0, 34),
    ("Команда лектория", "группа", -1002, "Игорь", "Можешь сегодня посмотреть макет афиши?", True, 2, 12),
    ("Вера Смирнова", "личный", 1003, "Вера", "Привет! Успеваешь до пятницы с разделом для заявки?", False, 5, 3),
    ("Хакатон: команда", "группа", -1004, "Олег", "Кто берёт презентацию? Экзамен у всех после 12 ноября", False, 7, 21),
    ("Мини-группа по статистике", "группа", -1005, "Дима", "Выложил разбор задач в папку", False, 8, 9),
    ("Общежитие, этаж 4", "группа", -1006, "Паша", "Кто оставил кастрюлю на кухне", False, 3, 58),
]
DIGESTS = {
    "Поток: психология, 3 курс": ("Семинар переносят на среду, спорят о формате эссе.", "Сдать эссе до 6 ноября"),
    "Команда лектория": ("Обсуждают афишу и дату следующей лекции.", "Посмотреть макет афиши"),
    "Хакатон: команда": ("Распределяют роли перед защитой кейса.", "Решить, кто делает презентацию"),
    "Общежитие, этаж 4": ("Бытовые вопросы, вам ничего не нужно делать.", ""),
}
DIALOGS = [{"id": c[2], "name": c[0], "kind": c[1], "unread": c[7], "last": None, "archived": False} for c in CHATS]


def without_chat_section(text):
    """Строки из настоящих чатов не должны попасть в демо и на скриншоты."""
    kept, skipping = [], False
    for line in text.split("\n"):
        if line.startswith("#"):
            skipping = line == f"### {CHAT_SECTION}"
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def build():
    data = DEMO / "data"
    if DEMO.exists():
        shutil.rmtree(DEMO)
    data.mkdir(parents=True)

    source = sqlite3.connect(ROOT / "data" / "pulse.db")
    target = sqlite3.connect(data / "pulse.db")
    source.backup(target)
    source.close()
    target.execute("DELETE FROM items WHERE kind='message' OR source_type='tg_private'")
    target.execute("DELETE FROM sources WHERE type='tg_private'")
    target.execute("DELETE FROM kv WHERE k LIKE 'tg_%' OR k='chat_overview'")
    target.execute("DELETE FROM items WHERE kind='task' AND json_extract(meta, '$.heading')=?", (CHAT_SECTION,))
    target.execute("CREATE TABLE IF NOT EXISTS chat_digests (chat TEXT PRIMARY KEY, summary TEXT NOT NULL, action TEXT NOT NULL DEFAULT '', last_uid TEXT NOT NULL, updated TEXT NOT NULL)")
    target.execute("DELETE FROM chat_digests")
    target.execute("CREATE TABLE IF NOT EXISTS chat_deadlines (key TEXT PRIMARY KEY, chat TEXT NOT NULL, due TEXT NOT NULL, status TEXT NOT NULL, written TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)")
    target.execute("DELETE FROM chat_deadlines")

    now = datetime.now(timezone.utc)
    for name, kind, chat_id, author, text, mentioned, hours, count in CHATS:
        for number in range(count):
            first = number == 0
            published = now - timedelta(hours=hours, minutes=number * 11)
            body = text if first else f"Вымышленное сообщение {number}"
            meta = {"chat_id": chat_id, "chat_kind": kind, "mentioned": mentioned and first}
            target.execute(
                """INSERT INTO items (uid, kind, source_type, source_name, title, body, url, author, published, collected, meta)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (f"tg-{chat_id}-{1000 - number}", "message", "tg_private", name, body, body, "", author,
                 published.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), json.dumps(meta, ensure_ascii=False)))
        target.execute("INSERT INTO sources (key, name, type, last_ok, last_count) VALUES (?,?,?,?,?)",
                       (f"tg:{chat_id}", name, "tg_private", now.isoformat(timespec="seconds"), count))
    overview = {"headline": "Вера ждёт раздел для заявки, а эссе надо сдать до 6 ноября", "chats": 4, "updated": now.isoformat(),
                "points": ["Лекторию нужен ваш ответ по макету афиши", "В команде хакатона не решили, кто делает презентацию"]}
    target.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('chat_overview', ?)", (json.dumps(overview, ensure_ascii=False),))
    for chat, (summary, action) in DIGESTS.items():
        target.execute("INSERT INTO chat_digests VALUES (?,?,?,?,?)", (chat, summary, action, "demo", now.isoformat()))
    target.commit()
    target.close()

    config = json.loads((ROOT / "config.json").read_text("utf-8"))
    # Кнопки демо пишут в копию «Актуального», настоящее хранилище они не трогают
    relative = config["obsidian"]["agenda_notes"][0]
    note = DEMO / "vault" / relative
    note.parent.mkdir(parents=True)
    note.write_text(without_chat_section((Path(config["vault"]) / relative).read_text("utf-8")), "utf-8")
    config["vault"] = str(DEMO / "vault")
    config["tg_private"] = [{"id": c[2], "name": c[0], "kind": c[1], "weight": 1} for c in CHATS]
    config["tg_public"] = []
    config["collect_interval_min"] = 10_000_000
    config["local_ai"] = {**config.get("local_ai", {}), "url": f"http://127.0.0.1:{FAKE_MODEL_PORT}/v1", "external": True, "max_chats_per_run": 15}
    config["remote"] = {"enabled": False, "key": ""}
    (DEMO / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), "utf-8")
    (data / "tg_dialogs.json").write_text(json.dumps(DIALOGS, ensure_ascii=False), "utf-8")
    (data / "telegram.json").write_text('{"api_id": 1, "api_hash": "demo"}', "utf-8")
    (data / "telegram.session").write_text("", "utf-8")
    if (ROOT / "data" / "brief.json").exists():
        shutil.copy(ROOT / "data" / "brief.json", data / "brief.json")


def run():
    build()
    env = {**os.environ, "PULSE_HOME": str(DEMO), "PULSE_PORT": PORT, "PULSE_NO_MODEL": "1"}
    # Оценки считаем без сети: только свежие вымышленные сообщения и уже собранные новости
    code = ("from pulse import analysis, db, pipeline\n"
            "from pulse.config import load_config\n"
            "cfg = load_config()\n"
            "with db.connect() as c:\n"
            "    pipeline.refresh_tasks(c, cfg)\n"
            "    analysis.run(c, cfg)\n"
            "    print(pipeline.run_local_model(c, cfg))\n"
            "    state = pipeline.chat_state(c, cfg)\n"
            "    overview = db.kv_get(c, 'chat_overview')\n"
            "    db.kv_set(c, 'chat_overview', {**overview, 'key': analysis.live_key(state['live'])})\n")
    subprocess.run([sys.executable, "-X", "utf8", "-c", code], env=env, cwd=ROOT, check=True)
    print(f"Демо: http://127.0.0.1:{PORT}")
    subprocess.run([sys.executable, "-X", "utf8", "-m", "pulse", "serve"], env=env, cwd=ROOT)
