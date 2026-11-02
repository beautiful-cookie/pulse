"""Новости: RSS/Atom и публичные каналы Telegram (страница t.me/s, вход не нужен)."""
import hashlib
import re
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import db
from .text import first_line_title, keyword_regex, strip_html

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Pulse/1.0"
TIMEOUT = 20
SEARCH_LIMIT = 15


def ssl_context():
    """Системные сертификаты плюс certifi: для части сайтов на этой машине нужен второй набор."""
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except ImportError:
        pass
    return context


CONTEXT = ssl_context()


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    for attempt in (1, 2):  # соединения иногда обрываются, второй попытки обычно хватает
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT, context=CONTEXT) as response:
                return response.read(6_000_000)
        except urllib.error.URLError:
            if attempt == 2:
                raise


def local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def child_text(node, *names):
    for child in node:
        if local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_feed(raw):
    """Разбирает RSS 2.0, RDF и Atom единым образом."""
    root = ET.fromstring(raw)
    entries = []
    for node in root.iter():
        if local(node.tag) not in ("item", "entry"):
            continue
        link = child_text(node, "link")
        publisher = ""
        for child in node:
            name = local(child.tag)
            if name == "link" and child.get("href") and child.get("rel", "alternate") == "alternate":
                link = child.get("href")
            if name == "source" and (child.text or "").strip():
                publisher = child.text.strip()
        title = strip_html(child_text(node, "title")).split(" // ")[0]
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3].strip()
        body = strip_html(child_text(node, "encoded", "description", "summary", "content"))
        if body.startswith(title[:40]) and len(body) < len(title) + 60:
            body = ""
        entries.append({
            "title": title, "body": body[:1500], "url": link or node.get("about", ""),
            "author": publisher or child_text(node, "creator", "author"),
            "date": parse_date(child_text(node, "pubdate", "published", "updated", "date")),
        })
    return entries


def compile_topics(cfg):
    compiled = []
    for topic in cfg["topics"]:
        compiled.append({
            **topic,
            "rx": [keyword_regex(k) for k in topic["keywords"]],
            "ex": [keyword_regex(k) for k in topic.get("exclude", [])],
        })
    return compiled


def topic_hits(topic, title, body):
    """Сколько ключевых слов темы в заголовке и в тексте, с учётом исключений."""
    text = title + " " + body
    if any(r.search(text) for r in topic["ex"]):
        return 0, 0
    return sum(1 for r in topic["rx"] if r.search(title)), sum(1 for r in topic["rx"] if r.search(body))


def is_on_topic(topic, title, body):
    """Одно слово где-то в тексте темой не считается: нужен заголовок или два разных слова."""
    in_title, in_body = topic_hits(topic, title, body)
    return in_title > 0 or in_body >= 2


def matches_any(title, body, topics):
    return [t["id"] for t in topics if is_on_topic(t, title, body)]


def uid_of(url, title):
    return hashlib.sha1((url or title).encode("utf-8")).hexdigest()[:20]


def collect_feed(feed, topics, window_days):
    """Сетевой шаг без записи в базу, выполняется параллельно."""
    raw = fetch(feed["url"])
    entries = parse_feed(raw)
    if "news.google.com" in feed["url"]:
        entries = entries[:SEARCH_LIMIT]  # поиск не должен вытеснять первоисточники
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    items = []
    for entry in entries:
        if not entry["title"]:
            continue
        published = entry["date"] or datetime.now(timezone.utc)
        if published < cutoff:
            continue
        hits = matches_any(entry["title"], entry["body"], topics)
        if feed.get("only_topics"):
            hits = [h for h in hits if h in feed["only_topics"]]
        if "news.google.com" in feed["url"] and feed.get("topic") not in hits:
            continue  # поиск часто приносит постороннее, поэтому тема должна подтвердиться
        if feed.get("filter") and not hits:
            continue
        items.append({
            "uid": uid_of(entry["url"], entry["title"]), "kind": "news", "source_type": "rss",
            "source_name": feed["name"], "title": entry["title"], "body": entry["body"], "url": entry["url"],
            "author": entry["author"], "published": published.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "meta": {"primary": bool(feed.get("primary")), "feed_topic": feed.get("topic"),
                     "search": "news.google.com" in feed["url"]},
        })
    return items


def parse_tg_page(raw, username):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, "html.parser")
    posts = []
    for wrap in soup.select(".tgme_widget_message"):
        post = wrap.get("data-post", "")
        text_node = wrap.select_one(".tgme_widget_message_text")
        time_node = wrap.select_one("time[datetime]")
        if not post or text_node is None or time_node is None:
            continue
        for br in text_node.find_all("br"):
            br.replace_with("\n")
        posts.append({
            "id": post.rsplit("/", 1)[-1], "text": text_node.get_text().strip(),
            "date": parse_date(time_node["datetime"]), "url": f"https://t.me/{post}",
        })
    return posts


def collect_tg_channel(channel, window_days):
    username = channel["username"].lstrip("@").strip()
    raw = fetch(f"https://t.me/s/{username}")
    if b"tgme_channel_info" not in raw and b"tgme_widget_message" not in raw:
        raise ValueError("канал не найден или закрыт для предпросмотра")
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    items = []
    for post in parse_tg_page(raw, username):
        if not post["text"] or (post["date"] and post["date"] < cutoff):
            continue
        items.append({
            "uid": f"tgp-{username}-{post['id']}", "kind": "post", "source_type": "tg_public",
            "source_name": channel.get("name") or username, "title": first_line_title(post["text"]),
            "body": post["text"][:1500], "url": post["url"],
            "published": (post["date"] or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds"),
            "meta": {"primary": False},
        })
    return items


def collect(conn, cfg):
    topics = compile_topics(cfg)
    window = cfg["news_window_days"]
    jobs = [("rss:" + f["url"], f["name"], "rss", lambda f=f: collect_feed(f, topics, window)) for f in cfg["feeds"] if f.get("enabled", True)]
    jobs += [("tg:" + c["username"], c.get("name") or c["username"], "tg_public", lambda c=c: collect_tg_channel(c, window)) for c in cfg["tg_public"]]

    def run(job):
        key, name, type_, call = job
        try:
            return key, name, type_, call(), None
        except Exception as error:  # один сломанный источник не должен останавливать остальные
            return key, name, type_, [], f"{type(error).__name__}: {str(error)[:160]}"

    total = 0
    with ThreadPoolExecutor(8) as pool:
        for key, name, type_, items, error in pool.map(run, jobs):
            for item in items:
                db.upsert_item(conn, item)
            db.record_source(conn, key, name, type_, count=len(items), error=error)
            conn.commit()  # не держим блокировку базы, пока ждём следующий источник
            total += len(items)
    keys = [job[0] for job in jobs]
    marks = ",".join("?" * len(keys))  # источник убрали из настроек: убираем и из статистики
    conn.execute(f"DELETE FROM sources WHERE type IN ('rss','tg_public') AND key NOT IN ({marks})", keys)
    return total
