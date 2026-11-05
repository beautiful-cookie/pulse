"""Локальный веб-сервер. Слушает только этот компьютер и проверяет, откуда пришёл запрос."""
import json
import mimetypes
import os
import re
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import agenda, analysis, assistant, autostart, claude_ai, claude_ask, db, deadlines, digest, interests, learning, local_ai, picks, pipeline, remote, suggest, summaries, telegram
from .config import LEVELS, load_config, save_config

_agenda_lock = threading.Lock()
_remote_server = None
VERSION = 3
TICK_SECONDS = 30
WEB = Path(__file__).parent / "web"
PORT = int(os.environ.get("PULSE_PORT", 8765))
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
SETTINGS_KEYS = {"topics", "feeds", "tg_public", "tg_private", "blacklist", "budget_main",
                 "topic_cap", "chat_interval_min", "news_interval_min", "model_interval_min", "full_collect_hour",
                 "notify", "interests", "me", "privacy", "deadlines", "claude", "modes"}
LOOPBACK_ONLY = {"/api/settings", "/api/remote", "/api/shutdown", "/api/autostart", "/api/learned", "/api/tg-dialogs",
                 "/api/suggest-about", "/api/suggest-kinds"}  # с телефона это менять нельзя
PAGE = 40
MAX_BODY_BYTES = 2_000_000


def today_payload():
    cfg = load_config()
    with db.connect() as conn:
        view = analysis.today_view(conn, cfg)
        view["picks"] = picks.shown(conn)
    brief = pipeline.load_brief()
    if brief and brief.get("date") == date.today().isoformat():
        view["brief"] = {**brief, "author": "claude"}
    else:
        view["brief"] = analysis.auto_brief(view)
        view["brief"]["previous"] = brief.get("date") if brief else None
    view["running"] = pipeline.is_running()
    view["stage"] = pipeline.stage()
    view["telegram"] = {"connected": telegram.is_connected(), "chats": len(cfg["tg_private"]),
                        "channels": len(cfg["tg_public"])}
    view["local_model"] = local_ai.cached_model(cfg)
    view["claude_ready"] = bool(cfg["claude"]["enabled"] and claude_ai.find_cli())
    return view


def feed_payload(query):
    get = lambda k: (query.get(k) or [""])[0]
    where = ["kind IN ('news','post','message')", "state!='gone'"]
    args = []
    if get("topic"):
        where.append("topics LIKE ?")
        args.append(f'%"{get("topic")}"%')
    if get("type"):
        where.append("source_type=?")
        args.append(get("type"))
    if get("source"):
        where.append("source_name=?")
        args.append(get("source"))
    if get("tier") == "important":
        where.append("tier IN ('high','mid')")
    elif get("tier") == "hidden":
        where.append("tier='noise'")
    if get("q"):
        where.append("(title LIKE ? OR body LIKE ?)")
        args += [f"%{get('q')}%"] * 2
    days = int(get("days") or 7)
    since = (datetime.now().astimezone() - timedelta(days=days)).isoformat()
    where.append("published>=?")
    args.append(since)
    offset = int(get("offset") or 0)
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM items WHERE {' AND '.join(where)} ORDER BY published DESC LIMIT ? OFFSET ?",
            (*args, PAGE + 1, offset)).fetchall()
    items = [db.row_to_item(r) for r in rows]
    return {"items": items[:PAGE], "more": len(items) > PAGE}


def settings_payload():
    cfg = load_config()
    with db.connect() as conn:
        learned = learning.view(conn, cfg)
        known = interests.known_names(conn, cfg)
        open_chats = claude_ask.public_chats(conn, cfg)
    return {"config": {k: cfg[k] for k in SETTINGS_KEYS}, "local_model": local_ai.cached_model(cfg), "telegram": {
        "connected": telegram.is_connected(), "dialogs": telegram.load_dialogs(), "folders": telegram.load_folders()},
        "learned": learned, "autostart": autostart.is_enabled(), "known_names": known, "public_chats": open_chats}


def validate_settings(body):
    clean = {k: v for k, v in body.items() if k in SETTINGS_KEYS}
    for feed in clean.get("feeds", []):
        if not re.match(r"https?://", feed.get("url", "")):
            raise ValueError("Адрес ленты должен начинаться с http:// или https://")
    for channel in clean.get("tg_public", []):
        if not re.fullmatch(r"@?[A-Za-z0-9_]{4,}", channel.get("username", "")):
            raise ValueError("Имя канала: латиница, цифры и подчёркивание, например durov")
    for mode in clean.get("modes", []):
        mode["budget"] = max(1, min(20, int(mode["budget"])))
        mode["topics"] = {k: max(0.0, min(2.0, float(v))) for k, v in mode["topics"].items()}
    for key, least in (("budget_main", 1), ("topic_cap", 1), ("chat_interval_min", 1), ("news_interval_min", 5), ("model_interval_min", 5)):
        if key in clean:
            clean[key] = max(least, int(clean[key]))
    if "interests" in clean:
        prefs = clean["interests"]
        clean["interests"] = {"about": [str(a).strip()[:200] for a in prefs.get("about", []) if str(a).strip()][:40],
                              "watch": [str(w).strip()[:120] for w in prefs.get("watch", []) if str(w).strip()][:60]}
    if "claude" in clean:
        clean["claude"]["profile"] = str(clean["claude"].get("profile", ""))[:claude_ask.PROFILE_CHARS]
    if "privacy" in clean:
        clean["privacy"]["claude_closed_chats"] = [str(n) for n in clean["privacy"].get("claude_closed_chats", [])][:300]
    if "full_collect_hour" in clean:
        clean["full_collect_hour"] = max(0, min(23, int(clean["full_collect_hour"])))
    return clean


def task_action(body):
    """Правка «Актуального». Запись и обновление экрана идут по одному."""
    cfg = load_config()
    action = body.get("action")
    line, raw = body.get("line", -1), body.get("raw", "")
    due = body.get("due") or None
    if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
        raise ValueError("Дата должна быть в формате ГГГГ-ММ-ДД")
    with _agenda_lock, db.connect() as conn:
        if action == "add":
            result = agenda.add(cfg, body.get("text", ""), due)
        elif action == "done":
            result = agenda.set_done(cfg, line, raw, bool(body.get("done")))
        elif action == "status":
            result = agenda.set_status(cfg, line, raw, str(body.get("status")))
        elif action == "edit":
            result = agenda.edit(cfg, line, raw, body.get("text", ""), due)
        elif action == "delete":
            agenda.delete(cfg, line, raw)
            result = {}
        elif action == "reject":
            deadlines.reject(conn, cfg, line, raw)
            result = {}
        else:
            raise ValueError("Нет такого действия")
        pipeline.refresh_tasks(conn, cfg)
    return result


def suggest_payload(home=False):
    cfg = load_config()
    with db.connect() as conn:
        return suggest.home(conn, cfg) if home else suggest.view(conn, cfg)


def suggest_task(uid):
    """«В дела»: предложение становится строкой «Актуального», со сроком, если он был в тексте."""
    cfg = load_config()
    with db.connect() as conn:
        item = next((s for s in suggest.candidates(conn, cfg) if s["uid"] == uid), None)
    if item is None:
        raise LookupError("Такого предложения уже нет")
    result = task_action({"action": "add", "text": f"{item['title'][:120]} · {item['source']}", "due": item["when"]})
    with db.connect() as conn:
        suggest.rate(conn, uid, 1)
    return result


def suggest_about(text):
    cfg = load_config()
    cfg["claude"]["profile"] = str(text or "").strip()[:claude_ask.PROFILE_CHARS]
    save_config(cfg)


def suggest_kinds(mode_id, kinds):
    save_config(suggest.set_kinds(load_config(), mode_id, kinds))


def set_mode(mode_id):
    cfg = load_config()
    if mode_id not in {m["id"] for m in cfg["modes"]}:
        raise ValueError("Нет такого режима")
    cfg["active_mode"] = mode_id
    save_config(cfg)
    rescore_later()


def set_chat_level(name, level):
    if level not in LEVELS:
        raise ValueError("Нет такого уровня")
    cfg = load_config()
    chat = next((c for c in cfg["tg_private"] if c["name"] == name), None)
    if chat is None:
        raise ValueError("Этого чата нет в списке")
    chat["level"] = level
    save_config(cfg)
    rescore_later()


def rescore(cfg):
    with db.connect() as conn:
        analysis.run(conn, cfg)


_rescore_lock = threading.Lock()
_pending_lock = threading.Lock()
_pending = [0]  # сколько пересчётов ждёт или идёт


def rescore_later():
    """Пересчёт оценок в фоне: экран меняется сразу, порядок новостей догоняет за секунды."""
    def work():
        with _rescore_lock:  # подряд нажатые режимы считаются по очереди, последний побеждает
            try:
                rescore(load_config())
            finally:
                with _pending_lock:
                    _pending[0] -= 1
    with _pending_lock:
        _pending[0] += 1
    threading.Thread(target=work, daemon=True).start()


def is_rescoring():
    return _pending[0] > 0


def mark_chat(name, why):
    """«Сделано» просто убирает чат из ждущих, а «Не важно» ещё и учит Пульс."""
    with db.connect() as conn:
        analysis.dismiss_chat(conn, name)
        return {"quiet": why == "unimportant" and learning.note_ignored(conn, load_config(), name)}


def update_item(item_id, body):
    fields, args = [], []
    if body.get("state") in ("new", "seen"):
        fields.append("state=?")
        args.append(body["state"])
    if body.get("feedback") in (-1, 0, 1):
        fields.append("feedback=?")
        args.append(body["feedback"])
    if body.get("queued") in (0, 1):
        fields.append("queued=?")
        args.append(body["queued"])
    if not fields:
        raise ValueError("Нечего менять")
    with db.connect() as conn:
        conn.execute(f"UPDATE items SET {', '.join(fields)} WHERE id=?", (*args, item_id))
    if "feedback" in body:  # отметка должна сказаться сразу, а не при следующем сборе новостей
        rescore_later()


class Handler(BaseHTTPRequestHandler):
    server_version = "Pulse"
    remote = False

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        if self.remote:
            allowed = remote.host_allowed(self.headers.get("Host", ""), remote.remote_port(PORT))
            allowed = allowed and remote.client_allowed(self.client_address[0])
        else:
            allowed = self.headers.get("Host") in ALLOWED_HOSTS
        if allowed:
            return True
        self._json({"error": "Запрещённый адрес"}, 403)
        return False

    def _authorized(self, url, query):
        """С этого компьютера вход не нужен, с телефона нужен ключ из QR-кода."""
        if not self.remote:
            return True
        key = load_config()["remote"]["key"]
        if remote.key_matches(key, (query.get("key") or [""])[0]):
            self.send_response(302)
            self.send_header("Set-Cookie", remote.cookie_header(key))
            self.send_header("Location", "/")
            self.end_headers()
            return False
        if not remote.key_matches(key, remote.cookie_key(self.headers.get("Cookie"))):
            data = remote.LOGIN_PAGE.encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return False
        if url.path in LOOPBACK_ONLY:
            self._json({"error": "Это меняется только на компьютере"}, 403)
            return False
        return True

    def do_GET(self):
        if not self._host_ok():
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if not self._authorized(url, query):
            return
        try:
            if url.path == "/api/today":
                return self._json(today_payload())
            if url.path == "/api/suggest":
                return self._json(suggest_payload(home="home" in query))
            if url.path == "/api/suggest-run":
                return self._json(suggest.status())
            if url.path == "/api/feed":
                return self._json(feed_payload(query))
            if url.path == "/api/analytics":
                with db.connect() as conn:
                    return self._json(analysis.analytics_view(conn, load_config()))
            if url.path == "/api/settings":
                return self._json(settings_payload())
            if url.path == "/api/tasks":
                with db.connect() as conn:
                    return self._json(analysis.tasks_view(conn, load_config()))
            if url.path == "/api/ask-claude":
                return self._json(claude_ask.status((query.get("id") or [""])[0]))
            if url.path == "/api/news-digest":
                return self._json(digest.status())
            if url.path == "/api/remote":
                return self._json(remote.status(load_config(), PORT))
            if url.path == "/api/version":
                return self._json({"version": VERSION})
            if url.path == "/api/status":
                with db.connect() as conn:
                    return self._json({"running": pipeline.is_running(), "stage": pipeline.stage(), "error": pipeline.last_error(),
                                       "last_run": db.kv_get(conn, "last_run"), "marks": db.kv_get(conn, "run_marks", {}),
                                       "rescoring": is_rescoring()})
        except Exception as error:
            return self._json({"error": f"{type(error).__name__}: {error}"}, 500)
        self._static(url.path)

    def _static(self, path):
        name = "index.html" if path == "/" else path.lstrip("/")
        target = (WEB / name).resolve()
        if not target.is_file() or WEB.resolve() not in target.parents:
            self.send_error(404)
            return
        data = target.read_bytes()
        self.send_response(200)
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        # Тело читаем до проверок: иначе на отказ Windows обрывает соединение вместо ответа
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            return self._json({"error": "Слишком большой запрос"}, 413)
        raw = self.rfile.read(length)
        if not self._host_ok():
            return
        url = urlparse(self.path)
        if not self._authorized(url, {}):
            return
        origin = urlparse(self.headers.get("Origin", "")).netloc
        if origin and origin != self.headers.get("Host"):
            return self._json({"error": "Запрос с чужого сайта"}, 403)
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._json({"error": "Нужен JSON"}, 415)
        try:
            body = json.loads(raw or b"{}")
            path = urlparse(self.path).path
            summary = re.fullmatch(r"/api/item/(\d+)/summary", path)
            if summary:
                return self._json(summaries.request(int(summary[1]), load_config(), body.get("engine")))
            match = re.fullmatch(r"/api/item/(\d+)", path)
            if match:
                update_item(int(match[1]), body)
                return self._json({"ok": True})
            if path == "/api/shutdown":
                threading.Timer(0.2, os._exit, [0]).start()  # новая версия заменяет старую
                return self._json({"ok": True})
            if path == "/api/mode":
                set_mode(body.get("id"))
                return self._json({"ok": True})
            if path == "/api/chat-level":
                set_chat_level(body.get("name"), body.get("level"))
                return self._json({"ok": True})
            if path == "/api/remote":
                return self._json(toggle_remote(body))
            if path == "/api/news-digest":
                return self._json(digest.start())
            if path == "/api/ask-claude":
                return self._json(claude_ask.start(load_config(), body.get("question"), body.get("history") or []))
            if path == "/api/ask":
                return self._json(assistant.answer(load_config(), body.get("question"), body.get("history") or []))
            if path == "/api/tasks":
                return self._json({"ok": True, **task_action(body)})
            if path == "/api/settings":
                cfg = load_config()
                cfg.update(validate_settings(body))
                save_config(cfg)
                threading.Thread(target=pipeline.run_in_background, daemon=True).start()
                return self._json({"ok": True})
            if path == "/api/collect":
                threading.Thread(target=pipeline.run_in_background, args=(bool(body.get("full")),), daemon=True).start()
                return self._json({"ok": True})
            if path == "/api/chat-done":
                return self._json({"ok": True, **mark_chat(body.get("name"), body.get("why"))})
            if path == "/api/suggest-run":
                return self._json(suggest.start(load_config(), body.get("engine")))
            if path == "/api/suggest-rate":
                with db.connect() as conn:
                    suggest.rate(conn, str(body.get("uid")), body.get("feedback"))
                return self._json({"ok": True})
            if path == "/api/suggest-task":
                return self._json({"ok": True, **suggest_task(str(body.get("uid")))})
            if path == "/api/suggest-about":
                suggest_about(body.get("about"))
                return self._json({"ok": True})
            if path == "/api/suggest-kinds":
                suggest_kinds(body.get("mode"), body.get("kinds"))
                return self._json({"ok": True})
            if path == "/api/pick":
                with db.connect() as conn:
                    picks.rate(conn, str(body.get("uid")), body.get("feedback"))
                return self._json({"ok": True})
            if path == "/api/tg-dialogs":
                return self._json(telegram.refresh_dialogs())
            if path == "/api/autostart":
                return self._json({"enabled": autostart.set_enabled(bool(body.get("enabled")))})
            if path == "/api/learned":
                with db.connect() as conn:
                    learning.forget(conn)
                rescore_later()
                return self._json({"ok": True})
        except ValueError as error:
            return self._json({"error": str(error)}, 400)
        except (agenda.Conflict, local_ai.ModelBusy, LookupError) as error:
            return self._json({"error": str(error)}, 409)
        except Exception as error:
            return self._json({"error": f"{type(error).__name__}: {error}"}, 500)
        self._json({"error": "Нет такого действия"}, 404)


def scheduler():
    """Пока сервер запущен, чаты и новости обновляются сами, каждый со своим интервалом."""
    while True:
        try:
            pipeline.run_collection(force=False)
        except Exception as error:
            print(f"Сбор не удался: {error}")
        time.sleep(TICK_SECONDS)


class Server(ThreadingHTTPServer):
    allow_reuse_address = False  # иначе Windows молча пускает второй Пульс на тот же порт


class RemoteHandler(Handler):
    remote = True


def toggle_remote(body):
    cfg = load_config()
    if body.get("reset"):
        cfg["remote"]["key"] = remote.new_key()
    if "enabled" in body:
        cfg["remote"]["enabled"] = bool(body["enabled"])
        cfg["remote"]["key"] = cfg["remote"].get("key") or remote.new_key()
    save_config(cfg)
    apply_remote(cfg)
    return remote.status(cfg, PORT)


def apply_remote(cfg):
    """Сервер для телефона работает, только пока доступ включён."""
    global _remote_server
    if cfg["remote"]["enabled"] and _remote_server is None:
        _remote_server = Server(("0.0.0.0", remote.remote_port(PORT)), RemoteHandler)
        threading.Thread(target=_remote_server.serve_forever, daemon=True).start()
    elif not cfg["remote"]["enabled"] and _remote_server is not None:
        _remote_server.shutdown()
        _remote_server.server_close()
        _remote_server = None


def bind_main():
    """Если работает другая копия Пульса, просим её уступить: новый запуск должен дать новую версию."""
    try:
        return Server(("127.0.0.1", PORT), Handler)
    except OSError:
        pass
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/shutdown", b"{}", {"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=3)
    except OSError:
        pass
    for _ in range(10):
        time.sleep(0.5)
        try:
            return Server(("127.0.0.1", PORT), Handler)
        except OSError:
            continue
    return None


def serve():
    server = bind_main()
    if server is None:
        print("Порт занят старой версией Пульса. Закройте её окно и запустите start.bat снова.")
        input("Нажмите Enter...")
        return
    threading.Thread(target=scheduler, daemon=True).start()
    try:
        apply_remote(load_config())
    except OSError as error:
        print(f"Доступ с телефона не запустился: {error}")
    print(f"Пульс: http://127.0.0.1:{PORT}")
    server.serve_forever()
