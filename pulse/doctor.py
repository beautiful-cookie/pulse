"""Самопроверка: python -m pulse doctor. Только числа, текст чатов и названия не выводятся."""
import asyncio
import time
from datetime import datetime

from . import autostart, claude_ai, db, local_ai, telegram
from .config import load_config


def line(ok, text):
    print(f"{'ок ' if ok else '!! '} {text}")


def age_minutes(iso):
    return round((datetime.now().astimezone() - datetime.fromisoformat(iso)).total_seconds() / 60) if iso else None


async def telegram_probe():
    client = telegram.make_client()
    await client.connect()
    try:
        started = time.monotonic()
        tops = await telegram.read_tops(client, telegram.QUICK_DIALOGS)
        return len(tops), round(time.monotonic() - started, 1)
    finally:
        await client.disconnect()


def check_telegram(conn, cfg):
    if not telegram.is_connected():
        return line(False, "Telegram не подключён: запустите telegram_login.bat")
    try:
        dialogs, seconds = asyncio.run(telegram_probe())
    except Exception as error:
        return line(False, f"Telegram не отвечает: {type(error).__name__}")
    chats = [str(c["id"]) for c in cfg["tg_private"] if c.get("kind") != "канал"]
    cursors = db.kv_get(conn, "tg_cursors", {})
    asked = set(db.kv_get(conn, "tg_replies_asked", []))
    line(True, f"Telegram: {dialogs} диалогов за {seconds} с")
    line(sum(c in cursors for c in chats) == len(chats), f"отметка чтения есть у {sum(c in cursors for c in chats)} чатов из {len(chats)}")
    line(sum(c in asked for c in chats) == len(chats), f"последний ответ проверен в {sum(c in asked for c in chats)} чатах из {len(chats)}")


def check_news(conn):
    rows = conn.execute("SELECT last_error, last_ok FROM sources WHERE type IN ('rss','tg_public')").fetchall()
    failing = sum(1 for r in rows if r["last_error"])
    line(rows and not failing, f"источники новостей: работают {len(rows) - failing} из {len(rows)}")
    newest = conn.execute("SELECT MAX(collected) m FROM items WHERE kind IN ('news','post')").fetchone()["m"]
    minutes = age_minutes(newest)
    line(minutes is not None and minutes < 720, f"последняя новая новость собрана {minutes} мин назад")


def run():
    cfg = load_config()
    with db.connect() as conn:
        marks = db.kv_get(conn, "run_marks", {})
        last = db.kv_get(conn, "last_run") or {}
        for key, label, limit in (("chats", "чаты", cfg["chat_interval_min"] * 3), ("news", "новости", cfg["news_interval_min"] * 3), ("full", "полное обновление", 36 * 60)):
            minutes = age_minutes(marks.get(key))
            line(minutes is not None and minutes <= limit, f"{label}: обновлено {minutes} мин назад")
        print(f"     последний проход: {last.get('mode', '?')}, {last.get('seconds', '?')} с")
        check_telegram(conn, cfg)
        check_news(conn)
    line(bool(local_ai.cached_model(cfg)), f"локальная модель: {local_ai.cached_model(cfg) or 'не запущена'}")
    line(bool(claude_ai.find_cli()), "Claude: программа найдена" if claude_ai.find_cli() else "Claude: программа не найдена")
    print(f"     автозапуск: {'включён' if autostart.is_enabled() else 'выключен'}")
