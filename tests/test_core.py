import asyncio
import http.client
import io
import json
import os
import sqlite3
import sys
import threading
import unittest
from contextlib import nullcontext, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pulse import agenda, analysis, assistant, autostart, claude_ai, db, deadlines, digest, feeds, interests, learning, local_ai, mcp_server, notify, obsidian, picks, pipeline, promises, remote, replies, summaries, telegram
from pulse.config import DEFAULT_CONFIG, active_mode
from pulse.text import extract_due, keyword_regex, stem

ANCHOR = date(2026, 10, 23)


class Keywords(unittest.TestCase):
    def test_russian_endings(self):
        rx = keyword_regex("сознание")
        for word in ("сознания", "сознании", "Сознанием"):
            self.assertTrue(rx.search(word), word)

    def test_multiword(self):
        self.assertTrue(keyword_regex("ключевая ставка").search("повысили ключевую ставку"))

    def test_caps_are_exact(self):
        rx = keyword_regex("ИИ")
        self.assertTrue(rx.search("новый ИИ от лаборатории"))
        self.assertFalse(rx.search("Россиию и России"))
        self.assertFalse(keyword_regex("AI").search("said paid air"))

    def test_wildcard(self):
        self.assertTrue(keyword_regex("нейронаук*").search("о нейронауках"))

    def test_short_word_does_not_leak(self):
        self.assertFalse(keyword_regex("мем*").search("в это время"))


class Dates(unittest.TestCase):
    def test_bare_day_after_do(self):
        self.assertEqual(extract_due("сделать до 29", ANCHOR), (date(2026, 10, 29), False))

    def test_bare_day_rolls_to_next_month(self):
        self.assertEqual(extract_due("до 5", ANCHOR), (date(2026, 11, 5), False))

    def test_not_a_date(self):
        for text in ("до 18ч свободен", "до 5 баллов", "до 30%", "на 12 часов"):
            self.assertEqual(extract_due(text, ANCHOR), (None, False), text)

    def test_numeric(self):
        self.assertEqual(extract_due("(Вс, 19.07)", date(2026, 7, 13)), (date(2026, 7, 19), False))

    def test_month_name(self):
        self.assertEqual(extract_due("экзамен 12 ноября", ANCHOR), (date(2026, 11, 12), False))

    def test_may_and_march(self):
        self.assertEqual(extract_due("3 мая", ANCHOR)[0], date(2027, 5, 3))
        self.assertEqual(extract_due("3 марта", ANCHOR)[0], date(2027, 3, 3))

    def test_relative_marked(self):
        self.assertEqual(extract_due("завтра в 10", ANCHOR), (date(2026, 10, 24), True))

    def test_version_number_ignored(self):
        self.assertEqual(extract_due("обновить до версии", ANCHOR), (None, False))


class Tasks(unittest.TestCase):
    def test_leaves_with_parents(self):
        text = "- [ ] Сегодня:\n\t- [ ] Сегодня сегодня:\n\t\t- [ ] Потренироваться\n- [x] Готово\n\t- [ ] Ждём письма\n"
        tasks = obsidian.parse_tasks(text)
        self.assertEqual([t["text"] for t in tasks], ["Потренироваться", "Ждём письма"])
        self.assertEqual(tasks[0]["parents"], ["Сегодня", "Сегодня сегодня"])

    def test_agenda_bullets_only_before_heading(self):
        text = "- Читать тему\n### Ссылки\n- Диск\n"
        self.assertEqual([t["text"] for t in obsidian.parse_tasks(text, True)], ["Читать тему"])

    def test_wikilinks_cleaned(self):
        tasks = obsidian.parse_tasks("- [ ] [[Лекция 2|Домашка]] **срочно**\n")
        self.assertEqual(tasks[0]["text"], "Домашка срочно")


class Scoring(unittest.TestCase):
    def ctx(self):
        cfg = {"topics": [{"id": "n", "name": "Нейро", "weight": 1.4, "keywords": ["мозг"], "exclude": []}]}
        return {"now": datetime.now(timezone.utc), "topics": feeds.compile_topics(cfg), "notes": [], "boost": {}, "levels": {},
                "activity": {}, "model": ({}, {}, {}), "blacklist": [keyword_regex("промокод")],
                "me": [keyword_regex("Максим")], "urgent": []}

    def item(self, title, **meta):
        return {"title": title, "body": "", "kind": "news", "meta": meta, "source_name": "x",
                "published": datetime.now(timezone.utc).isoformat()}

    def test_blacklist_zeroes(self):
        self.assertEqual(analysis.score_item(self.item("мозг и промокод"), self.ctx())[0], 0)

    def test_primary_beats_aggregator(self):
        ctx = self.ctx()
        self.assertGreater(analysis.score_item(self.item("Мозг", primary=True), ctx)[0],
                           analysis.score_item(self.item("Мозг"), ctx)[0])

    def test_mention_in_chat_is_high(self):
        item = self.item("посмотри", mentioned=True, chat_kind="группа")
        item["kind"] = "message"
        self.assertEqual(analysis.tier_of(analysis.score_item(item, self.ctx())[0]), "high")

    def test_overdue_task_outranks_future(self):
        today = date(2026, 11, 2)
        late = {"meta": {}, "due": "2026-10-29"}
        later = {"meta": {}, "due": "2026-11-20"}
        self.assertGreater(analysis.score_task(late, today)[0], analysis.score_task(later, today)[0])

    def test_budget_caps_topic_and_cluster(self):
        items = [{"id": i, "cluster": i if i != 1 else 0, "topics": ["a"]} for i in range(6)]
        chosen = analysis.select_main(items, 5, 3)
        self.assertEqual(len(chosen), 3)


class Feeds(unittest.TestCase):
    def test_rss_and_publisher_suffix(self):
        raw = b"""<rss><channel><item><title>Headline - Reuters</title><link>https://x/1</link>
        <source url="u">Reuters</source><pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>"""
        entry = feeds.parse_feed(raw)[0]
        self.assertEqual((entry["title"], entry["author"]), ("Headline", "Reuters"))

    def test_atom_link(self):
        raw = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title>
        <link rel="alternate" href="https://x/2"/><updated>2026-11-02T10:00:00Z</updated></entry></feed>"""
        self.assertEqual(feeds.parse_feed(raw)[0]["url"], "https://x/2")


class Relevance(unittest.TestCase):
    def topic(self, keywords, exclude=()):
        return feeds.compile_topics({"topics": [{"id": "t", "keywords": list(keywords), "exclude": list(exclude)}]})[0]

    def test_single_word_in_body_is_not_a_topic(self):
        topic = self.topic(["сознание", "квалиа"])
        self.assertFalse(feeds.is_on_topic(topic, "Гибель сержанта", "Он долго искал сознание истины"))
        self.assertTrue(feeds.is_on_topic(topic, "Новая теория сознания", ""))
        self.assertTrue(feeds.is_on_topic(topic, "Статья", "о сознании и квалиа"))

    def test_exclusion_wins(self):
        topic = self.topic(["сознание"], ["потерял* сознание"])
        self.assertFalse(feeds.is_on_topic(topic, "Водитель потерял сознание", ""))

    def test_search_result_must_confirm_topic(self):
        cfg = {"topics": [{"id": "t", "keywords": ["consciousness"], "exclude": []}]}
        topics = feeds.compile_topics(cfg)
        raw = """<rss><channel>
          <item><title>Viking Treasure Found</title><link>https://x/1</link></item>
          <item><title>New consciousness theory</title><link>https://x/2</link></item>
        </channel></rss>""".encode("utf-8")
        original = feeds.fetch
        feeds.fetch = lambda url: raw
        try:
            items = feeds.collect_feed({"name": "s", "url": "https://news.google.com/rss/search?q=x", "topic": "t"}, topics, 4)
        finally:
            feeds.fetch = original
        self.assertEqual([i["title"] for i in items], ["New consciousness theory"])


class Telegram(unittest.TestCase):
    def test_supergroup_link(self):
        self.assertEqual(telegram.message_url(-1001234, SimpleNamespace(username=None), 7), "https://t.me/c/1234/7")

    def test_message_mapping(self):
        message = SimpleNamespace(id=5, message="Пара перенесена\nна среду", mentioned=True, is_reply=False,
                                  date=datetime(2026, 11, 2, tzinfo=timezone.utc))
        item = telegram.message_to_item({"id": -1001, "name": "Поток"}, SimpleNamespace(username="flow"), message, "Лёша")
        self.assertEqual(item["title"], "Пара перенесена")
        self.assertTrue(item["meta"]["mentioned"])
        self.assertEqual(item["url"], "https://t.me/flow/5")

    def test_no_write_methods_anywhere(self):
        root = Path(telegram.__file__).parent
        source = (root / "telegram.py").read_text("utf-8") + (root / "mcp_server.py").read_text("utf-8")
        for forbidden in ("send_message", "send_file", "edit_message", "delete_messages", "send_read_acknowledge", "forward_messages"):
            self.assertNotIn(forbidden, source)


class Login(unittest.TestCase):
    GOOD_HASH = "0123456789abcdef0123456789ABCDEF"

    def test_ctrl_v_character_is_rejected(self):
        with self.assertRaises(ValueError):
            telegram.parse_api_hash("\x16")

    def test_valid_values_survive_paste_noise(self):
        self.assertEqual(telegram.parse_api_hash(f'  "{self.GOOD_HASH}" \n'), self.GOOD_HASH)
        self.assertEqual(telegram.parse_api_id(" 12345678 "), 12345678)

    def test_ask_repeats_until_valid(self):
        answers = iter(["abc", "12345678"])
        with redirect_stdout(StringIO()):
            self.assertEqual(telegram.ask("", telegram.parse_api_id, lambda _: next(answers)), 12345678)

    def test_password_paste_is_rejected(self):
        with self.assertRaises(ValueError):
            telegram.parse_password("\x16")

    def test_rejected_keys_are_not_saved(self):
        from telethon.errors import ApiIdInvalidError
        attempts = []

        async def fake_authorize(api_id, api_hash):
            attempts.append(api_id)
            if len(attempts) == 1:
                raise ApiIdInvalidError(request=None)

        answers = iter(["11111111", self.GOOD_HASH, "22222222", self.GOOD_HASH])
        with TemporaryDirectory() as tmp:
            creds = Path(tmp) / "telegram.json"
            with patch.object(telegram, "authorize", fake_authorize), patch.object(telegram, "TG_CREDS_PATH", creds), \
                    patch("builtins.input", lambda _: next(answers)), redirect_stdout(StringIO()):
                telegram.login()
            self.assertEqual(attempts, [11111111, 22222222])
            self.assertEqual(json.loads(creds.read_text("utf-8"))["api_id"], 22222222)

    def test_failed_login_leaves_no_keys(self):
        from telethon.errors import FloodWaitError

        async def fake_authorize(api_id, api_hash):
            raise FloodWaitError(request=None, capture=60)

        answers = iter(["11111111", self.GOOD_HASH])
        with TemporaryDirectory() as tmp:
            creds = Path(tmp) / "telegram.json"
            with patch.object(telegram, "authorize", fake_authorize), patch.object(telegram, "TG_CREDS_PATH", creds), \
                    patch("builtins.input", lambda _: next(answers)), redirect_stdout(StringIO()):
                telegram.login()
            self.assertFalse(creds.exists())


class CodeDelivery(unittest.TestCase):
    def sent(self, kind, next_type=None, timeout=None):
        from telethon.tl.types import auth
        return auth.SentCode(type=kind, phone_code_hash="h", next_type=next_type, timeout=timeout)

    def app_sent(self):
        from telethon.tl.types import auth
        return self.sent(auth.SentCodeTypeApp(length=5), auth.CodeTypeSms(), 60)

    def sms_sent(self):
        from telethon.tl.types import auth
        return self.sent(auth.SentCodeTypeSms(length=5))

    def run_sign_in(self, client, inputs):
        answers = iter(inputs)
        out = StringIO()
        with patch("builtins.input", lambda _: next(answers)), redirect_stdout(out):
            asyncio.run(telegram.sign_in(client))
        return out.getvalue()

    def client(self, sends, sign_ins):
        class Fake:
            def __init__(self):
                self.sends, self.calls = list(sends), []

            async def send_code_request(self, phone):
                result = self.sends.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

            async def sign_in(self, phone=None, code=None, password=None):
                self.calls.append(password or code)
                result = sign_ins.pop(0)
                if result:
                    raise result
        return Fake()

    def test_app_route_and_resend_hint_are_shown(self):
        text = telegram.describe_delivery(self.app_sent())
        self.assertIn("чат «Telegram»", text)
        self.assertIn("через 60 с", text)
        self.assertIn("в SMS", text)

    def test_empty_enter_resends_by_next_route(self):
        client = self.client([self.app_sent(), self.sms_sent()], [None])
        out = self.run_sign_in(client, ["+79991234567", "", "12345"])
        self.assertIn("Telegram отправил код: в SMS", out)
        self.assertEqual(client.calls, ["12345"])

    def test_no_other_route_keeps_waiting(self):
        client = self.client([self.sms_sent()], [None])
        out = self.run_sign_in(client, ["+79991234567", "", "12345"])
        self.assertIn("Другого способа", out)

    def test_flood_wait_on_resend_does_not_abort(self):
        from telethon.errors import FloodWaitError
        client = self.client([self.app_sent(), FloodWaitError(request=None, capture=120)], [None])
        out = self.run_sign_in(client, ["+79991234567", "", "12345"])
        self.assertIn("подождать 120 с", out)
        self.assertEqual(client.calls, ["12345"])

    def test_wrong_code_then_right(self):
        from telethon.errors import PhoneCodeInvalidError
        client = self.client([self.app_sent()], [PhoneCodeInvalidError(request=None), None])
        self.run_sign_in(client, ["+79991234567", "00000", "12345"])
        self.assertEqual(client.calls, ["00000", "12345"])

    def test_wrong_phone_is_asked_again(self):
        from telethon.errors import PhoneNumberInvalidError
        client = self.client([PhoneNumberInvalidError(request=None), self.app_sent()], [None])
        self.run_sign_in(client, ["123", "+79991234567", "12345"])
        self.assertEqual(client.calls, ["12345"])

    def test_cloud_password_after_code(self):
        from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError
        client = self.client([self.app_sent()], [SessionPasswordNeededError(request=None), PasswordHashInvalidError(request=None), None])
        passwords = iter(["bad", "good"])
        with patch.object(telegram.getpass, "getpass", lambda _: next(passwords)):
            self.run_sign_in(client, ["+79991234567", "12345"])
        self.assertEqual(client.calls, ["12345", "bad", "good"])


class QrLogin(unittest.TestCase):
    def fake_qr(self, results):
        class Qr:
            url = "tg://login?token=first"
            recreated = 0

            async def wait(self, timeout=None):
                result = results.pop(0)
                if result:
                    raise result

            async def recreate(self):
                Qr.recreated += 1
                self.url = "tg://login?token=second"
        return Qr()

    def run_qr(self, qr, client=None):
        class Client:
            calls = []

            async def qr_login(self):
                return qr

            async def sign_in(self, password=None):
                self.calls.append(password)
        client = client or Client()
        with TemporaryDirectory() as tmp:
            page = Path(tmp) / "qr.html"
            seen = []
            real_write = telegram.write_qr_page

            def spy(url):
                real_write(url)
                seen.append((url, page.read_text("utf-8")))

            with patch.object(telegram, "TG_QR_PAGE_PATH", page), patch.object(telegram, "write_qr_page", spy), \
                    patch.object(telegram.webbrowser, "open") as browser, redirect_stdout(StringIO()):
                asyncio.run(telegram.sign_in_qr(client))
            self.assertFalse(page.exists(), "страница с токеном не должна оставаться")
        return seen, browser, client

    def test_expired_code_is_refreshed_without_new_tab(self):
        qr = self.fake_qr([asyncio.TimeoutError(), None])
        seen, browser, _ = self.run_qr(qr)
        self.assertEqual([url for url, _ in seen], ["tg://login?token=first", "tg://login?token=second"])
        self.assertEqual(browser.call_count, 1)
        self.assertNotEqual(seen[0][1], seen[1][1])
        self.assertIn("<svg", seen[0][1])

    def test_cloud_password_after_scan(self):
        from telethon.errors import SessionPasswordNeededError
        qr = self.fake_qr([SessionPasswordNeededError(request=None)])
        with patch.object(telegram.getpass, "getpass", lambda _: "secret"):
            _, _, client = self.run_qr(qr)
        self.assertEqual(client.calls, ["secret"])


class Privacy(unittest.TestCase):
    SECRET = "СЕКРЕТНЫЙ-ТЕКСТ-ЧАТА"

    def view(self):
        message = {"id": 1, "title": self.SECRET, "body": self.SECRET, "source_name": "Секретный чат", "topics": [],
                   "links": [], "url": "https://t.me/c/1/1", "source_type": "tg_private", "author": "Секретный автор"}
        news = {"id": 2, "title": "Публичная новость", "body": "", "source_name": "N+1", "topics": [], "links": [],
                "url": "https://x/1", "source_type": "rss", "author": ""}
        return {"actions": [], "agenda": [], "main": [message, news], "rest": [{"name": "t", "items": [message]}],
                "stats": {}, "tiles": {"reply": 2, "chat_due": 1},
                "chats": {"signals": [{"name": "Секретный чат", "preview": self.SECRET}], "quiet": []}}

    def assert_clean(self, text):
        for secret in (self.SECRET, "Секретный чат", "Секретный автор", "t.me/c/"):
            self.assertNotIn(secret, text)

    def test_claude_export_has_no_chat_content(self):
        text = pipeline.render_export(self.view(), [])
        self.assert_clean(text)
        self.assertIn("Публичная новость", text)
        self.assertIn("Ждут ответа: 2", text)

    def test_connector_today_has_no_chat_content(self):
        text = json.dumps(mcp_server.public_today(self.view()), ensure_ascii=False)
        self.assert_clean(text)

    def test_chat_tools_are_closed_by_default(self):
        with patch.object(mcp_server, "load_config", lambda: {"privacy": {"claude_reads_chats": False}, "tg_private": [{"id": 1, "name": "x"}]}):
            names = {t["name"] for t in mcp_server.handle({"method": "tools/list"})["tools"]}
            self.assertEqual(names, {"pulse_today"})
            for tool in ("tg_list_chats", "tg_read_messages", "tg_search"):
                reply = mcp_server.handle({"method": "tools/call", "params": {"name": tool, "arguments": {"chat": "x", "query": "y"}}})
                self.assertTrue(reply["isError"], tool)

    def test_default_config_keeps_chats_closed(self):
        self.assertFalse(DEFAULT_CONFIG["privacy"]["claude_reads_chats"])

    def test_chat_text_cannot_leave_this_computer(self):
        for url in ("https://api.example.com/v1", "http://192.168.1.5:1234/v1", "http://127.0.0.1.evil.com/v1"):
            with self.assertRaises(ValueError):
                local_ai.base_url({"local_ai": {"url": url}})
        self.assertIsNone(local_ai.current_model({"local_ai": {"url": "https://api.example.com/v1"}}))
        with self.assertRaises(ValueError):
            local_ai.summarize({"local_ai": {"url": "https://api.example.com/v1"}}, "m", "c", [{"author": "a", "body": "b"}])


class FakeModel(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def _send(self, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"data": [{"id": "embed-x"}, {"id": "local-model"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeModel.seen.append(body)
        self._send({"choices": [{"message": {"content": "<think>думаю</think>СУТЬ: Обсуждают экзамен.\nДЕЛО: нет"}}]})


class LocalModel(unittest.TestCase):
    def setUp(self):
        FakeModel.seen.clear()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.cfg = {"local_ai": {"url": f"http://127.0.0.1:{self.server.server_port}/v1", "max_chats_per_run": 1}}
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_picks_chat_model_and_parses_reply(self):
        self.assertEqual(local_ai.current_model(self.cfg), "local-model")
        reply = local_ai.summarize(self.cfg, "local-model", "Поток", [{"author": "Аня", "body": "экзамен в среду"}])
        self.assertEqual(reply, {"summary": "Обсуждают экзамен.", "action": ""})
        self.assertIn("<messages>", FakeModel.seen[0]["messages"][1]["content"])

    def test_digest_budget_and_no_repeat(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        now = db.now_iso()
        for chat in ("А", "Б"):
            for n in range(6):
                db.upsert_item(conn, {"uid": f"tg-{chat}-{n}", "kind": "message", "source_type": "tg_private", "source_name": chat,
                                      "title": "т", "body": "текст", "published": now})
        self.assertEqual(local_ai.refresh_digests(conn, self.cfg), 1)
        self.assertEqual(local_ai.refresh_digests(conn, self.cfg), 1)
        self.assertEqual(local_ai.refresh_digests(conn, self.cfg), 0)

    def test_no_model_means_no_work(self):
        cfg = {"local_ai": {"url": "http://127.0.0.1:9/v1", "max_chats_per_run": 1}}
        self.assertEqual(local_ai.refresh_digests(None, cfg), 0)


class Chats(unittest.TestCase):
    def message(self, msg_id, body="Посмотришь?", **meta):
        return {"uid": f"tg--100-{msg_id}", "body": body, "title": body, "meta": {"chat_id": -100, **meta}}

    def test_mention_waits_until_i_answer(self):
        self.assertTrue(analysis.needs_reply(self.message(10, mentioned=True), {}))
        self.assertFalse(analysis.needs_reply(self.message(10, mentioned=True), {"-100": 11}))
        self.assertTrue(analysis.needs_reply(self.message(12, chat_kind="личный"), {"-100": 11}))

    def test_group_chatter_does_not_wait(self):
        self.assertFalse(analysis.needs_reply(self.message(10, chat_kind="группа"), {}))


SAMPLE_NOTE = (
    "\nЗдесь хранится все\n\n- Читать тему **срочно** \n\nСделать сейчас: \n\n- [x] Готовое\n\t- [ ] Подзадача \n"
    "- [ ] Обычное дело \n\n\n### Ссылки\n\n- [диск](https://x) не задача\n"
)


class AgendaFile(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.note = root / "vault" / "00" / "Актуальное.md"
        self.note.parent.mkdir(parents=True)
        self.note.write_bytes(SAMPLE_NOTE.encode("utf-8"))
        self.cfg = {"vault": str(root / "vault"), "obsidian": {"agenda_notes": ["00/Актуальное.md"]}}
        patcher = patch.object(agenda, "backups_dir", lambda: root / "backups")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.backups = root / "backups"

    def text(self):
        return self.note.read_bytes().decode("utf-8")

    def task(self, text):
        return next(t for t in obsidian.parse_tasks(self.text(), True) if t["text"].startswith(text))

    def test_done_changes_only_one_line_and_is_reversible(self):
        task = self.task("Обычное")
        result = agenda.set_done(self.cfg, task["line"], task["raw"], True)
        self.assertEqual(self.text(), SAMPLE_NOTE.replace("- [ ] Обычное дело \n", "- [x] Обычное дело\n"))
        agenda.set_done(self.cfg, result["line"], result["raw"], False)
        self.assertEqual(self.text(), SAMPLE_NOTE.replace("Обычное дело \n", "Обычное дело\n"))

    def test_nested_task_keeps_indent(self):
        task = self.task("Подзадача")
        agenda.set_done(self.cfg, task["line"], task["raw"], True)
        self.assertIn("\t- [x] Подзадача", self.text())

    def test_plain_bullet_becomes_checkbox(self):
        task = self.task("Читать")
        agenda.set_done(self.cfg, task["line"], task["raw"], True)
        self.assertIn("- [x] Читать тему **срочно**", self.text())

    def test_add_goes_under_do_now(self):
        agenda.add(self.cfg, "Новая задача", "2026-11-05")
        self.assertIn("Сделать сейчас: \n\n- [ ] Новая задача (до 05.11)\n- [x] Готовое", self.text())

    def test_edit_replaces_due_and_keeps_markup(self):
        task = self.task("Читать")
        result = agenda.edit(self.cfg, task["line"], task["raw"], "Читать тему **срочно**", "2026-11-05")
        again = agenda.edit(self.cfg, result["line"], result["raw"], "Читать главу (до 05.11)", "2026-11-07")
        self.assertEqual(again["raw"], "- [ ] Читать главу (до 07.11)")

    def test_delete_removes_exactly_one_line(self):
        task = self.task("Обычное")
        agenda.delete(self.cfg, task["line"], task["raw"])
        self.assertEqual(self.text(), SAMPLE_NOTE.replace("- [ ] Обычное дело \n", ""))

    def test_kanban_status_is_kept_in_the_line(self):
        task = self.task("Обычное")
        result = agenda.set_status(self.cfg, task["line"], task["raw"], "/")
        self.assertIn("- [/] Обычное дело\n", self.text())
        moved = self.task("Обычное")
        self.assertEqual(moved["status"], "/")
        result = agenda.set_status(self.cfg, result["line"], result["raw"], "w")
        self.assertEqual(self.task("Обычное")["status"], "w")
        edited = agenda.edit(self.cfg, result["line"], result["raw"], "Другое дело", None)
        self.assertEqual(edited["raw"], "- [w] Другое дело")

    def test_unknown_status_is_refused(self):
        task = self.task("Обычное")
        with self.assertRaises(ValueError):
            agenda.set_status(self.cfg, task["line"], task["raw"], "q")

    def test_done_column_lists_checked_lines_and_they_can_return(self):
        task = self.task("Обычное")
        agenda.set_status(self.cfg, task["line"], task["raw"], "x")
        done = agenda.done_tasks(self.cfg)
        self.assertEqual(done[0]["title"], "Обычное дело")
        agenda.set_status(self.cfg, done[0]["line"], done[0]["raw"], " ")
        self.assertTrue(self.task("Обычное"))

    def test_conflict_when_line_changed_in_obsidian(self):
        task = self.task("Обычное")
        self.note.write_bytes(SAMPLE_NOTE.replace("Обычное дело", "Другое дело").encode("utf-8"))
        with self.assertRaises(agenda.Conflict):
            agenda.set_done(self.cfg, task["line"], task["raw"], True)

    def test_moved_line_is_still_found(self):
        task = self.task("Обычное")
        self.note.write_bytes(("Новая строка\n" + SAMPLE_NOTE).encode("utf-8"))
        agenda.set_done(self.cfg, task["line"], task["raw"], True)
        self.assertIn("- [x] Обычное дело", self.text())

    def test_windows_line_endings_survive(self):
        self.note.write_bytes(SAMPLE_NOTE.replace("\n", "\r\n").encode("utf-8"))
        agenda.add(self.cfg, "Задача")
        raw = self.note.read_bytes()
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))

    def test_chat_section_is_created_once_before_first_heading(self):
        agenda.add(self.cfg, "Первый срок", "2026-11-05", section=agenda.CHAT_SECTION)
        agenda.add(self.cfg, "Второй срок", "2026-11-06", section=agenda.CHAT_SECTION)
        text = self.text()
        self.assertEqual(text.count("### Сроки из чатов"), 1)
        self.assertLess(text.index("### Сроки из чатов"), text.index("### Ссылки"))
        self.assertLess(text.index("Первый срок"), text.index("Второй срок"))
        tasks = {t["text"]: t["heading"] for t in obsidian.parse_tasks(text, True)}
        self.assertEqual(tasks["Второй срок (до 06.11)"], "Сроки из чатов")

    def test_input_cannot_break_file_structure(self):
        agenda.add(self.cfg, "### Заголовок\n- [ ] ещё строка\x07")
        self.assertIn("- [ ] Заголовок - [ ] ещё строка\n", self.text())
        with self.assertRaises(ValueError):
            agenda.add(self.cfg, "   \n ")

    def test_every_write_makes_a_backup(self):
        agenda.add(self.cfg, "Раз")
        agenda.add(self.cfg, "Два")
        copies = sorted(self.backups.iterdir())
        self.assertEqual(len(copies), 2)
        self.assertEqual(copies[0].read_bytes().decode("utf-8"), SAMPLE_NOTE)

    def test_no_temp_file_left_in_vault(self):
        agenda.add(self.cfg, "Задача")
        self.assertEqual([p.name for p in self.note.parent.iterdir()], ["Актуальное.md"])


class TaskAnchors(unittest.TestCase):
    def test_writing_file_does_not_refresh_old_relative_dates(self):
        """Запись меняет время файла: старое «сегодня» не должно стать сегодняшним."""
        with TemporaryDirectory() as tmp:
            vault = Path(tmp)
            note = vault / "a.md"
            note.write_text("- [ ] Позвонить сегодня\n- [ ] Сдать до 29\n", "utf-8")
            old = datetime(2026, 10, 23, 12, tzinfo=timezone.utc).timestamp()
            os.utime(note, (old, old))
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.executescript(db.SCHEMA)
            opts = {"include": ["a.md"], "exclude": [], "agenda_notes": ["a.md"], "stale_days": 45}
            today = date(2026, 11, 2)
            obsidian.collect_tasks(conn, vault, opts, today)
            os.utime(note, None)  # файл только что записали
            obsidian.collect_tasks(conn, vault, opts, today)
            rows = {r["title"]: (r["due"], json.loads(r["meta"]).get("stale")) for r in conn.execute("SELECT * FROM items")}
        self.assertEqual(rows["Сдать до 29"][0], "2026-10-29")
        self.assertIsNone(rows["Позвонить сегодня"][0])
        self.assertIn("написано 23.10", rows["Позвонить сегодня"][1])


class ChatDeadlines(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.note = root / "vault" / "Актуальное.md"
        self.note.parent.mkdir()
        self.note.write_bytes(SAMPLE_NOTE.encode("utf-8"))
        self.cfg = {"vault": str(root / "vault"), "obsidian": {"agenda_notes": ["Актуальное.md"]},
                    "deadlines": {"auto_add": True, "max_per_run": 2}}
        patcher = patch.object(agenda, "backups_dir", lambda: root / "backups")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        self.due = (date.today() + timedelta(days=3)).isoformat()

    def message(self, n, text, chat="Поток"):
        db.upsert_item(self.conn, {"uid": f"tg-1-{n}", "kind": "message", "source_type": "tg_private", "source_name": chat,
                                   "title": text, "body": text, "published": db.now_iso(), "due": self.due})

    def text(self):
        return self.note.read_bytes().decode("utf-8")

    def test_without_model_only_certain_deadlines_pass(self):
        self.message(1, "Дедлайн по эссе в пятницу.")
        self.message(2, "Может встретимся в пятницу")
        self.message(3, "Когда дедлайн по эссе?")
        self.assertEqual(deadlines.process(self.conn, self.cfg), 1)
        self.assertIn("Дедлайн по эссе в пятницу · Поток", self.text())
        self.assertNotIn("встретимся", self.text())
        self.assertNotIn("Когда", self.text())

    def test_after_date_is_not_a_deadline(self):
        self.message(1, "Кто берёт презентацию? Экзамен у всех после 12 ноября")
        self.assertEqual(deadlines.process(self.conn, self.cfg), 0)

    def test_task_takes_the_sentence_with_the_deadline(self):
        self.message(1, "Всем привет. Дедлайн по эссе в пятницу. Удачи")
        deadlines.process(self.conn, self.cfg)
        self.assertIn("- [ ] Дедлайн по эссе в пятницу · Поток", self.text())

    def test_same_deadline_in_two_chats_is_added_once(self):
        self.message(1, "Дедлайн по эссе в пятницу", "Поток")
        self.message(2, "Дедлайн по эссе в пятницу", "Группа")
        self.assertEqual(deadlines.process(self.conn, self.cfg), 1)

    def test_nothing_is_added_twice_across_runs(self):
        self.message(1, "Экзамен в пятницу")
        deadlines.process(self.conn, self.cfg)
        self.assertEqual(deadlines.process(self.conn, self.cfg), 0)

    def test_limit_per_run(self):
        for n, word in enumerate(("эссе", "реферат", "проект", "доклад")):
            self.message(n, f"Дедлайн: {word}")
        self.assertEqual(deadlines.process(self.conn, self.cfg), 2)

    def test_rejected_never_returns(self):
        self.message(1, "Дедлайн по эссе в пятницу")
        deadlines.process(self.conn, self.cfg)
        task = next(t for t in obsidian.parse_tasks(self.text(), True) if "эссе" in t["text"])
        deadlines.reject(self.conn, self.cfg, task["line"], task["raw"])
        self.assertNotIn("эссе", self.text())
        self.assertEqual(deadlines.process(self.conn, self.cfg), 0)
        self.assertEqual(self.conn.execute("SELECT status FROM chat_deadlines").fetchone()["status"], "rejected")

    def test_model_can_refuse_and_rephrase(self):
        self.message(1, "Сдать эссе в пятницу, не забудьте")
        self.message(2, "Экзамен по приколу отменён")
        answers = iter(["СРОК: да\nЗАДАЧА: Сдать эссе", "СРОК: нет\nЗАДАЧА: нет"])
        with patch.object(local_ai, "ask", lambda *a, **k: next(answers)):
            self.assertEqual(deadlines.process(self.conn, self.cfg, self.cfg, "model"), 1)
        self.assertIn("Сдать эссе · Поток", self.text())
        self.assertNotIn("приколу", self.text())

    def test_model_task_is_capitalized(self):
        self.message(1, "Сдать эссе в пятницу")
        with patch.object(local_ai, "ask", lambda *a, **k: "СРОК: да\nЗАДАЧА: сдать эссе"):
            deadlines.process(self.conn, self.cfg, self.cfg, "model")
        self.assertIn("- [ ] Сдать эссе · Поток", self.text())

    def test_off_switch(self):
        self.message(1, "Дедлайн по эссе")
        self.cfg["deadlines"]["auto_add"] = False
        self.assertEqual(deadlines.process(self.conn, self.cfg), 0)
        self.assertEqual(self.text(), SAMPLE_NOTE)

    def test_chat_tasks_never_reach_claude(self):
        chat_task = {"id": 1, "title": "Секрет из чата", "reasons": [], "source_name": "Актуальное",
                     "meta": {"heading": agenda.CHAT_SECTION}}
        own = {"id": 2, "title": "Своё дело", "reasons": [], "source_name": "Актуальное", "meta": {"heading": ""}}
        view = {"actions": [chat_task, own], "agenda": [chat_task], "main": [], "rest": [],
                "stats": {}, "tiles": {"reply": 0, "chat_due": 0}}
        text = pipeline.render_export(view, [])
        self.assertNotIn("Секрет", text)
        self.assertIn("Своё дело", text)
        self.assertNotIn("Секрет", json.dumps(mcp_server.public_today(view), ensure_ascii=False))


class Modes(unittest.TestCase):
    def ctx(self, **extra):
        cfg = {"topics": [{"id": "n", "name": "Нейро", "weight": 1.0, "keywords": ["мозг"], "exclude": []}]}
        base = {"now": datetime.now(timezone.utc), "topics": feeds.compile_topics(cfg), "notes": [], "boost": {}, "levels": {},
                "activity": {}, "model": ({}, {}, {}), "blacklist": [], "me": [], "urgent": []}
        return {**base, **extra}

    def item(self, kind="news", name="x", **meta):
        return {"title": "Мозг", "body": "", "kind": kind, "meta": meta, "source_name": name, "published": datetime.now(timezone.utc).isoformat()}

    def test_mode_changes_topic_weight(self):
        plain = analysis.score_item(self.item(), self.ctx())[0]
        boosted = analysis.score_item(self.item(), self.ctx(boost={"n": 2.0}))[0]
        quiet = analysis.score_item(self.item(), self.ctx(boost={"n": 0.5}))[0]
        self.assertGreater(boosted, plain)
        self.assertLess(quiet, plain)

    def test_level_changes_chat_score(self):
        always = analysis.score_item(self.item("message", "A", chat_kind="группа"), self.ctx(levels={"A": "всегда"}))[0]
        normal = analysis.score_item(self.item("message", "A", chat_kind="группа"), self.ctx())[0]
        never = analysis.score_item(self.item("message", "A", chat_kind="группа"), self.ctx(levels={"A": "никогда"}))[0]
        self.assertGreater(always, normal)
        self.assertEqual(never, 0)

    def test_default_modes_exist_and_budget_is_used(self):
        self.assertEqual([m["id"] for m in DEFAULT_CONFIG["modes"]], ["all", "study", "projects", "rest"])
        cfg = {"modes": DEFAULT_CONFIG["modes"], "active_mode": "rest"}
        self.assertEqual(active_mode(cfg)["budget"], 3)
        self.assertEqual(active_mode({"modes": DEFAULT_CONFIG["modes"], "active_mode": "нет"})["id"], "all")


class ChatLevels(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        self.since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

    def add(self, chat, n=1, due=None, **meta):
        db.upsert_item(self.conn, {"uid": f"tg-{chat}-{n}", "kind": "message", "source_type": "tg_private", "source_name": chat,
                                   "title": f"текст {chat}", "body": "Посмотришь?", "published": db.now_iso(), "due": due,
                                   "meta": {"chat_id": chat, "chat_kind": "группа", **meta}})

    def view(self, levels, mode="all"):
        cfg = {"tg_private": [{"name": k, "level": v} for k, v in levels.items()], "modes": DEFAULT_CONFIG["modes"], "active_mode": mode}
        return analysis.chat_view(self.conn, self.since, date.today(), cfg)

    def names(self, view):
        return [c["name"] for c in view["signals"]]

    def test_always_shows_any_message(self):
        self.add("Семья")
        self.assertEqual(self.names(self.view({"Семья": "обычно"})), [])
        self.assertEqual(self.names(self.view({"Семья": "всегда"})), ["Семья"])

    def test_quiet_keeps_only_direct_address(self):
        soon = (date.today() + timedelta(days=2)).isoformat()
        self.add("Поток", 1, due=soon)
        self.assertEqual(self.names(self.view({"Поток": "обычно"})), ["Поток"])
        self.assertEqual(self.names(self.view({"Поток": "тише"})), [])
        self.add("Поток", 2, mentioned=True)
        self.assertEqual(self.names(self.view({"Поток": "тише"})), ["Поток"])

    def test_never_hides_the_chat_completely(self):
        self.add("Шум", mentioned=True)
        view = self.view({"Шум": "никогда"})
        self.assertEqual((view["signals"], view["quiet"]), ([], []))

    def test_rest_mode_keeps_only_always_chats(self):
        self.add("Семья", mentioned=True)
        self.add("Работа", mentioned=True)
        self.assertEqual(self.names(self.view({"Семья": "всегда", "Работа": "обычно"}, "rest")), ["Семья"])

    def test_muted_chats_get_no_digest_and_no_deadline(self):
        due = (date.today() + timedelta(days=2)).isoformat()
        self.add("Шум", 1, due=due)
        self.assertEqual(deadlines.candidates(self.conn, date.today(), {"Шум"}), [])
        self.assertTrue(deadlines.candidates(self.conn, date.today()))
        for n in range(2, 8):
            self.add("Шум", n)
        self.assertEqual(local_ai.chats_to_digest(self.conn, {"Шум"}), [])


class ExternalModel(unittest.TestCase):
    def test_foreign_server_is_ignored_unless_enabled(self):
        calls = []
        cfg = {"local_ai": {"url": "http://127.0.0.1:1234/v1"}}
        with patch.object(local_ai, "current_model", lambda c: calls.append(c) or "чужая"), patch.object(local_ai.setup_model, "installed", lambda: False):
            with local_ai.running_model(cfg) as (_, model):
                self.assertIsNone(model)
            self.assertEqual(calls, [], "порт 1234 нельзя опрашивать без разрешения")
            with local_ai.running_model({"local_ai": {**cfg["local_ai"], "external": True}}) as (_, model):
                self.assertEqual(model, "чужая")


class PhoneAccess(unittest.TestCase):
    KEY = "ключ-для-теста"

    def setUp(self):
        from pulse import server as web
        self.web = web
        self.server = web.Server(("127.0.0.1", 0), web.RemoteHandler)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        cfg = {"remote": {"enabled": True, "key": "testkey123"}}
        patches = [patch.object(web, "load_config", lambda: cfg), patch.object(web.remote, "remote_port", lambda p: self.port)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def call(self, path, headers=None, method="GET", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers={"Host": f"127.0.0.1:{self.port}", **(headers or {})})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, dict(response.getheaders()), data

    def test_nothing_without_key(self):
        for path in ("/", "/api/today", "/app.js", "/api/tasks"):
            self.assertEqual(self.call(path)[0], 401, path)
        self.assertEqual(self.call("/?key=wrong")[0], 401)

    def test_key_gives_cookie_and_cookie_gives_access(self):
        status, headers, _ = self.call("/?key=testkey123")
        self.assertEqual(status, 302)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertEqual(self.call("/", {"Cookie": "pulse_key=testkey123"})[0], 200)

    def test_settings_are_closed_from_phone(self):
        auth = {"Cookie": "pulse_key=testkey123", "Content-Type": "application/json"}
        self.assertEqual(self.call("/api/settings", auth)[0], 403)
        self.assertEqual(self.call("/api/remote", auth)[0], 403)
        self.assertEqual(self.call("/api/settings", auth, "POST", "{}")[0], 403)
        self.assertEqual(self.call("/api/shutdown", auth, "POST", "{}")[0], 403)

    def test_domain_host_is_refused(self):
        status = self.call("/", {"Host": f"evil.example:{self.port}", "Cookie": "pulse_key=testkey123"})[0]
        self.assertEqual(status, 403)

    def test_public_client_is_refused(self):
        self.assertFalse(remote.client_allowed("8.8.8.8"))
        for address in ("192.168.1.5", "10.0.0.2", "100.100.1.1", "127.0.0.1"):
            self.assertTrue(remote.client_allowed(address), address)

    def test_public_host_is_refused(self):
        self.assertFalse(remote.host_allowed("8.8.8.8:8775", 8775))
        self.assertTrue(remote.host_allowed("192.168.1.5:8775", 8775))

    def test_key_compare(self):
        self.assertFalse(remote.key_matches("", ""))
        self.assertTrue(remote.key_matches("abc", "abc"))

    def test_disabled_status_reveals_nothing(self):
        self.assertEqual(remote.status({"remote": {"enabled": False, "key": "секрет"}}, 8765), {"enabled": False})


class ModelLifecycle(unittest.TestCase):
    def fake_server(self):
        started = []

        class FakeServer:
            terminated = False
            _handle = 0

            def __init__(self, command, **kwargs):
                started.append(command)

            def poll(self):
                return None

            def terminate(self):
                FakeServer.terminated = True

            def wait(self, timeout=None):
                return 0

        return FakeServer, started

    def test_engine_runs_on_loopback_and_survives_between_questions(self):
        server, started = self.fake_server()
        answers = iter([None, None, "gemma", "gemma", "gemma"])
        self.addCleanup(local_ai._engine.update, process=None, ready=False, reaper=False)
        with patch.object(local_ai.setup_model, "installed", lambda: True), patch.object(local_ai.subprocess, "Popen", server), \
                patch.object(local_ai, "_kill_with_parent", lambda p: None), patch.object(local_ai.threading, "Thread"), \
                patch.object(local_ai, "current_model", lambda cfg: next(answers)), patch.dict(os.environ, {"PULSE_NO_MODEL": ""}):
            with self.assertRaises(RuntimeError):
                with local_ai.running_model({"local_ai": {"url": "http://127.0.0.1:1234/v1"}}) as (cfg, model):
                    self.assertEqual(model, "gemma")
                    self.assertIn(f"127.0.0.1:{local_ai.LLAMA_PORT}", cfg["local_ai"]["url"])
                    raise RuntimeError("ошибка внутри блока")
            self.assertFalse(local_ai._model_lock.locked())
            self.assertFalse(server.terminated)
            with local_ai.running_model({"local_ai": {"url": "http://127.0.0.1:1234/v1"}}) as (_, model):
                self.assertEqual(model, "gemma")
        self.assertEqual(len(started), 1, "второй вопрос не должен заново грузить модель")
        self.assertEqual(started[0][started[0].index("--host") + 1], "127.0.0.1")
        local_ai.stop_engine()
        self.assertTrue(server.terminated)

    def test_engine_keeps_thoughts_out_of_the_answer(self):
        source = Path(local_ai.__file__).read_text("utf-8")
        self.assertNotIn('"--reasoning-format", "none"', source)
        self.assertIn('"--reasoning", "off"', source)

    def test_only_thinking_and_no_answer_is_an_error(self):
        reply = {"choices": [{"message": {"content": "", "reasoning_content": "думаю"}}]}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch.object(local_ai._no_proxy, "open", lambda *a, **k: Response(json.dumps(reply).encode("utf-8"))):
            with self.assertRaises(ValueError):
                local_ai.ask({"local_ai": {"url": "http://127.0.0.1:1/v1"}}, "m", "s", "u")

    def test_busy_model_is_reported(self):
        local_ai._model_lock.acquire()
        self.addCleanup(local_ai._model_lock.release)
        with patch.object(local_ai.setup_model, "installed", lambda: True), patch.object(local_ai, "current_model", lambda cfg: None), \
                patch.dict(os.environ, {"PULSE_NO_MODEL": ""}):
            with self.assertRaises(local_ai.ModelBusy):
                with local_ai.running_model({"local_ai": {"url": "http://127.0.0.1:1234/v1"}}, wait_seconds=0):
                    pass

    def test_second_server_does_not_share_the_port(self):
        from pulse.server import Handler, Server
        first = Server(("127.0.0.1", 0), Handler)
        self.addCleanup(first.server_close)
        with self.assertRaises(OSError):
            Server(("127.0.0.1", first.server_port), Handler)


class ClaudeFallback(unittest.TestCase):
    NEWS = {"kind": "news", "source_type": "rss", "title": "Новость", "url": "https://x/1", "source_name": "N+1", "body": ""}
    CHAT = {"kind": "message", "source_type": "tg_private", "title": "Личное", "url": "", "source_name": "Вера", "body": "секрет"}
    CFG = {"claude": {"enabled": True, "model": "haiku"}}

    def test_private_chat_can_never_reach_claude(self):
        with patch.object(claude_ai, "run", side_effect=AssertionError("дошло до Claude")):
            for item in (self.CHAT, {**self.CHAT, "kind": "post"}, {**self.NEWS, "source_type": "tg_private"}):
                with self.assertRaises(PermissionError):
                    claude_ai.summarize(item, "текст", "промпт", self.CFG)
                self.assertIsNone(summaries.by_claude(item, "текст", self.CFG))

    def test_no_file_access_and_no_foreign_settings(self):
        command = claude_ai.command("claude.exe", "haiku")
        self.assertEqual(command[command.index("--tools") + 1], "WebSearch,WebFetch")
        self.assertIn("--strict-mcp-config", command)
        self.assertIn("--no-session-persistence", command)
        for tool in ("Bash", "Read", "Write", "Edit"):
            self.assertNotIn(tool, " ".join(command))

    def test_session_secrets_are_not_inherited(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_X": "1", "ANTHROPIC_BASE_URL": "x", "PATH": "p"}):
            env = claude_ai.clean_env()
        self.assertNotIn("CLAUDE_CODE_X", env)
        self.assertNotIn("ANTHROPIC_BASE_URL", env)

    def test_expired_login_is_unavailable_not_a_summary(self):
        result = SimpleNamespace(returncode=1, stdout="Failed to authenticate: OAuth session expired", stderr="")
        with patch.object(claude_ai, "find_cli", lambda: "claude.exe"), patch.object(claude_ai.subprocess, "run", lambda *a, **k: result):
            with self.assertRaises(claude_ai.Unavailable):
                claude_ai.run("x", self.CFG)

    def test_order_local_then_claude_then_extract(self):
        text = "Пятьсот студентов прошли опыт с памятью, и результат оказался стабильным. " * 12
        item = {**self.NEWS, "body": text}
        good = {"gist": "Память студентов оказалась стабильной во всём опыте", "points": ["Пятьсот студентов", "Результат стабильный"], "limits": ""}
        with patch.object(summaries, "fetch_article", lambda url, title: text):
            with patch.object(summaries, "by_local_model", lambda *a: good), patch.object(summaries, "by_claude", side_effect=AssertionError):
                self.assertEqual(summaries.build(item, self.CFG)["method"], "model")
            with patch.object(summaries, "by_local_model", lambda *a: None), patch.object(summaries, "by_claude", lambda *a: good):
                self.assertEqual(summaries.build(item, self.CFG)["method"], "claude")
            with patch.object(summaries, "by_local_model", lambda *a: None), patch.object(summaries, "by_claude", lambda *a: None):
                self.assertEqual(summaries.build(item, self.CFG)["method"], "extract")

    def test_button_uses_stronger_model_and_may_search_elsewhere(self):
        seen = {}

        def fake_run(prompt, cfg, model=None):
            seen.update(prompt=prompt, model=model)
            return "НЕТ"

        with patch.object(summaries, "source_text", lambda item: ("", False, "feed")), patch.object(claude_ai, "run", fake_run):
            summaries.build_by_claude(self.NEWS, {"claude": {"enabled": True, "model": "haiku", "digest_model": "sonnet"}})
        self.assertEqual(seen["model"], "sonnet")
        self.assertIn("других надёжных источниках", seen["prompt"])

    def test_button_retries_once_after_a_random_refusal(self):
        answers = iter(["НЕТ", "СУТЬ: Настоящая мысль статьи о памяти людей\nЗАПОМНИТЬ:\n- Первый длинный факт\n- Второй длинный факт\nОГОВОРКИ: нет"])
        with patch.object(summaries, "source_text", lambda item: ("", False, "feed")), patch.object(claude_ai, "run", lambda *a, **k: next(answers)):
            result = summaries.build_by_claude(self.NEWS, {"claude": {"enabled": True, "model": "haiku"}})
        self.assertEqual(result["method"], "claude")

    def test_links_in_summary_become_plain_text(self):
        result = summaries.parse_summary("СУТЬ: Мысль\nЗАПОМНИТЬ:\n- Факт\nОГОВОРКИ: Источники: [Fraza.ua](https://fraza.ua/x)")
        self.assertEqual(result["limits"], "Источники: Fraza.ua")

    def test_closed_page_goes_to_claude_search(self):
        good = {"gist": "Память студентов оказалась стабильной во всём опыте", "points": ["А длинный пункт", "Б длинный пункт"], "limits": ""}
        with patch.object(summaries, "fetch_article", lambda url, title: ""), patch.object(summaries, "by_claude", lambda *a: good):
            result = summaries.build(self.NEWS, self.CFG)
        self.assertEqual((result["basis"], result["method"]), ("search", "claude"))

    def test_too_little_text_is_not_summarized_or_called_full_text(self):
        item = {"kind": "news", "source_type": "rss", "url": "https://x/1", "title": "Хроника", "body": "Последние новости о событиях."}
        calls = []
        with patch.object(summaries, "fetch_article", lambda url, title: "Короткая фраза на странице раздела."), \
                patch.object(summaries, "by_local_model", lambda *a: calls.append(a)), patch.object(summaries, "by_claude", lambda i, t, c: None):
            result = summaries.build(item, self.CFG)
        self.assertEqual(calls, [])
        self.assertEqual(result["method"], "none")

    def test_real_echo_from_screenshot_is_rejected(self):
        bad = {"gist": "главная мысль одним предложением.",
               "points": ["факт/вывод с числами, ещё 3-5 пунктов.", "ОГОВОРКИ: что не показано", "*Анализ статьи:**", "<article>", "Последние новости о войне"],
               "limits": "что не показано/чего не хватает (одной строкой или слово «нет»)"}
        self.assertFalse(summaries.is_usable(bad))

    def test_saved_bad_summary_is_dropped(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        bad = {"gist": "главная мысль одним предложением.", "points": ["а", "б"], "limits": "", "method": "model", "basis": "article"}
        conn.execute("INSERT INTO article_summaries VALUES (1, ?, 'x')", (json.dumps(bad, ensure_ascii=False),))
        self.assertIsNone(summaries.stored(conn, 1))
        self.assertEqual(conn.execute("SELECT count(1) FROM article_summaries").fetchone()[0], 0)

    def test_thinking_before_summary_is_cut_off(self):
        text = "Анализ: нужно найти СУТЬ: и пункты.\nСУТЬ: Настоящая мысль статьи о памяти\nЗАПОМНИТЬ:\n- Первый факт\n- Второй факт\nОГОВОРКИ: нет"
        result = summaries.parse_summary(text)
        self.assertEqual(result["gist"], "Настоящая мысль статьи о памяти")
        self.assertEqual(result["points"], ["Первый факт", "Второй факт"])

    def test_echoed_template_is_rejected(self):
        for bad in ({"gist": "Главная мысль статьи.", "points": ["Факты с числами.", "3-5 пунктов."], "limits": ""},
                    {"gist": "Кофе ускоряет ходы шахматистов, но не делает их точнее.", "points": ["Двести игроков сыграли партии", "Время на ход сократилось"], "limits": ""}):
            self.assertFalse(summaries.is_usable(bad))
        self.assertTrue(summaries.is_usable({"gist": "Сон после обучения улучшает память студентов на 14%", "points": ["412 участников", "Эффект сильнее у недосыпающих"], "limits": ""}))

    def test_post_link_is_followed(self):
        self.assertEqual(summaries.first_link("Читайте https://example.com/a. Интересно"), "https://example.com/a")


class ClaudeButtons(unittest.TestCase):
    NEWS = {"id": 1, "kind": "news", "source_type": "rss", "title": "Открытая новость", "body": "Текст новости", "url": "https://x/1",
            "source_name": "N+1", "topics": []}
    CHAT = {"id": 2, "kind": "message", "source_type": "tg_private", "title": "СЕКРЕТ из чата", "body": "СЕКРЕТ", "url": "",
            "source_name": "Вера", "topics": []}
    GROUP_AS_POST = {**CHAT, "kind": "post"}
    CFG = {"claude": {"enabled": True, "model": "haiku"}}

    def test_digest_takes_only_public_items(self):
        view = {"main": [self.NEWS, self.CHAT], "rest": [{"items": [self.GROUP_AS_POST]}]}
        items = digest.public_items(view)
        self.assertEqual([i["id"] for i in items], [1])
        prompt = digest.render_prompt(items, ["Нейронаука"])
        self.assertIn("Открытая новость", prompt)
        self.assertNotIn("СЕКРЕТ", prompt)

    def test_digest_parses_answer_around_extra_text(self):
        answer = 'Вот сводка: {"headline": "Главное", "summary": "Суть", "sections": [{"title": "А", "text": "Б", "sources": [{"title": "т", "url": "https://x/1"}, {"title": "плохая", "url": "javascript:alert(1)"}]}]}'
        result = digest.parse(answer)
        self.assertEqual(result["headline"], "Главное")
        self.assertEqual(result["date"], date.today().isoformat())
        self.assertEqual(len(result["sections"][0]["sources"]), 1)

    def test_digest_rejects_empty_answer(self):
        for answer in ("НЕТ", '{"summary": "без заголовка"}'):
            with self.assertRaises(ValueError):
                digest.parse(answer)

    def test_claude_button_skips_local_model(self):
        good = {"gist": "Память студентов оказалась стабильной во всём опыте", "points": ["Пятьсот студентов", "Результат стабильный"], "limits": ""}
        with patch.object(summaries, "source_text", lambda item: ("текст " * 100, True, "article")), \
                patch.object(summaries, "by_local_model", side_effect=AssertionError("локальная модель не должна вызываться")), \
                patch.object(summaries, "by_claude", lambda *a: good):
            self.assertEqual(summaries.build_by_claude(self.NEWS, self.CFG)["method"], "claude")

    def test_claude_button_reports_failure(self):
        with patch.object(summaries, "source_text", lambda item: ("", False, "feed")), patch.object(summaries, "by_claude", lambda *a: None):
            result = summaries.build_by_claude(self.NEWS, self.CFG)
        self.assertEqual((result["method"], result["engine"]), ("none", "claude"))

    def test_claude_button_refuses_chats(self):
        with patch.object(summaries, "source_text", lambda item: ("секрет " * 100, True, "article")), \
                patch.object(claude_ai, "run", side_effect=AssertionError("дошло до Claude")):
            self.assertEqual(summaries.build_by_claude(self.CHAT, self.CFG)["method"], "none")


class Assistant(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        db.upsert_item(self.conn, {"uid": "tg-1-1", "kind": "message", "source_type": "tg_private", "source_name": "Поток",
                                   "title": "Эссе сдаём до пятницы", "body": "Эссе сдаём до пятницы", "author": "Анна", "published": db.now_iso()})
        db.upsert_item(self.conn, {"uid": "tg-1-2", "kind": "message", "source_type": "tg_private", "source_name": "Быт",
                                   "title": "Кто забыл кастрюлю", "body": "Кто забыл кастрюлю", "published": db.now_iso()})

    def test_finds_relevant_messages_with_their_source(self):
        context, sources = assistant.build_context(self.conn, "когда сдавать эссе?")
        self.assertIn("Эссе сдаём", context)
        self.assertIn("Анна", context)
        self.assertNotIn("кастрюлю", context)
        self.assertEqual(sources, ["Поток"])

    def test_general_question_uses_digests(self):
        self.conn.execute("INSERT INTO chat_digests VALUES ('Поток','Обсуждают эссе','Сдать эссе','x','y')")
        context, _ = assistant.build_context(self.conn, "что?")
        self.assertIn("Обсуждают эссе", context)
        self.assertNotIn("Сдать эссе", context, "дело без проверки актуальности не передаём")

    def test_summary_question_is_not_a_keyword_search(self):
        db.upsert_item(self.conn, {"uid": "n", "kind": "news", "source_type": "rss", "source_name": "Хабр",
                                   "title": "Важные новости про чат-боты", "published": db.now_iso()})
        self.conn.execute("INSERT INTO chat_digests VALUES ('Поток','Обсуждают эссе','Сдать эссе','x','y')")
        for question in ("Что важного в чатах?", "Какие сроки на этой неделе?", "Кто ждёт моего ответа?"):
            context, sources = assistant.build_context(self.conn, question)
            self.assertIn("Обсуждают эссе", context, question)
            self.assertNotIn("чат-боты", context, question)

    def test_visible_thinking_is_never_shown(self):
        draft = "Пользователь спрашивает, кто ждёт его ответа.\nНеобходимо проанализировать данные."
        with self.assertRaises(ValueError):
            assistant.final_answer(draft)
        self.assertEqual(assistant.final_answer(draft + "\nОТВЕТ: Ждёт Анна."), "Ждёт Анна.")
        self.assertEqual(assistant.final_answer("Эссе надо сдать до 6 ноября."), "Эссе надо сдать до 6 ноября.")

    def test_waiting_question_is_answered_without_model(self):
        for question in ("Кто ждёт моего ответа?", "кто ждет ответа", "Кому надо ответить?"):
            self.assertTrue(assistant.WAITING_QUESTION.search(question), question)
        db.upsert_item(self.conn, {"uid": "tg-5-9", "kind": "message", "source_type": "tg_private", "source_name": "Мама",
                                   "title": "Позвони вечером", "body": "Позвони вечером", "published": db.now_iso(),
                                   "meta": {"chat_id": 5, "chat_kind": "личный"}})
        cfg = {"tg_private": [], "modes": DEFAULT_CONFIG["modes"], "active_mode": "all"}
        result = assistant.waiting_answer(cfg, self.conn)
        self.assertIn("Мама: Позвони вечером", result["answer"])
        self.assertIn("Ждут ответа: 1", result["answer"])

    def test_general_question_includes_who_is_waiting(self):
        db.upsert_item(self.conn, {"uid": "tg-5-9", "kind": "message", "source_type": "tg_private", "source_name": "Мама",
                                   "title": "Позвони вечером", "body": "Позвони вечером", "published": db.now_iso(),
                                   "meta": {"chat_id": 5, "chat_kind": "личный"}})
        cfg = {"tg_private": [], "modes": DEFAULT_CONFIG["modes"], "active_mode": "all"}
        context, _ = assistant.build_context(self.conn, "Что важного в чатах?", cfg)
        self.assertIn("Позвони вечером", context)

    def test_chat_summary_comes_from_digests_without_model(self):
        for question in ("Сделай саммари по чатам", "Что важного в чатах?", "итоги по чатам"):
            self.assertTrue(assistant.CHATS_QUESTION.search(question), question)
        cfg = {"tg_private": [], "modes": DEFAULT_CONFIG["modes"], "active_mode": "all"}
        self.assertEqual(assistant.chats_answer(self.conn, cfg)["answer"], "В чатах сейчас нет незакрытых дел.")
        self.conn.execute("INSERT INTO chat_digests VALUES ('Поток','Обсуждают эссе','Сдать эссе','tg-1-1','y')")
        self.assertIn("Поток: Сдать эссе", assistant.chats_answer(self.conn, cfg)["answer"])
        db.kv_set(self.conn, "tg_my_last", {"None": 5})

    def test_most_important_task_is_answered_from_the_list(self):
        for question in ("Какое дело сейчас самое важное?", "Что сделать первым?", "С чего начать?", "какие задачи срочные"):
            self.assertTrue(assistant.TASKS_QUESTION.search(question), question)
        soon = (date.today() + timedelta(days=1)).isoformat()
        db.upsert_item(self.conn, {"uid": "t1", "kind": "task", "source_type": "obsidian", "source_name": "Актуальное", "title": "Сдать эссе",
                                   "published": db.now_iso(), "due": soon, "meta": {"agenda": True}})
        self.conn.execute("UPDATE items SET tier='high', score=80 WHERE uid='t1'")
        cfg = {**DEFAULT_CONFIG, "tg_private": []}
        answer = assistant.tasks_answer(self.conn, cfg)["answer"]
        self.assertTrue(answer.startswith("Самое важное сейчас: Сдать эссе"))

    def test_named_chat_is_searched_even_for_a_long_question(self):
        for n, text in enumerate(("Давайте я сделаю презентацию к рассказу", "Отлично, тогда до вечера", "Кто идёт в кино?")):
            db.upsert_item(self.conn, {"uid": f"tg-9-{n}", "kind": "message", "source_type": "tg_private", "source_name": "Диптех головного мозга 2026 Chat",
                                       "title": text, "body": text, "author": "Олег", "published": db.now_iso()})
        question = "Говорили ли в чате по диптеху сделать презентацию для нашего рассказа по экскурсии по офису?"
        context, sources = assistant.build_context(self.conn, question)
        self.assertIn("сделаю презентацию", context)
        self.assertNotIn("кастрюлю", context)
        self.assertEqual(sources, ["Диптех головного мозга 2026 Chat"])

    def test_one_stray_word_is_not_a_match(self):
        self.assertEqual(assistant.search(self.conn, "эссе кастрюлю поездка билеты самолёт"), [])

    def test_answer_keeps_text_and_drops_references(self):
        raw = "ОТВЕТ: Да, Олег предлагал сделать презентацию [3] [Чат «Диптех», Олег, 31.10].\nИСТОЧНИКИ: 3, 9, 3"
        self.assertEqual(assistant.final_answer(raw), "Да, Олег предлагал сделать презентацию.")
        self.assertEqual(assistant.cited_numbers(raw, 5), [3])
        self.assertEqual(assistant.cited_numbers("ОТВЕТ: Нет.\nИСТОЧНИКИ: нет", 5), [])
        self.assertEqual(assistant.cited_numbers("ОТВЕТ: Да [2].", 5), [2])

    def test_quotes_come_from_the_database_not_the_model(self):
        db.upsert_item(self.conn, {"uid": "tg-9-1", "kind": "message", "source_type": "tg_private", "source_name": "Диптех головного мозга",
                                   "title": "Сделаю презентацию к экскурсии", "body": "Сделаю презентацию к экскурсии", "author": "Олег",
                                   "published": db.now_iso(), "meta": {"chat_id": -105, "chat_kind": "группа"}})
        model_text = "ОТВЕТ: Да, писали.\nИСТОЧНИКИ: 1"
        with patch.object(db, "connect", lambda: nullcontext(self.conn)), \
                patch.object(local_ai, "running_model", lambda cfg: nullcontext((cfg, "gemma"))), patch.object(local_ai, "ask", lambda *a, **k: model_text):
            result = assistant.answer({}, "Писали ли в чате по диптеху про презентацию?")
        self.assertEqual(result["answer"], "Да, писали.")
        self.assertEqual(result["quotes"], [{"source": "Диптех головного мозга", "author": "Олег", "day": datetime.now().strftime("%d.%m"),
                                             "text": "Сделаю презентацию к экскурсии", "link": "tg://openmessage?chat_id=105&message_id=1"}])

    def test_context_lines_are_numbered(self):
        context, _, refs = assistant.gather(self.conn, "когда сдавать эссе?")
        self.assertTrue(context.startswith("[1] Чат «Поток», Анна,"))
        self.assertEqual(len(refs), 1)

    def test_echoed_data_line_is_not_an_answer(self):
        with self.assertRaises(ValueError):
            assistant.final_answer("[Чат «Полынь», автор, 02.11] текст сообщения")

    def test_empty_question(self):
        with self.assertRaises(ValueError):
            assistant.clean_question("   ")

    def test_answer_needs_local_model_and_never_claude(self):
        source = (Path(assistant.__file__)).read_text("utf-8")
        self.assertNotIn("claude_ai", source)


class Replies(unittest.TestCase):
    def test_closed_remarks_need_no_answer(self):
        for text in ("Супер!", "Нет", "Хорошо", "Да", "Ок", "Спасибо!", "Понял, спасибо", "👍", "Хорошего дня", "ага", "+"):
            self.assertEqual(replies.verdict(text), replies.NO, text)

    def test_questions_and_requests_need_an_answer(self):
        for text in ("Ты придёшь?", "Привет! Деньги переслала, проверь и ответь", "Посмотри, пожалуйста, макет", "Можешь сегодня?", "Привет"):
            self.assertEqual(replies.verdict(text), replies.YES, text)

    def test_other_text_goes_to_the_model(self):
        for text in ("Запись встречи доступна на платформе", "Финалим вторник в 18:30", "Я не уверена, какой вариант лучше"):
            self.assertEqual(replies.verdict(text), replies.MAYBE, text)

    def item(self, body, kind="личный", uid="tg-5-9", **meta):
        return {"uid": uid, "body": body, "title": body, "meta": {"chat_id": 5, "chat_kind": kind, **meta}}

    def test_real_examples_from_the_screen(self):
        for body in ("Супер!", "Нет", "Хорошо"):
            self.assertFalse(analysis.needs_reply(self.item(body), {}), body)
        self.assertTrue(analysis.needs_reply(self.item("Деньги переслала, проверь и ответь"), {}))

    def test_group_announcement_with_mention_is_not_a_question(self):
        item = self.item("Запись встречи доступна на платформе", "группа", mentioned=True)
        self.assertFalse(analysis.needs_reply(item, {}))

    def test_model_decides_unclear_cases(self):
        item = self.item("Я не уверена, какой вариант лучше выбрать", "группа", mentioned=True)
        self.assertTrue(analysis.needs_reply(item, {}, {"tg-5-9": True}))
        self.assertFalse(analysis.needs_reply(item, {}, {"tg-5-9": False}))

    def test_unclear_long_personal_message_is_shown_until_checked(self):
        self.assertTrue(analysis.needs_reply(self.item("Я тут подумала про завтрашний план на вечер"), {}))
        self.assertFalse(analysis.needs_reply(self.item("Завтра буду"), {}))
        self.assertFalse(analysis.needs_reply(self.item("Ну, ты конечно преувеличил"), {}))

    def conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        return conn

    def add(self, conn, uid, body, chat="Мама"):
        db.upsert_item(conn, {"uid": uid, "kind": "message", "source_type": "tg_private", "source_name": chat, "title": body, "body": body,
                              "author": "М", "published": db.now_iso(), "meta": {"chat_id": 5, "chat_kind": "личный"}})

    def test_model_check_is_saved_once_and_only_for_unclear(self):
        conn = self.conn()
        self.add(conn, "tg-5-1", "Супер!")
        self.add(conn, "tg-5-2", "Я тут подумала про завтрашний план на вечер")
        with patch.object(local_ai, "ask", lambda *a, **k: "НЕТ"):
            self.assertEqual(replies.refresh(conn, {}, "m"), 1)
            self.assertEqual(replies.refresh(conn, {}, "m"), 0)
        self.assertEqual(replies.load_checks(conn), {"tg-5-2": False})

    def test_dismiss_clears_the_chat_until_a_new_message(self):
        conn = self.conn()
        self.add(conn, "tg-5-3", "Ты придёшь?")
        cfg = {"tg_private": [], "modes": DEFAULT_CONFIG["modes"], "active_mode": "all"}
        since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        waiting = lambda: [c["name"] for c in analysis.chat_view(conn, since, date.today(), cfg)["signals"] if c["reply"]]
        self.assertEqual(waiting(), ["Мама"])
        analysis.dismiss_chat(conn, "Мама")
        self.assertEqual(waiting(), [])
        self.add(conn, "tg-5-4", "А завтра сможешь?")
        self.assertEqual(waiting(), ["Мама"])


class Freshness(unittest.TestCase):
    def conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        return conn

    def add(self, conn, n, body, hours=1, chat="Раиса", kind="личный", **meta):
        moment = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
        db.upsert_item(conn, {"uid": f"tg-7-{n}", "kind": "message", "source_type": "tg_private", "source_name": chat, "title": body, "body": body,
                              "author": "Р", "published": moment, "meta": {"chat_id": 7, "chat_kind": kind, **meta}})

    def digest(self, conn, last_uid, action="Проверить перевод", chat="Раиса"):
        conn.execute("INSERT OR REPLACE INTO chat_digests VALUES (?,?,?,?,?)", (chat, "Обсуждают перевод", action, last_uid, db.now_iso()))

    def view(self, conn):
        cfg = {"tg_private": [], "modes": DEFAULT_CONFIG["modes"], "active_mode": "all"}
        return analysis.chat_view(conn, (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(), date.today(), cfg)

    def test_my_reply_clears_digest_action(self):
        conn = self.conn()
        self.add(conn, 10, "Деньги переслала, проверь и ответь")
        self.digest(conn, "tg-7-10")
        self.assertEqual([c["action"] for c in self.view(conn)["live"]], ["Проверить перевод"])
        db.kv_set(conn, "tg_my_last", {"7": 11})  # вы ответили
        view = self.view(conn)
        self.assertEqual(view["live"], [])
        self.assertEqual([c for c in view["signals"] if c["reply"]], [])

    def test_old_group_notice_is_not_waiting(self):
        item = {"uid": "tg-7-1", "body": "Подключитесь, пожалуйста?", "title": "т", "published": (datetime.now(timezone.utc) - timedelta(hours=40)).isoformat(),
                "meta": {"chat_id": 7, "chat_kind": "группа", "mentioned": True}}
        self.assertTrue(analysis.is_outdated(item))
        self.assertFalse(analysis.needs_reply(item, {}))

    def test_old_personal_question_still_waits_three_days(self):
        moment = (datetime.now(timezone.utc) - timedelta(hours=40)).isoformat()
        item = {"uid": "tg-7-1", "body": "Ты придёшь?", "title": "т", "published": moment, "meta": {"chat_id": 7, "chat_kind": "личный"}}
        self.assertFalse(analysis.is_outdated(item))
        self.assertTrue(analysis.needs_reply(item, {}))

    def test_passed_date_is_outdated_even_if_message_is_fresh(self):
        yesterday = date.today() - timedelta(days=1)
        item = {"uid": "tg-7-1", "body": f"Подключитесь до {yesterday.strftime('%d.%m')}", "title": "т",
                "published": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), "meta": {"chat_kind": "личный"}}
        self.assertTrue(analysis.is_outdated(item))

    def test_action_from_old_message_is_dropped(self):
        conn = self.conn()
        self.add(conn, 10, "Подключиться по ссылке", hours=30, chat="Диптех", kind="группа", mentioned=True)
        self.digest(conn, "tg-7-10", "Подключиться по ссылке", "Диптех")
        self.assertEqual(self.view(conn)["live"], [])

    def test_model_sees_dates_and_what_was_answered(self):
        moment = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        line = local_ai.message_line({"author": "А", "body": "Текст", "uid": "tg-7-5", "published": moment}, 6)
        self.assertIn("[уже отвечено]", line)
        self.assertRegex(line, r"\d\d\.\d\d \d\d:\d\d")
        self.assertNotIn("уже отвечено", local_ai.message_line({"author": "А", "body": "Текст", "uid": "tg-7-7", "published": moment}, 6))


class AnsweredBefore(unittest.TestCase):
    def client(self, mine, private=True):
        """Ведёт себя как Telethon: в личных чатах фильтр по отправителю смотрит только первую страницу."""
        history = [SimpleNamespace(id=100, out=False), SimpleNamespace(id=99, out=False)]
        if mine:
            history.append(SimpleNamespace(id=mine, out=True))

        class Client:
            calls = []

            async def get_entity(self, chat_id):
                return SimpleNamespace(username=None)

            def iter_messages(self, entity, **kwargs):
                Client.calls.append(kwargs)

                async def run():
                    if "min_id" in kwargs:
                        return
                    limit = kwargs["limit"]
                    found = history[:limit]
                    if kwargs.get("from_user") == "me":
                        found = [m for m in (found if private else history) if m.out][:limit]
                    for message in found:
                        yield message
                return run()
        return Client()

    def test_old_reply_is_found_by_asking_telegram(self):
        client = self.client(77)
        items, newest, my_last = asyncio.run(telegram.fetch_chat(client, {"id": 5, "name": "Маша", "kind": "личный"}, 90, True))
        self.assertEqual(my_last, 77)

    def test_second_run_does_not_ask_again(self):
        client = self.client(77)
        result = asyncio.run(telegram.fetch_chat(client, {"id": 5, "name": "Маша", "kind": "личный"}, 90, False))
        self.assertIsNone(result[2])
        self.assertEqual(len(client.calls), 1)

    def test_chat_without_my_messages_is_remembered(self):
        result = asyncio.run(telegram.fetch_chat(self.client(0, private=False), {"id": 5, "name": "Шум", "kind": "группа"}, 90, True))
        self.assertEqual(result[2], 0)

    def test_old_reply_is_found_in_a_group(self):
        client = self.client(77, private=False)
        result = asyncio.run(telegram.fetch_chat(client, {"id": 5, "name": "Группа", "kind": "группа"}, 90, True))
        self.assertEqual(result[2], 77)

    def test_chat_with_zero_from_old_version_is_asked_again(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        db.kv_set(conn, "tg_my_last", {"5": 0})
        seen = {}

        async def collect_async(client, chats, cursors, asked, full=False):
            seen["asked"] = set(asked)
            return [(chats[0], [], 100, 77, None)]

        cfg = {"tg_private": [{"id": 5, "name": "Маша", "kind": "личный"}]}
        with patch.object(telegram, "is_connected", lambda: True), patch.object(telegram, "make_client", lambda: None), \
                patch.object(telegram, "collect_async", collect_async):
            telegram.collect(conn, cfg)
            self.assertEqual(seen["asked"], set())
            self.assertEqual(db.answered_ids(conn)["5"], 77)
            telegram.collect(conn, cfg)
        self.assertEqual(seen["asked"], {"5"})

    def test_channels_are_not_asked(self):
        client = self.client(77)
        asyncio.run(telegram.fetch_chat(client, {"id": 5, "name": "Канал", "kind": "канал"}, 90, True))
        self.assertEqual(len(client.calls), 1)

    def test_bot_never_waits_for_a_reply(self):
        item = {"uid": "tg-5-9", "body": "Ответите?", "title": "т", "meta": {"chat_id": 5, "chat_kind": "бот"}}
        self.assertFalse(analysis.needs_reply(item, {}))

    def test_bot_is_recognized_in_dialogs(self):
        dialog = SimpleNamespace(is_user=True, is_channel=False, is_group=False, entity=SimpleNamespace(bot=True))
        self.assertEqual(telegram.chat_kind(dialog), "бот")
        dialog.entity = SimpleNamespace(bot=False)
        self.assertEqual(telegram.chat_kind(dialog), "личный")

    def test_stored_bot_messages_are_updated(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        db.upsert_item(conn, {"uid": "tg-5-1", "kind": "message", "source_type": "tg_private", "source_name": "Рассылка", "title": "т",
                              "published": db.now_iso(), "meta": {"chat_id": 5, "chat_kind": "личный"}})
        with patch.object(telegram, "load_dialogs", lambda: [{"name": "Рассылка", "kind": "бот"}]):
            pipeline.mark_bots(conn)
        self.assertEqual(json.loads(conn.execute("SELECT meta FROM items").fetchone()["meta"])["chat_kind"], "бот")


class ChatPrivacy(unittest.TestCase):
    def test_queue_never_reaches_claude_export(self):
        secret = {"name": "СЕКРЕТ", "preview": "СЕКРЕТ", "action": "СЕКРЕТ"}
        view = {"actions": [], "agenda": [], "main": [], "rest": [], "stats": {}, "tiles": {"reply": 0, "chat_due": 0},
                "queue": [{"chat": secret}], "fresh": [], "chats": {"signals": [secret]}}
        self.assertNotIn("СЕКРЕТ", pipeline.render_export(view, []))
        self.assertNotIn("СЕКРЕТ", json.dumps(mcp_server.public_today(view), ensure_ascii=False))

    def test_channels_move_to_news_but_groups_stay_private(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        for uid, kind in (("a", "канал"), ("b", "группа"), ("c", "личный")):
            db.upsert_item(conn, {"uid": uid, "kind": "message", "source_type": "tg_private", "source_name": "x", "title": "т",
                                  "published": db.now_iso(), "meta": {"chat_kind": kind}})
        pipeline.move_channels_to_news(conn)
        kinds = {r["uid"]: (r["kind"], r["source_type"]) for r in conn.execute("SELECT uid, kind, source_type FROM items")}
        self.assertEqual(kinds, {"a": ("post", "tg_public"), "b": ("message", "tg_private"), "c": ("message", "tg_private")})


def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


class Schedule(unittest.TestCase):
    CFG = {"chat_interval_min": 5, "news_interval_min": 30, "model_interval_min": 30, "full_collect_hour": 5}
    NOW = datetime(2026, 11, 3, 12, 0).astimezone()

    def marks(self, **minutes):
        return {k: (self.NOW - timedelta(minutes=v)).isoformat() for k, v in minutes.items()}

    def test_first_run_is_full(self):
        self.assertTrue(pipeline.plan({}, self.CFG, self.NOW)["full"])

    def test_nothing_due_means_no_work(self):
        todo = pipeline.plan(self.marks(full=60, chats=2, news=2, model=2), self.CFG, self.NOW)
        self.assertFalse(any(todo.values()))

    def test_chats_are_due_earlier_than_news(self):
        todo = pipeline.plan(self.marks(full=60, chats=6, news=10, model=10), self.CFG, self.NOW)
        self.assertEqual(todo, {"full": False, "chats": True, "news": False, "model": False})

    def test_button_refreshes_everything_except_full(self):
        todo = pipeline.plan(self.marks(full=60, chats=1, news=1, model=1), self.CFG, self.NOW, force=True)
        self.assertEqual(todo, {"full": False, "chats": True, "news": True, "model": True})

    def test_full_runs_once_a_day_after_the_hour(self):
        yesterday = datetime(2026, 11, 2, 23, 0).astimezone().isoformat()
        self.assertTrue(pipeline.full_is_due(yesterday, 5, datetime(2026, 11, 3, 5, 1).astimezone()))
        self.assertFalse(pipeline.full_is_due(yesterday, 5, datetime(2026, 11, 3, 4, 59).astimezone()))
        today = datetime(2026, 11, 3, 5, 1).astimezone().isoformat()
        self.assertFalse(pipeline.full_is_due(today, 5, datetime(2026, 11, 3, 23, 0).astimezone()))

    def test_computer_off_for_days_does_not_wait_for_the_hour(self):
        old = datetime(2026, 10, 30, 12, 0).astimezone().isoformat()
        self.assertTrue(pipeline.full_is_due(old, 5, datetime(2026, 11, 3, 2, 0).astimezone()))


class QuickCollect(unittest.TestCase):
    def client(self, tops):
        class Client:
            fetched = []

            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def is_user_authorized(self):
                return True

            def iter_dialogs(self, limit):
                async def run():
                    for chat_id, top in tops.items():
                        yield SimpleNamespace(id=chat_id, message=SimpleNamespace(id=top))
                return run()

            async def get_entity(self, chat_id):
                Client.fetched.append(chat_id)
                return SimpleNamespace(username=None)

            def iter_messages(self, entity, **kwargs):
                async def run():
                    return
                    yield
                return run()
        return Client()

    def test_chats_without_news_are_not_touched(self):
        client = self.client({1: 100, 2: 205})
        chats = [{"id": 1, "name": "А", "kind": "личный"}, {"id": 2, "name": "Б", "kind": "личный"}]
        results = asyncio.run(telegram.collect_async(client, chats, {"1": 100, "2": 200}, {"1", "2"}))
        self.assertEqual(client.fetched, [2])
        self.assertEqual(len(results), 1)

    def test_new_chat_is_read_even_without_news(self):
        client = self.client({})
        asyncio.run(telegram.collect_async(client, [{"id": 3, "name": "В", "kind": "группа"}], {}, set()))
        self.assertEqual(client.fetched, [3])

    def test_full_reads_every_chat_from_the_window_start(self):
        client = self.client({1: 100})
        with patch.object(telegram, "TG_DIALOGS_PATH", SimpleNamespace(write_text=lambda *a: None)), \
                patch.object(telegram, "chat_kind", lambda d: "личный"):
            client.iter_dialogs = lambda limit: self._dialogs()
            asyncio.run(telegram.collect_async(client, [{"id": 1, "name": "А", "kind": "личный"}], {"1": 100}, {"1"}, full=True))
        self.assertEqual(client.fetched, [1])

    async def _dialogs(self):
        yield SimpleNamespace(id=1, name="А", message=SimpleNamespace(id=100), unread_count=0, date=None, archived=False)

    def test_quiet_chat_gets_a_cursor(self):
        old = datetime.now(timezone.utc) - timedelta(days=30)

        class Client:
            async def get_entity(self, chat_id):
                return SimpleNamespace(username=None)

            def iter_messages(self, entity, **kwargs):
                async def run():
                    yield SimpleNamespace(id=50, date=old, out=False, message="старое")
                return run()
        items, newest, _ = asyncio.run(telegram.fetch_chat(Client(), {"id": 1, "name": "А", "kind": "группа"}, None))
        self.assertEqual((items, newest), ([], 50))

    def test_my_promise_is_kept_as_private_outgoing(self):
        now = datetime.now(timezone.utc)

        class Client:
            async def get_entity(self, chat_id):
                return SimpleNamespace(username=None)

            def iter_messages(self, entity, **kwargs):
                async def run():
                    yield SimpleNamespace(id=60, date=now, out=True, message="Скину отчёт завтра")
                    yield SimpleNamespace(id=59, date=now, out=True, message="Ок")
                return run()
        items, _, my_last = asyncio.run(telegram.fetch_chat(Client(), {"id": 1, "name": "Вера", "kind": "личный"}, 1))
        self.assertEqual([(i["kind"], i["source_type"]) for i in items], [("outgoing", "tg_private")])
        self.assertEqual(my_last, 60)


class Promises(unittest.TestCase):
    def test_clear_promises(self):
        for text in ("Скину презентацию завтра утром", "Хорошо, посмотрю к пятнице", "Оплачу сегодня вечером", "напишу до среды"):
            self.assertTrue(promises.is_promise(text), text)

    def test_not_promises(self):
        for text in ("Скинешь завтра?", "Не скину сегодня, прости", "Если успею, сделаю завтра", "Может, посмотрю вечером",
                     "Завтра на пары", "Сделаю", "Посмотрел вчера", "Когда сделаю завтра, скажу"):
            self.assertFalse(promises.is_promise(text), text)

    def test_due_from_words(self):
        tuesday = date(2026, 11, 3)
        self.assertEqual(promises.promise_due("Скину завтра", tuesday), date(2026, 11, 4))
        self.assertEqual(promises.promise_due("Скину вечером", tuesday), tuesday)
        self.assertEqual(promises.promise_due("Сделаю на неделе", tuesday), date(2026, 11, 8))
        self.assertEqual(promises.promise_due("Сделаю на выходных", tuesday), date(2026, 11, 7))

    def add(self, conn, body="Скину отчёт завтра", chat="Вера"):
        db.upsert_item(conn, {"uid": "tg-1-5", "kind": "outgoing", "source_type": "tg_private", "source_name": chat, "title": body, "body": body,
                              "published": db.now_iso(), "meta": {"chat_id": 1, "chat_kind": "личный"}})

    def test_written_once_into_chat_section_without_model(self):
        conn = memory_db()
        self.add(conn)
        with TemporaryDirectory() as folder:
            note = Path(folder) / "Актуальное.md"
            note.write_text("Сделать сейчас\n\n### Прочее\n", "utf-8")
            cfg = {"vault": folder, "obsidian": {"agenda_notes": ["Актуальное.md"]}, "tg_private": [], "deadlines": {"auto_add": True, "max_per_run": 5}}
            with patch.object(agenda, "backup", lambda path: None):
                self.assertEqual(promises.process(conn, cfg), 1)
                self.assertEqual(promises.process(conn, cfg), 0)
            text = note.read_text("utf-8")
        self.assertIn(f"### {agenda.CHAT_SECTION}", text)
        self.assertIn("Обещание: Скину отчёт завтра · Вера", text)

    def test_can_be_switched_off(self):
        conn = memory_db()
        self.add(conn)
        cfg = {"tg_private": [], "deadlines": {"promises": False}}
        self.assertFalse(promises.has_work(conn, cfg))

    def test_outgoing_never_shows_in_chat_views(self):
        conn = memory_db()
        self.add(conn)
        cfg = {"tg_private": [], "modes": DEFAULT_CONFIG["modes"], "active_mode": "all"}
        view = analysis.chat_view(conn, (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(), date.today(), cfg)
        self.assertEqual(view["messages"], 0)
        self.assertEqual(claude_ai.is_public(db.row_to_item(conn.execute("SELECT * FROM items").fetchone())), False)


class Queue(unittest.TestCase):
    TODAY = date(2026, 11, 3)

    def task(self, title, due, heading=""):
        return {"title": title, "due": due, "meta": {"heading": heading}}

    def chat(self, name, reply=0, due=None, action=""):
        return {"name": name, "reply": reply, "due": due, "action": action}

    def test_chat_with_same_deadline_as_task_is_dropped(self):
        rows = analysis.do_queue([self.task("Дедлайн регистрации · HSE Career", "2026-11-05", agenda.CHAT_SECTION)],
                                 [self.chat("Чат HSE Career", due="2026-11-05")], self.TODAY)
        self.assertEqual(len(rows), 1)
        self.assertIn("task", rows[0])

    def test_waiting_person_stays_next_to_a_task(self):
        rows = analysis.do_queue([self.task("Эссе · Поток", "2026-11-05", agenda.CHAT_SECTION)], [self.chat("Поток", reply=1)], self.TODAY)
        self.assertEqual(len(rows), 2)

    def test_my_promise_does_not_hide_a_chat(self):
        rows = analysis.do_queue([self.task("Обещание: Скинуть файл · Вера", "2026-11-05", agenda.CHAT_SECTION)],
                                 [self.chat("Вера", action="Посмотреть договор")], self.TODAY)
        self.assertEqual(len(rows), 2)

    def test_chat_retelling_my_task_is_dropped(self):
        rows = analysis.do_queue([self.task("Диптех 18:30. Подготовить презентации", "2026-11-03")],
                                 [self.chat("Диптех головного мозга 2026 Chat", action="Согласиться на встречу во вторник в 18:30")], self.TODAY)
        self.assertEqual(len(rows), 1)
        rows = analysis.do_queue([self.task("Сдать эссе", "2026-11-05")], [self.chat("турклуб", action="Добавить Машу в команду хакатона")], self.TODAY)
        self.assertEqual(len(rows), 2)

    def test_same_task_twice_is_shown_once(self):
        rows = analysis.do_queue([self.task("Сдать эссе", "2026-11-05"), self.task("сдать эссе", "2026-11-05")], [], self.TODAY)
        self.assertEqual(len(rows), 1)

    def test_order_overdue_then_soon_then_people(self):
        rows = analysis.do_queue([self.task("Потом", "2026-11-08"), self.task("Просрочено", "2026-11-01"), self.task("Завтра", "2026-11-04")],
                                 [self.chat("Вера", reply=1)], self.TODAY)
        self.assertEqual([r.get("task", {}).get("title") or r["chat"]["name"] for r in rows], ["Просрочено", "Завтра", "Вера", "Потом"])

    def test_fresh_news_skip_what_is_already_shown(self):
        now = datetime.now(timezone.utc)
        item = lambda n, hours, cluster: {"id": n, "published": (now - timedelta(hours=hours)).isoformat(), "cluster": cluster}
        stream = [item(1, 1, 10), item(2, 2, 20), item(3, 2, 20), item(4, 8, 40)]
        self.assertEqual([i["id"] for i in analysis.fresh_news(stream, {10}, now)], [2])

    def test_links_open_the_message(self):
        private = {"url": "", "uid": "tg-5-77", "meta": {"chat_id": 5, "chat_kind": "личный"}}
        group = {"url": "", "uid": "tg--105-9", "meta": {"chat_id": -105, "chat_kind": "группа"}}
        public = {"url": "https://t.me/c/1/2", "uid": "tg--1001-2", "meta": {"chat_id": -1001, "chat_kind": "группа"}}
        self.assertEqual(analysis.message_link(private), "tg://openmessage?user_id=5&message_id=77")
        self.assertEqual(analysis.message_link(group), "tg://openmessage?chat_id=105&message_id=9")
        self.assertEqual(analysis.message_link(public), "https://t.me/c/1/2")


class Learning(unittest.TestCase):
    def add_news(self, conn, uid, feedback, topics=("ai",), source="Коммерсантъ"):
        db.upsert_item(conn, {"uid": uid, "kind": "news", "source_type": "rss", "source_name": source, "title": f"Заголовок {uid}", "published": db.now_iso()})
        conn.execute("UPDATE items SET feedback=?, topics=? WHERE uid=?", (feedback, json.dumps(list(topics)), uid))

    def test_topic_marks_lower_similar_news(self):
        conn = memory_db()
        for n in range(3):
            self.add_news(conn, f"u{n}", -1)
        model = analysis.feedback_model(conn)
        self.assertLess(analysis.feedback_score(set(), "Другой", model, ["ai"]), 0)
        self.assertEqual(analysis.feedback_score(set(), "Другой", model, ["neuro"]), 0)

    def test_chat_gets_quiet_after_three_marks_and_only_once(self):
        conn = memory_db()
        cfg = {"tg_private": [{"id": 1, "name": "Шум"}]}
        with patch.object(learning, "save_config", lambda c: None):
            self.assertFalse(learning.note_ignored(conn, cfg, "Шум"))
            self.assertFalse(learning.note_ignored(conn, cfg, "Шум"))
            self.assertTrue(learning.note_ignored(conn, cfg, "Шум"))
            self.assertEqual(cfg["tg_private"][0]["level"], "тише")
            self.assertFalse(learning.note_ignored(conn, cfg, "Шум"))

    def test_chat_set_by_hand_is_not_changed(self):
        conn = memory_db()
        db.kv_set(conn, "learned_chats", {"Важный": 2})
        cfg = {"tg_private": [{"id": 1, "name": "Важный", "level": "всегда"}]}
        self.assertFalse(learning.note_ignored(conn, cfg, "Важный"))
        self.assertEqual(cfg["tg_private"][0]["level"], "всегда")

    def test_view_and_forget(self):
        conn = memory_db()
        self.add_news(conn, "a", -1)
        db.kv_set(conn, "learned_chats", {"Шум": 3})
        cfg = {"tg_private": [{"id": 1, "name": "Шум", "level": "тише"}], "topics": [{"id": "ai", "name": "ИИ"}]}
        seen = learning.view(conn, cfg)
        self.assertEqual(seen["topics"], [{"name": "ИИ", "score": -1}])
        self.assertTrue(seen["chats"][0]["quiet"])
        with patch.object(learning, "load_config", lambda: cfg), patch.object(learning, "save_config", lambda c: None):
            learning.forget(conn)
        self.assertEqual(cfg["tg_private"][0]["level"], "обычно")
        self.assertEqual(learning.view(conn, cfg)["marks"], 0)


class Notifications(unittest.TestCase):
    TODAY = date(2026, 11, 3)
    NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    def view(self, hours=30, kind="личный", level="обычно"):
        since = (self.NOW - timedelta(hours=hours)).isoformat()
        return {"actions": [{"uid": "t1", "title": "Сдать эссе", "due": "2026-11-04"}, {"uid": "t2", "title": "Потом", "due": "2026-11-09"}],
                "chats": {"signals": [{"name": "Вера", "reply": 1, "kind": kind, "level": level, "waiting_since": since}]}}

    def test_tomorrow_and_long_wait(self):
        self.assertEqual([a[1:] for a in notify.alerts(self.view(), self.TODAY, self.NOW)],
                         [("Срок завтра", "Сдать эссе"), ("Ждут ответа больше суток", "Вера")])

    def test_short_wait_is_silent(self):
        self.assertEqual(len(notify.alerts(self.view(hours=5), self.TODAY, self.NOW)), 1)

    def test_group_only_if_marked_important(self):
        self.assertEqual(len(notify.alerts(self.view(kind="группа"), self.TODAY, self.NOW)), 1)
        self.assertEqual(len(notify.alerts(self.view(kind="группа", level="всегда"), self.TODAY, self.NOW)), 2)

    def test_quiet_hours_cross_midnight(self):
        options = {"quiet_from": 23, "quiet_to": 9}
        self.assertTrue(notify.is_quiet(23, options))
        self.assertTrue(notify.is_quiet(3, options))
        self.assertFalse(notify.is_quiet(12, options))

    def test_each_alert_is_shown_once(self):
        conn = memory_db()
        shown = []
        with patch.object(notify, "show", lambda *a: shown.append(a)), patch.object(notify, "is_quiet", lambda *a: False):
            notify.check(conn, {"notify": {"enabled": True}}, self.view(hours=500), self.TODAY)
            notify.check(conn, {"notify": {"enabled": True}}, self.view(hours=500), self.TODAY)
        self.assertEqual(len(shown), 2)

    def test_switched_off(self):
        with patch.object(notify, "show", lambda *a: self.fail("показано")):
            self.assertEqual(notify.check(memory_db(), {"notify": {"enabled": False}}, self.view(), self.TODAY), 0)

    def test_message_text_cannot_break_the_toast(self):
        self.assertNotIn("<script", notify.toast_xml("<script>", "a & b"))


class Autostart(unittest.TestCase):
    def test_file_appears_and_disappears(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "Startup" / "Pulse.vbs"
            with patch.object(autostart, "STARTUP", target):
                self.assertTrue(autostart.set_enabled(True))
                self.assertIn("pulse serve", target.read_text("utf-16"))
                self.assertFalse(autostart.set_enabled(False))


class WeekReview(unittest.TestCase):
    def test_done_log_starts_from_now(self):
        conn = memory_db()
        with patch.object(pipeline, "done_tasks", lambda cfg, limit: [{"title": "Старое"}]):
            pipeline.note_done(conn, {})
        with patch.object(pipeline, "done_tasks", lambda cfg, limit: [{"title": "Старое"}, {"title": "Новое"}]):
            pipeline.note_done(conn, {})
        cfg = {**DEFAULT_CONFIG, "tg_private": []}
        week = analysis.week_view(conn, cfg)
        self.assertEqual(week["done"], ["Новое"])

    def test_overdue_and_waiting_columns(self):
        conn = memory_db()
        past = (date.today() - timedelta(days=2)).isoformat()
        db.upsert_item(conn, {"uid": "t1", "kind": "task", "source_type": "obsidian", "source_name": "Актуальное", "title": "Забытое", "published": db.now_iso(),
                              "due": past, "meta": {"agenda": True, "status": "w"}})
        conn.execute("UPDATE items SET tier='high'")
        week = analysis.week_view(conn, {**DEFAULT_CONFIG, "tg_private": []})
        self.assertEqual((week["overdue_total"], week["waiting_for"]), (1, 1))


class Interests(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        for uid, source, author in (("tgp-1", ".intuition-", ".intuition-"), ("tg-2-1", "Олег Телемский", "Олег Телемский"),
                                    ("tg-3-1", "Поток", "Олег Петров"), ("tg-4-1", "Антон Кузнецов: философия", "Антон Кузнецов: философия")):
            db.upsert_item(self.conn, {"uid": uid, "kind": "post", "source_type": "tg_public", "source_name": source, "title": "т",
                                       "body": "Длинный пост " * 20, "author": author, "published": db.now_iso()})
        self.cfg = {"tg_private": [], "tg_public": [], "interests": {"about": [], "watch": []}, "topics": []}

    def say(self, text):
        with patch.object(interests, "save_config", lambda c: None):
            return interests.command(self.conn, self.cfg, text)

    def test_names_from_a_sentence(self):
        reply = self.say("Мне интересно что о чем пишет Антон Кузнецов, Олег Телемский и .intuition")
        self.assertEqual(self.cfg["interests"]["watch"], ["Антон Кузнецов: философия", "Олег Телемский", ".intuition-"])
        self.assertIn("Слежу за", reply)

    def test_topic_is_remembered_and_forgotten(self):
        self.say("Мне интересна философия сознания")
        self.assertEqual(self.cfg["interests"]["about"], ["философия сознания"])
        self.say("Мне больше не интересна философия сознания")
        self.assertEqual(self.cfg["interests"]["about"], [])

    def test_unwatch(self):
        self.say("Следи за .intuition")
        self.say("Не следи за .intuition")
        self.assertEqual(self.cfg["interests"]["watch"], [])

    def test_question_is_not_a_command(self):
        self.assertIsNone(self.say("Мне интересно, писали ли в чате про экскурсию?"))
        self.assertIsNone(self.say("Что нового в чатах?"))

    def test_shows_what_it_remembers(self):
        self.say("Следи за Олегом Телемским")
        self.assertIn("Олег Телемский", self.say("За чем ты следишь?"))


class Picks(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        self.add("tg-1-1", "Канал А", "Пост про сознание и квалиа. " * 20)
        self.add("tg-2-1", "Канал Б", "Короткое")
        self.add("tg-3-1", "Канал В", "Длинный пост про рецепты борща. " * 20)
        self.cfg = {"tg_private": [{"name": "Канал А", "kind": "канал"}, {"name": "Канал В", "kind": "канал"}], "tg_public": [],
                    "interests": {"about": [], "watch": ["Канал А", "Канал Б"]}, "topics": [{"name": "Сознание", "weight": 1.4}]}

    def add(self, uid, source, body):
        db.upsert_item(self.conn, {"uid": uid, "kind": "message", "source_type": "tg_private", "source_name": source, "title": body[:40],
                                   "body": body, "author": "Автор", "published": db.now_iso(), "meta": {"chat_id": int(uid.split("-")[1]), "chat_kind": "канал"}})

    def test_only_watched_and_long_enough(self):
        self.assertEqual([c["uid"] for c in picks.candidates(self.conn, self.cfg)], ["tg-1-1"])
        self.cfg["interests"]["about"] = ["философия сознания"]
        self.assertEqual(sorted(c["uid"] for c in picks.candidates(self.conn, self.cfg)), ["tg-1-1", "tg-3-1"])

    def test_model_decides_and_learns_from_marks(self):
        self.cfg["interests"]["about"] = ["философия сознания"]
        prompts = []

        def ask(cfg, model, system, user, max_tokens):
            prompts.append(system)
            if "борща" in user:
                return "ИНТЕРЕСНО: нет\nСУТЬ: Рецепт\nПОЧЕМУ: не по теме"
            return "ИНТЕРЕСНО: да\nСУТЬ: Разбор квалиа\nПОЧЕМУ: философия сознания"
        with patch.object(local_ai, "ask", ask):
            self.assertEqual(picks.process(self.conn, self.cfg, {}, "gemma"), 1)
        shown = picks.shown(self.conn)
        self.assertEqual([(p["gist"], p["why"]) for p in shown], [("Разбор квалиа", "философия сознания")])
        self.assertIn("философия сознания", prompts[0])
        picks.rate(self.conn, "tg-1-1", 1)
        self.assertIn("Разбор квалиа", picks.profile_text(self.cfg, self.conn))
        picks.rate(self.conn, "tg-1-1", -1)
        self.assertEqual(picks.shown(self.conn), [])

    def test_without_model_only_watched_with_their_own_words(self):
        self.cfg["interests"]["about"] = ["философия сознания"]
        self.assertEqual(picks.process(self.conn, self.cfg), 1)
        self.assertTrue(picks.shown(self.conn)[0]["gist"].startswith("Пост про сознание"))
        self.assertEqual(len(picks.candidates(self.conn, self.cfg)), 1, "чужой канал ждёт модель")

    def test_broken_model_answer_stops_quietly(self):
        with patch.object(local_ai, "ask", lambda *a, **k: "что-то не то"):
            self.assertEqual(picks.process(self.conn, self.cfg, {}, "gemma"), 0)

    def test_what_is_interesting_comes_from_picks(self):
        picks.remember(self.conn, "tg-1-1", True, "Разбор квалиа", "")
        self.assertTrue(assistant.asks_for_picks("Что интересного?"))
        self.assertFalse(assistant.asks_for_picks("Что интересного в канале .intuition?"))
        self.assertFalse(assistant.asks_for_picks("А что интересного по темам?", [{"role": "user", "text": "Что в канале .intuition?"}]))
        result = assistant.picks_answer(self.conn)
        self.assertIn("Канал А: Разбор квалиа", result["answer"])
        self.assertEqual(result["quotes"][0]["source"], "Канал А")


class AskConversation(unittest.TestCase):
    def setUp(self):
        self.conn = memory_db()
        for n, text in enumerate(("Сходил на съёмки подкаста про тело", "Новый разбор книги о сознании")):
            db.upsert_item(self.conn, {"uid": f"tgp-x-{n}", "kind": "post", "source_type": "tg_public", "source_name": ".intuition-",
                                       "title": text, "body": text, "author": ".intuition-", "published": db.now_iso()})
        db.upsert_item(self.conn, {"uid": "tg-5-1", "kind": "message", "source_type": "tg_private", "source_name": "Быт",
                                   "title": "Кастрюля", "body": "Кто забыл кастрюлю", "published": db.now_iso()})

    def test_follow_up_keeps_the_channel(self):
        history = [{"role": "user", "text": "Что есть интересного в канале .intuition?"}, {"role": "bot", "text": "Таро"}]
        context, sources, _ = assistant.gather(self.conn, "А конкретно что интересного по темам?", None, history)
        self.assertIn("разбор книги", context)
        self.assertNotIn("кастрюлю", context)
        self.assertEqual(sources, [".intuition-"])

    def test_yes_no_rule_only_for_yes_no_questions(self):
        self.assertTrue(assistant.YES_NO.search("Писали ли в чате про экскурсию?"))
        self.assertFalse(assistant.YES_NO.search("Что интересного по темам?"))

    def test_channel_author_is_not_repeated_in_quote(self):
        row = self.conn.execute("SELECT * FROM items WHERE uid='tgp-x-0'").fetchone()
        self.assertEqual(assistant.quote(row)["author"], "")


class ArticleSummaries(unittest.TestCase):
    PAGE = """<html><body><nav><p>Меню сайта с длинным текстом, который не должен попасть в саммари статьи никогда</p></nav>
      <article><p>Учёные из пяти лабораторий проверили влияние псилоцибина на поведение мышей в одинаковых условиях опыта.</p>
      <p>Коротко</p><p>Эффект сохранялся только в первые сутки, а более долгие изменения оказались слабыми и непостоянными.</p></article>
      <footer><p>Подвал сайта с длинным текстом, который тоже не должен попасть в саммари статьи никогда</p></footer>
      <script>var x = "код страницы";</script></body></html>"""

    def test_extract_keeps_article_only(self):
        text = summaries.extract_text(self.PAGE)
        self.assertIn("Эффект сохранялся", text)
        for junk in ("Меню", "Подвал", "код страницы", "Коротко"):
            self.assertNotIn(junk, text)

    def test_scientific_abstract_wins_over_site_boilerplate(self):
        page = """<html><head><meta name="citation_abstract" content="Мы показали, что внимание зависит от ритма мозга в опыте на людях и животных."></head>
          <body><p>arXivLabs is a framework that allows collaborators to develop and share new tools</p></body></html>"""
        text = summaries.extract_text(page)
        self.assertTrue(text.startswith("Мы показали"))
        self.assertNotIn("arXivLabs", text)

    def test_abstract_in_blockquote_is_read(self):
        page = '<html><body><blockquote class="abstract">Abstract: We audit a system claiming twenty five cognitive mechanisms in total.</blockquote></body></html>'
        self.assertIn("We audit", summaries.extract_text(page))

    def test_google_link_is_replaced_by_the_original(self):
        rss = """<rss><channel><item><title>New consciousness theory explained today</title>
          <link>https://www.bing.com/news/apiclick.aspx?url=https%3a%2f%2fexample.com%2fstory&amp;c=1</link></item></channel></rss>""".encode("utf-8")
        with patch.object(feeds, "fetch", lambda url: rss):
            self.assertEqual(summaries.find_original("New consciousness theory explained today"), "https://example.com/story")
            self.assertEqual(summaries.find_original("Совершенно другой заголовок про другое"), "")

    def test_parse_model_answer(self):
        result = summaries.parse_summary("СУТЬ: Главное.\nЗАПОМНИТЬ:\n- Первое\n* Второе\n3) Третье\nОГОВОРКИ: Только мыши.")
        self.assertEqual(result, {"gist": "Главное.", "points": ["Первое", "Второе", "Третье"], "limits": "Только мыши"})

    def test_no_limits_word_means_empty(self):
        self.assertEqual(summaries.parse_summary("СУТЬ: А\nЗАПОМНИТЬ:\n- Б\nОГОВОРКИ: нет")["limits"], "")

    def test_internal_addresses_are_refused(self):
        for url in ("http://127.0.0.1:8765/api/today", "http://localhost/x", "http://192.168.1.1/", "file:///C:/x", "obsidian://x", ""):
            self.assertFalse(summaries.is_public_url(url), url)

    def test_extract_summary_without_model(self):
        text = ("Пять лабораторий проверили псилоцибин на мышах и получили один результат. "
                "Эффект держался 24 часа и затем исчез без следа у всех животных опыта. "
                "Авторы считают, что долгие эффекты псилоцибина раньше были переоценены.")
        result = summaries.extract_summary("Псилоцибин и мыши", text)
        self.assertTrue(result["gist"])
        self.assertGreaterEqual(len(result["points"]), 2)

    def test_uses_feed_text_and_says_so_when_page_is_closed(self):
        item = {"kind": "news", "url": "https://x/1", "title": "Заголовок", "body": "Анонс статьи, в котором есть одно достаточно длинное предложение о сути работы. " * 5}
        cfg = {"local_ai": {"url": "http://127.0.0.1:9/v1"}}
        with patch.object(summaries, "fetch_article", lambda url, title: ""), patch.object(local_ai.setup_model, "installed", lambda: False):
            result = summaries.build(item, cfg)
        self.assertEqual((result["basis"], result["method"]), ("feed", "extract"))

    def test_title_only_is_reported_honestly(self):
        item = {"kind": "news", "url": "", "title": "Заголовок", "body": ""}
        with patch.object(summaries, "fetch_article", lambda url, title: ""):
            self.assertEqual(summaries.build(item, {})["method"], "none")

    def test_chat_messages_have_no_summary(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        db.upsert_item(conn, {"uid": "m", "kind": "message", "source_type": "tg_private", "source_name": "c",
                              "title": "т", "published": db.now_iso()})
        with self.assertRaises(ValueError):
            summaries.load_item(conn, 1)


class Connector(unittest.TestCase):
    def test_handshake_and_tools(self):
        reply = mcp_server.handle({"method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(reply["serverInfo"]["name"], "pulse")
        allowed = {"privacy": {"claude_reads_chats": True}}
        with patch.object(mcp_server, "load_config", lambda: allowed):
            names = {t["name"] for t in mcp_server.handle({"method": "tools/list"})["tools"]}
        self.assertEqual(names, {"pulse_today", "tg_list_chats", "tg_read_messages", "tg_search"})


if __name__ == "__main__":
    unittest.main()
