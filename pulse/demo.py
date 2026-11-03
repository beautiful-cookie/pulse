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
FOLDERS = {"Учёба": {-1001, -1005}, "Проекты": {-1002, -1004}}
DIALOGS = [{"id": c[2], "name": c[0], "kind": c[1], "unread": c[7], "last": None, "archived": c[2] == -1006, "contact": False,
            "folders": [name for name, ids in FOLDERS.items() if c[2] in ids]} for c in CHATS]


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
    target.execute("DELETE FROM kv WHERE k LIKE 'tg_%' OR k IN ('chat_overview', 'learned_chats', 'reply_dismissed', 'notified', 'done_log')")
    target.execute("DELETE FROM items WHERE kind='task' AND json_extract(meta, '$.heading')=?", (CHAT_SECTION,))
    target.execute("CREATE TABLE IF NOT EXISTS chat_digests (chat TEXT PRIMARY KEY, summary TEXT NOT NULL, action TEXT NOT NULL DEFAULT '', last_uid TEXT NOT NULL, updated TEXT NOT NULL)")
    target.execute("DELETE FROM chat_digests")
    target.execute("CREATE TABLE IF NOT EXISTS chat_deadlines (key TEXT PRIMARY KEY, chat TEXT NOT NULL, due TEXT NOT NULL, status TEXT NOT NULL, written TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)")
    target.execute("DELETE FROM chat_deadlines")
    target.execute("CREATE TABLE IF NOT EXISTS picks (uid TEXT PRIMARY KEY, interesting INTEGER NOT NULL, gist TEXT NOT NULL, why TEXT NOT NULL DEFAULT '', feedback INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL)")
    target.execute("DELETE FROM picks")

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
    moment = now.astimezone().isoformat(timespec="seconds")  # расписание не должно идти в сеть
    marks = {"full": moment, "chats": moment, "news": moment, "model": moment, "data": moment}
    target.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('run_marks', ?)", (json.dumps(marks),))
    target.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('done_log', ?)", (json.dumps({"Сдать реферат": now.date().isoformat(), "Записаться к врачу": now.date().isoformat()}, ensure_ascii=False),))
    target.execute(
        """INSERT INTO items (uid, kind, source_type, source_name, title, body, url, author, published, collected, meta)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("tg-1003-1001", "outgoing", "tg_private", "Вера Смирнова", "Скину раздел завтра вечером", "Скину раздел завтра вечером", "", "",
         now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), json.dumps({"chat_id": 1003, "chat_kind": "личный"})))
    for chat, (summary, action) in DIGESTS.items():
        target.execute("INSERT INTO chat_digests VALUES (?,?,?,?,?)", (chat, summary, action, "demo", now.isoformat()))
    long_text = ("Собрали материалы к защите кейса: как нейроинтерфейсы меняют реабилитацию после инсульта, "
                 "три исследования за год и разбор, почему метрики точности обманчивы. Нужна помощь с разделом про этику. ") * 2
    target.execute(
        """INSERT INTO items (uid, kind, source_type, source_name, title, body, url, author, published, collected, meta)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("tg--1004-2000", "message", "tg_private", "Хакатон: команда", long_text[:80], long_text, "", "Олег",
         now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), json.dumps({"chat_id": -1004, "chat_kind": "группа"})))
    target.execute("INSERT INTO picks VALUES (?,?,?,?,?,?)", ("tg--1004-2000", 1, "Команда собрала материалы о нейроинтерфейсах в реабилитации и просит помочь с разделом про этику.",
                                                            "нейроинтерфейсы и этика, вы следите за командой", 0, now.isoformat()))
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
    config["chat_interval_min"] = config["news_interval_min"] = config["model_interval_min"] = 10_000_000
    config["notify"] = {"enabled": False}
    config["interests"] = {"about": ["философия сознания", "нейроинтерфейсы"], "watch": ["Хакатон: команда"]}
    config["local_ai"] = {**config.get("local_ai", {}), "url": f"http://127.0.0.1:{FAKE_MODEL_PORT}/v1", "external": True, "max_chats_per_run": 15}
    config["remote"] = {"enabled": False, "key": ""}
    (DEMO / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), "utf-8")
    (data / "tg_dialogs.json").write_text(json.dumps(DIALOGS, ensure_ascii=False), "utf-8")
    (data / "tg_folders.json").write_text(json.dumps(list(FOLDERS), ensure_ascii=False), "utf-8")
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
            "    print(pipeline.run_local_model(c, cfg))\n")
    subprocess.run([sys.executable, "-X", "utf8", "-c", code], env=env, cwd=ROOT, check=True)
    print(f"Демо: http://127.0.0.1:{PORT}")
    subprocess.run([sys.executable, "-X", "utf8", "-m", "pulse", "serve"], env=env, cwd=ROOT)
