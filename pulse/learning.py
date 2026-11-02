"""Чему Пульс научился на ваших отметках. Всё видно в настройках и сбрасывается одной кнопкой."""
from . import analysis, db
from .config import DEFAULT_LEVEL, chat_levels, load_config, save_config

IGNORES_TO_QUIET = 3
QUIET_LEVEL = "тише"


def note_ignored(conn, cfg, name):
    """«Не важно» у чата: после нескольких отметок он становится тише. Возвращает, стал ли."""
    ignored = db.kv_get(conn, "learned_chats", {})
    ignored[name] = ignored.get(name, 0) + 1
    db.kv_set(conn, "learned_chats", ignored)
    if ignored[name] < IGNORES_TO_QUIET or chat_levels(cfg).get(name, DEFAULT_LEVEL) != DEFAULT_LEVEL:
        return False
    set_level(cfg, name, QUIET_LEVEL)
    return True


def set_level(cfg, name, level):
    chat = next((c for c in cfg["tg_private"] if c["name"] == name), None)
    if chat is None:
        return
    chat["level"] = level
    save_config(cfg)


def ranked(scores):
    rows = sorted(scores.items(), key=lambda pair: pair[1])
    return [{"name": name, "score": score} for name, score in rows if score]


def view(conn, cfg):
    _, sources, topics = analysis.feedback_model(conn)
    names = {t["id"]: t["name"] for t in cfg["topics"]}
    ignored = db.kv_get(conn, "learned_chats", {})
    levels = chat_levels(cfg)
    return {
        "marks": conn.execute("SELECT COUNT(*) c FROM items WHERE feedback!=0").fetchone()["c"],
        "sources": ranked(sources),
        "topics": ranked({names[t]: v for t, v in topics.items() if t in names}),
        "chats": [{"name": name, "count": count, "quiet": levels.get(name) == QUIET_LEVEL} for name, count in sorted(ignored.items())],
    }


def forget(conn):
    """Сбрасывает отметки и возвращает обычную важность чатам, которые Пульс сделал тише."""
    cfg = load_config()
    for name, count in db.kv_get(conn, "learned_chats", {}).items():
        if count >= IGNORES_TO_QUIET and chat_levels(cfg).get(name) == QUIET_LEVEL:
            set_level(cfg, name, DEFAULT_LEVEL)
    conn.execute("UPDATE items SET feedback=0 WHERE feedback!=0")
    db.kv_set(conn, "learned_chats", {})
