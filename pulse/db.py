"""SQLite-хранилище: элементы всех источников, здоровье источников, индекс заметок."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import DATA, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  uid TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,            -- news | post | message | task | review
  source_type TEXT NOT NULL,     -- rss | tg_public | tg_private | obsidian
  source_name TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '',
  published TEXT NOT NULL,       -- UTC ISO
  collected TEXT NOT NULL,
  due TEXT,                      -- YYYY-MM-DD
  meta TEXT NOT NULL DEFAULT '{}',
  score REAL NOT NULL DEFAULT 0,
  tier TEXT NOT NULL DEFAULT 'noise',   -- high | mid | low | noise | stale
  topics TEXT NOT NULL DEFAULT '[]',
  reasons TEXT NOT NULL DEFAULT '[]',
  links TEXT NOT NULL DEFAULT '[]',
  cluster INTEGER,
  cluster_size INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL DEFAULT 'new',    -- new | seen | gone
  feedback INTEGER NOT NULL DEFAULT 0,  -- +1 важно, -1 не важно
  queued INTEGER NOT NULL DEFAULT 0     -- попросили разобрать глубже
);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind, state);

CREATE TABLE IF NOT EXISTS sources (
  key TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  last_ok TEXT,
  last_error TEXT,
  last_error_at TEXT,
  last_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notes (
  path TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  zone TEXT NOT NULL,
  mtime TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS chat_digests (
  chat TEXT PRIMARY KEY,
  summary TEXT NOT NULL,
  action TEXT NOT NULL DEFAULT '',
  last_uid TEXT NOT NULL,
  updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_deadlines (
  key TEXT PRIMARY KEY,
  chat TEXT NOT NULL,
  due TEXT NOT NULL,
  status TEXT NOT NULL,          -- added | skipped | rejected
  written TEXT NOT NULL DEFAULT '',
  created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS article_summaries (
  item_id INTEGER PRIMARY KEY,
  payload TEXT NOT NULL,
  created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reply_checks (
  uid TEXT PRIMARY KEY,
  needs INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS picks (
  uid TEXT PRIMARY KEY,
  interesting INTEGER NOT NULL,
  gist TEXT NOT NULL,
  why TEXT NOT NULL DEFAULT '',
  feedback INTEGER NOT NULL DEFAULT 0,  -- +1 интересно, -1 не то
  created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suggestions (
  uid TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                   -- gigs, jobs, events, reads
  fit INTEGER NOT NULL,                 -- 1 подходит, 0 проверка сказала «нет»
  why TEXT NOT NULL DEFAULT '',
  feedback INTEGER NOT NULL DEFAULT 0,  -- +1 подходит, -1 не то
  by TEXT NOT NULL DEFAULT 'model',     -- кто проверил: model или claude
  created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    DATA.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_item(conn, it):
    """Новый элемент добавляет, у известного обновляет содержимое, не трогая оценки пользователя."""
    conn.execute(
        """INSERT INTO items (uid, kind, source_type, source_name, title, body, url, author,
                              published, collected, due, meta)
           VALUES (:uid, :kind, :source_type, :source_name, :title, :body, :url, :author,
                   :published, :collected, :due, :meta)
           ON CONFLICT(uid) DO UPDATE SET
             title=excluded.title, body=excluded.body, url=excluded.url, due=excluded.due,
             meta=excluded.meta, published=excluded.published,
             state=CASE WHEN items.state='gone' THEN 'new' ELSE items.state END""",
        {
            "due": None, "url": "", "author": "", "body": "", "collected": now_iso(),
            **it, "meta": json.dumps(it.get("meta", {}), ensure_ascii=False),
        },
    )


def record_source(conn, key, name, type_, count=0, error=None):
    now = now_iso()
    conn.execute(
        """INSERT INTO sources (key, name, type, last_ok, last_error, last_error_at, last_count)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(key) DO UPDATE SET
             name=excluded.name,
             last_ok=COALESCE(excluded.last_ok, sources.last_ok),
             last_error=excluded.last_error,
             last_error_at=COALESCE(excluded.last_error_at, sources.last_error_at),
             last_count=excluded.last_count""",
        (key, name, type_, None if error else now, error, now if error else None, count),
    )


def kv_get(conn, key, default=None):
    row = conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    return json.loads(row["v"]) if row else default


def kv_set(conn, key, value):
    conn.execute(
        "INSERT INTO kv (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def answered_ids(conn):
    """Последний ответ в каждом чате: ваш или отметка «ответ не нужен»."""
    answered = dict(kv_get(conn, "tg_my_last", {}))
    for chat_id, message_id in kv_get(conn, "reply_dismissed", {}).items():
        answered[chat_id] = max(answered.get(chat_id, 0), message_id)
    return answered


def row_to_item(row):
    item = dict(row)
    for field in ("meta", "topics", "reasons", "links"):
        item[field] = json.loads(item[field] or ("{}" if field == "meta" else "[]"))
    return item
