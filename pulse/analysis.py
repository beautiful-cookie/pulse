"""Что важно и почему. Оценка складывается из понятных слагаемых, и каждое
из них становится видимой причиной в карточке: чёрного ящика нет.

Правила перенесены из методики новостей: только дельта, первоисточник важнее
агрегатора, сцепка с заметками, чёрный список.
"""
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from . import agenda, db, replies
from .config import DEFAULT_LEVEL, LEVELS, active_mode, chat_levels
from .feeds import compile_topics, is_on_topic, topic_hits
from .promises import PREFIX
from .text import extract_due, keyword_regex, stems

ANALYSIS_DAYS = 21
FRESH_HOURS = 6
FRESH_SHOWN = 3
WEEK_SHOWN = 6
ACTION_HOURS = 24
GROUP_REPLY_HOURS = 24
PERSONAL_REPLY_HOURS = 72
HIGH, MID, LOW = 55, 32, 14
HALF_LIFE_HOURS = 40
CLUSTER_SIMILARITY = 0.36
CLUSTER_WINDOW_DAYS = 4
LINK_ZONES = ("30 Ресурсы", "10 Проекты", "20 Области")
LINK_EXCLUDE = re.compile(r"Учеба/|Новости/|Тайм-менеджмент/|Навигация|Обзор|MOC|Без названия|Untitled")


def build_note_index(conn):
    """Заметки, на которые можно сослаться: понятия и проекты, а не лекции."""
    index = []
    for row in conn.execute("SELECT path, title FROM notes").fetchall():
        if row["path"].split("/")[0] not in LINK_ZONES or LINK_EXCLUDE.search(row["path"]):
            continue
        clean = re.sub(r"\(.*?\)|§[\d.]+|\d{4}", " ", row["title"])
        words = stems(clean)
        if not words or len(words) > 5:
            continue
        if len(words) == 1 and len(words[0]) < 8:
            continue
        index.append((set(words), row["title"], row["path"]))
    return index


def link_notes(words, index):
    found = [(len(required), title) for required, title, _ in index if required <= words]
    return [title for _, title in sorted(found, reverse=True)[:3]]


def recent_activity(conn, topics, days=14):
    """Над чем вы работаете: темы недавно изменённых заметок поднимают соседние новости."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    counts = Counter()
    for row in conn.execute("SELECT path, title, tags FROM notes WHERE mtime>=?", (since,)).fetchall():
        text = row["path"] + " " + " ".join(json.loads(row["tags"]))
        for topic in topics:
            if any(r.search(text) for r in topic["rx"]):
                counts[topic["id"]] += 1
    return counts


def feedback_model(conn):
    """Оценки «важно» и «не важно» превращаются в веса слов, источников и тем."""
    words, sources, topics = defaultdict(float), defaultdict(float), defaultdict(float)
    rows = conn.execute("SELECT title, source_name, feedback, topics FROM items WHERE feedback!=0").fetchall()
    for row in rows:
        sources[row["source_name"]] += row["feedback"]
        for word in set(stems(row["title"])):
            words[word] += row["feedback"]
        for topic in json.loads(row["topics"] or "[]"):
            topics[topic] += row["feedback"]
    return words, sources, topics


def feedback_score(item_words, source, model, item_topics=()):
    words, sources, topics = model
    if not words and not sources:
        return 0.0
    word_part = sum(max(-3, min(3, words.get(w, 0))) for w in item_words) * 1.5
    source_part = max(-3, min(3, sources.get(source, 0))) * 3
    topic_part = max(-3, min(3, sum(topics.get(t, 0) for t in item_topics))) * 2
    return max(-20.0, min(20.0, word_part + source_part + topic_part))


def assign_clusters(items):
    """Один сюжет в разных источниках склеивается по сходству заголовков."""
    parent = {it["id"]: it["id"] for it in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    news = [it for it in items if it["kind"] in ("news", "post")]
    for it in news:
        it["_title_words"] = set(stems(it["title"]))
    news.sort(key=lambda it: it["published"])
    for i, a in enumerate(news):
        for b in news[i + 1:]:
            if (_day(b) - _day(a)).days > CLUSTER_WINDOW_DAYS:
                break
            if len(a["_title_words"]) < 3 or len(b["_title_words"]) < 3:
                continue
            inter = len(a["_title_words"] & b["_title_words"])
            if inter >= 3 and inter / len(a["_title_words"] | b["_title_words"]) >= CLUSTER_SIMILARITY:
                parent[find(a["id"])] = find(b["id"])
    groups = defaultdict(list)
    for it in items:
        groups[find(it["id"])].append(it)
    for root, members in groups.items():
        sources = {m["source_name"] for m in members}
        for member in members:
            member["cluster"] = root
            member["cluster_size"] = len(sources)


def _day(item):
    return datetime.fromisoformat(item["published"])


def score_item(item, ctx):
    """Возвращает (оценка, причины, темы, ссылки). Причины показываются в интерфейсе."""
    text = item["title"] + " " + item["body"]
    reasons = []
    score = 0.0
    meta = item["meta"]

    if any(rx.search(text) for rx in ctx["blacklist"]):
        return 0.0, ["чёрный список"], [], []

    topics = []
    best = 0.0
    for topic in ctx["topics"]:
        title_hits, body_hits = topic_hits(topic, item["title"], item["body"])
        on_topic = is_on_topic(topic, item["title"], item["body"])
        strength = min(1.0, (title_hits * 2 + body_hits) / 3) if on_topic else 0.0
        if meta.get("feed_topic") == topic["id"] and (title_hits or body_hits or not meta.get("search")):
            strength = max(strength, 0.6)
        if strength > 0:
            topics.append(topic["id"])
            value = strength * topic["weight"] * ctx["boost"].get(topic["id"], 1.0) * 30
            boost = topic.get("activity_boost", True)
            activity = min(ctx["activity"].get(topic["id"], 0), 12) / 12 * 8 if boost else 0
            best = max(best, value + activity)
            if activity >= 3 and "вы сейчас над этим работаете" not in reasons:
                reasons.append("вы сейчас над этим работаете")
    score += best

    if meta.get("primary"):
        score += 8
        reasons.append("первоисточник")

    size = item.get("cluster_size", 1)
    if size >= 2:
        score += 7 * min(size - 1, 3)
        reasons.append(f"пишут {size} источника" if size < 5 else f"пишут {size} источников")

    words = set(stems(text))
    links = link_notes(words, ctx["notes"]) if item["kind"] in ("news", "post") else []
    if links:
        score += 12
        reasons.append("связано с вашими заметками")

    if item["kind"] == "message":
        kind = meta.get("chat_kind")
        if meta.get("mentioned"):
            score += 60
            reasons.append("вас упомянули или ответили вам")
        elif kind == "личный":
            score += 40
            reasons.append("личное сообщение")
        if any(rx.search(text) for rx in ctx["me"]):
            score += 30
            reasons.append("названо ваше имя")
        if any(rx.search(text) for rx in ctx["urgent"]):
            score += 20
            reasons.append("похоже на срок или изменение")

    score *= LEVELS.get(ctx["levels"].get(item["source_name"], DEFAULT_LEVEL), 1.0)

    if item["kind"] in ("news", "post", "message"):
        age_hours = max(0.0, (ctx["now"] - _day(item)).total_seconds() / 3600)
        score *= 0.5 ** (age_hours / HALF_LIFE_HOURS)

    learned = feedback_score(set(stems(item["title"])), item["source_name"], ctx["model"], topics)
    if abs(learned) >= 3:
        score += learned
        reasons.append("вы отмечали похожее как важное" if learned > 0 else "вы отмечали похожее как неважное")

    return round(score, 1), reasons, topics, links


def message_due(item, ctx):
    """Срок из сообщения считаем только рядом со словом вроде «дедлайн» или «экзамен»."""
    text = item["title"] + " " + item["body"]
    if not any(rx.search(text) for rx in ctx["urgent"]):
        return None
    due, _ = extract_due(text, _day(item).astimezone().date())
    return due.isoformat() if due else None


def tier_of(score):
    return "high" if score >= HIGH else "mid" if score >= MID else "low" if score >= LOW else "noise"


def score_task(item, today):
    """Дела ранжируются отдельно: срок важнее всего остального."""
    meta = item["meta"]
    if meta.get("stale"):
        return 5.0, "stale", [meta["stale"]]
    reasons = []
    score = 20.0
    if item["due"]:
        days = (date.fromisoformat(item["due"]) - today).days
        if days < 0:
            score, label = 95, f"просрочено на {-days} дн."
        elif days == 0:
            score, label = 90, "срок сегодня"
        elif days == 1:
            score, label = 80, "срок завтра"
        elif days <= 7:
            score, label = 65 - days, f"срок через {days} дн."
        else:
            score, label = 35, f"срок через {days} дн."
        reasons.append(label)
    if meta.get("agenda"):
        score += 8
        reasons.append("из «Актуального»")
    return score, ("high" if score >= 60 else "mid"), reasons


def run(conn, cfg):
    now = datetime.now(timezone.utc)
    today = date.today()
    topics = compile_topics(cfg)
    ctx = {
        "now": now, "topics": topics, "notes": build_note_index(conn),
        "boost": active_mode(cfg)["topics"], "levels": chat_levels(cfg),
        "activity": recent_activity(conn, topics), "model": feedback_model(conn),
        "blacklist": [keyword_regex(b) for b in cfg["blacklist"]],
        "me": [keyword_regex(n) for n in cfg["me"]["names"]],
        "urgent": [re.compile(p, re.I) for p in cfg["urgent_terms"]],
    }
    since = (now - timedelta(days=ANALYSIS_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT * FROM items WHERE state!='gone' AND (published>=? OR kind IN ('task','review'))", (since,)
    ).fetchall()
    items = [db.row_to_item(r) for r in rows]
    stream = [it for it in items if it["kind"] in ("news", "post", "message")]
    assign_clusters(stream)
    for item in stream:
        score, reasons, topic_ids, links = score_item(item, ctx)
        due = message_due(item, ctx) if item["kind"] == "message" else None
        conn.execute(
            "UPDATE items SET score=?, tier=?, reasons=?, topics=?, links=?, cluster=?, cluster_size=?, due=? WHERE id=?",
            (score, tier_of(score), json.dumps(reasons, ensure_ascii=False), json.dumps(topic_ids),
             json.dumps(links, ensure_ascii=False), item.get("cluster"), item.get("cluster_size", 1), due, item["id"]),
        )
    for item in items:
        if item["kind"] == "task":
            score, tier, reasons = score_task(item, today)
        elif item["kind"] == "review":
            overdue = (today - date.fromisoformat(item["due"])).days
            score, tier = 30.0, "mid"
            reasons = [f"просрочено на {overdue} дн."] if overdue > 0 else (["на сегодня"] if overdue == 0 else [f"через {-overdue} дн."])
        else:
            continue
        conn.execute("UPDATE items SET score=?, tier=?, reasons=? WHERE id=?",
                     (score, tier, json.dumps(reasons, ensure_ascii=False), item["id"]))
    return len(items)


def rescore_tasks(conn):
    """Быстрый пересчёт только дел: нужен сразу после правки из приложения."""
    today = date.today()
    for row in conn.execute("SELECT * FROM items WHERE kind='task' AND state!='gone'").fetchall():
        item = db.row_to_item(row)
        score, tier, reasons = score_task(item, today)
        conn.execute("UPDATE items SET score=?, tier=?, reasons=? WHERE id=?",
                     (score, tier, json.dumps(reasons, ensure_ascii=False), item["id"]))


def tasks_view(conn, cfg):
    """Все открытые дела. Менять можно только строки из «Актуального»."""
    rows = [db.row_to_item(r) for r in conn.execute(
        "SELECT * FROM items WHERE kind='task' AND state='new' ORDER BY (due IS NULL), due, score DESC").fetchall()]
    agenda_notes = set(cfg["obsidian"]["agenda_notes"])
    for task in rows:
        task["editable"] = task["meta"].get("path") in agenda_notes
    open_tasks = [t for t in rows if t["tier"] != "stale"]
    return {
        "tasks": [t for t in open_tasks if t["editable"] or t["due"]],
        "stale": [t for t in rows if t["tier"] == "stale" and t["editable"]],
        "others": sum(1 for t in rows if not t["editable"] and not t["due"]),
        "done": agenda.done_tasks(cfg),
    }


def select_main(items, budget, topic_cap):
    """Бюджет внимания: один пункт на сюжет и не больше нескольких на тему."""
    chosen, seen_clusters, per_topic = [], set(), Counter()
    for item in items:
        if item["cluster"] in seen_clusters:
            continue
        topic = item["topics"][0] if item["topics"] else None
        if topic and per_topic[topic] >= topic_cap:
            continue
        chosen.append(item)
        seen_clusters.add(item["cluster"])
        if topic:
            per_topic[topic] += 1
        if len(chosen) >= budget:
            break
    return chosen


def needs_reply(item, my_last, checks=None):
    """Вас упомянули или написали лично, вы ещё не отвечали, и сообщение правда ждёт ответа."""
    meta = item["meta"]
    if not (meta.get("mentioned") or meta.get("chat_kind") == "личный"):
        return False
    if int(item["uid"].rsplit("-", 1)[1]) <= my_last.get(str(meta.get("chat_id")), 0):
        return False
    if is_outdated(item):
        return False
    return replies.needs_answer(item, checks or {})


def is_outdated(item):
    """Вчерашняя рассылка и встреча, которая уже прошла, ответа не ждут."""
    published = item.get("published")
    if not published:
        return False
    moment = datetime.fromisoformat(published)
    limit = PERSONAL_REPLY_HOURS if item["meta"].get("chat_kind") == "личный" else GROUP_REPLY_HOURS
    if datetime.now(timezone.utc) - moment > timedelta(hours=limit):
        return True
    due, _ = extract_due(item.get("body") or "", moment.astimezone().date())
    return bool(due and due < date.today())


def live_action(chat, digest, answered, now):
    """Дело из разбора живо, пока вы не ответили позже него и оно не устарело."""
    if not digest or not digest["action"]:
        return ""
    if now - datetime.fromisoformat(chat["last"]) > timedelta(hours=ACTION_HOURS):
        return ""
    tail = digest["last_uid"].rsplit("-", 1)[-1]
    if tail.isdigit() and int(tail) <= answered.get(str(chat["chat_id"]), 0):
        return ""
    return digest["action"]


def message_link(item):
    """Ссылка на сообщение. У личных чатов и обычных групп нет адреса в интернете."""
    if item["url"]:
        return item["url"]
    chat_id = item["meta"].get("chat_id")
    message_id = item["uid"].rsplit("-", 1)[1]
    if item["meta"].get("chat_kind") in ("личный", "бот"):
        return f"tg://openmessage?user_id={chat_id}&message_id={message_id}"
    return f"tg://openmessage?chat_id={str(chat_id).lstrip('-')}&message_id={message_id}"


def dismiss_chat(conn, name):
    """«Ответ не нужен»: всё, что уже пришло в этот чат, больше не считается ждущим."""
    rows = conn.execute("SELECT uid, meta FROM items WHERE kind='message' AND source_name=?", (name,)).fetchall()
    if not rows:
        raise ValueError("Такого чата нет")
    dismissed = db.kv_get(conn, "reply_dismissed", {})
    chat_id = str(json.loads(rows[0]["meta"]).get("chat_id"))
    dismissed[chat_id] = max(int(r["uid"].rsplit("-", 1)[1]) for r in rows)
    db.kv_set(conn, "reply_dismissed", dismissed)


def chat_view(conn, since, today, cfg):
    """Чаты сведены к сигналам: кто ждёт ответа и какие появились сроки.

    Важность чата решает, что считать сигналом: «всегда» показывает любое новое
    сообщение, «тише» только обращения к вам, «никогда» скрывает чат совсем.
    """
    levels = chat_levels(cfg)
    only_important = active_mode(cfg)["chats"] == "important"
    my_last = db.answered_ids(conn)
    now = datetime.now(timezone.utc)
    checks = replies.load_checks(conn)
    messages = [db.row_to_item(r) for r in conn.execute(
        "SELECT * FROM items WHERE kind='message' AND state='new' AND published>=? ORDER BY published DESC", (since,)).fetchall()]
    digests = {r["chat"]: dict(r) for r in conn.execute("SELECT * FROM chat_digests").fetchall()}
    chats = {}
    for item in messages:
        level = levels.get(item["source_name"], DEFAULT_LEVEL)
        if level == "никогда" or (only_important and level != "всегда"):
            continue
        chat = chats.setdefault(item["source_name"], {
            "name": item["source_name"], "kind": item["meta"].get("chat_kind", "группа"), "count": 0, "reply": 0, "last": item["published"],
            "due": None, "preview": None, "author": None, "url": item["url"], "id": item["id"], "ids": [], "level": level,
            "chat_id": item["meta"].get("chat_id"), "link": "", "waiting_since": None,
        })
        chat["count"] += 1
        wants_reply = needs_reply(item, my_last, checks)
        has_due = level != "тише" and bool(item["due"] and item["due"] >= today.isoformat())
        if wants_reply:
            chat["reply"] += 1
            chat["waiting_since"] = item["published"]  # сообщения идут от новых к старым
        if has_due and (not chat["due"] or item["due"] < chat["due"]):
            chat["due"] = item["due"]
        if (wants_reply or has_due) and not chat["preview"]:
            chat.update(preview=item["title"], author=item["author"], url=item["url"], id=item["id"], link=message_link(item))
        if wants_reply or has_due:
            chat["ids"].append(item["id"])
    for chat in chats.values():
        digest = digests.get(chat["name"])
        chat["summary"] = digest["summary"] if digest else None
        chat["action"] = live_action(chat, digest, my_last, now)
    for chat in chats.values():
        if chat["level"] == "всегда" and not chat["preview"]:
            newest = next(m for m in messages if m["source_name"] == chat["name"])
            chat.update(preview=newest["title"], author=newest["author"], url=newest["url"], id=newest["id"], link=message_link(newest))
    wanted = lambda c: c["reply"] or c["due"] or (c["level"] != "тише" and c["action"]) or c["level"] == "всегда"
    signals = sorted((c for c in chats.values() if wanted(c)),
                     key=lambda c: (c["level"] != "всегда", -c["reply"], c["due"] or "9999", c["name"]))
    quiet = [c for c in chats.values() if c not in signals]
    quiet.sort(key=lambda c: -c["count"])
    live = [{"chat": c["name"], "summary": c["summary"], "action": c["action"]} for c in signals if c["action"]]
    return {"signals": signals, "quiet": quiet, "messages": len(messages), "live": live}


def is_promise_task(task):
    return task["title"].startswith(PREFIX)


def do_queue(actions, signals, today):
    """Одна очередь «Сделать»: дела и чаты, каждое дело один раз."""
    def rank(due):
        days = (date.fromisoformat(due) - today).days
        return 0 if days < 0 else 1 if days <= 1 else 3 if days <= 7 else 4

    from_chats = [t for t in actions if t["meta"].get("heading") == agenda.CHAT_SECTION and not is_promise_task(t)]

    def repeats_task(chat):
        same_chat = any(chat["name"] in t["title"] for t in from_chats)
        return same_chat or any(t["due"] == chat["due"] for t in from_chats if chat["due"])

    rows, seen = [], set()
    for task in actions:
        words = frozenset(stems(task["title"]))
        if words and words in seen:
            continue
        seen.add(words)
        rows.append({"task": task, "rank": rank(task["due"]), "due": task["due"]})
    for chat in signals:
        if chat["reply"] or ((chat["due"] or chat["action"]) and not repeats_task(chat)):
            rows.append({"chat": chat, "rank": 2 if chat["reply"] else rank(chat["due"]) if chat["due"] else 3, "due": chat["due"]})
    return sorted(rows, key=lambda r: (r["rank"], r["due"] or "9999"))


def fresh_news(stream, shown_clusters, now):
    """Только что вышедшее: рейтинг меняется медленно, и без этого новое не видно часами."""
    since = (now - timedelta(hours=FRESH_HOURS)).isoformat()
    found, seen = [], set(shown_clusters)
    for item in sorted(stream, key=lambda i: i["published"], reverse=True):
        if item["published"] >= since and item["cluster"] not in seen:
            seen.add(item["cluster"])
            found.append(item)
    return found[:FRESH_SHOWN]


def today_view(conn, cfg):
    now = datetime.now(timezone.utc)
    today = date.today()
    names = {t["id"]: t["name"] for t in cfg["topics"]}

    since = (now - timedelta(hours=72)).isoformat()
    stream = [db.row_to_item(r) for r in conn.execute(
        """SELECT * FROM items WHERE kind IN ('news','post') AND state='new' AND published>=?
           AND tier IN ('high','mid','low') AND feedback>=0 ORDER BY score DESC""", (since,)).fetchall()]
    mode = active_mode(cfg)
    main = select_main([i for i in stream if i["tier"] in ("high", "mid")], mode["budget"], cfg["topic_cap"])
    main_ids = {i["id"] for i in main}
    main_clusters = {i["cluster"] for i in main}

    by_topic = defaultdict(list)
    seen = set(main_clusters)
    for item in stream:
        if item["id"] in main_ids or item["cluster"] in seen:
            continue
        seen.add(item["cluster"])
        by_topic[item["topics"][0] if item["topics"] else "other"].append(item)
    rest = [{"id": t["id"], "name": t["name"], "items": by_topic[t["id"]][:5], "total": len(by_topic[t["id"]])}
            for t in cfg["topics"] if by_topic.get(t["id"])]

    tasks = [db.row_to_item(r) for r in conn.execute(
        "SELECT * FROM items WHERE kind='task' AND state='new' ORDER BY score DESC, due").fetchall()]
    actions = [t for t in tasks if t["tier"] != "stale" and t["due"]]
    agenda = [t for t in tasks if t["tier"] != "stale" and not t["due"] and t["meta"].get("agenda")]
    stale = [t for t in tasks if t["tier"] == "stale"]

    reviews = [db.row_to_item(r) for r in conn.execute(
        "SELECT * FROM items WHERE kind='review' AND state='new' AND due<=? ORDER BY due", (today.isoformat(),)).fetchall()]

    chats = chat_view(conn, since, today, cfg)
    failing = [dict(r) for r in conn.execute(
        "SELECT name, last_error FROM sources WHERE last_error IS NOT NULL AND type!='tg_private'").fetchall()]
    total_sources = conn.execute("SELECT COUNT(*) c FROM sources WHERE type!='tg_private'").fetchone()["c"]
    counts = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE kind IN ('news','post') AND published>=?", (since,)
    ).fetchone()["c"]
    inbox = conn.execute("SELECT COUNT(*) c FROM notes WHERE path LIKE '00 Система/Инбокс/%'").fetchone()["c"]
    today_iso, week_iso = today.isoformat(), (today + timedelta(days=7)).isoformat()
    topics = [{"id": t["id"], "name": t["name"], "count": sum(t["id"] in i["topics"] for i in main) + by_topic_total(rest, t["id"])}
              for t in cfg["topics"]]

    return {
        "topic_names": names, "main": main, "fresh": fresh_news(stream, main_clusters, now),
        "queue": do_queue(actions, chats["signals"], today), "rest": rest, "actions": actions[:10],
        "actions_total": len(actions), "agenda": agenda[:6], "stale": stale[:30], "stale_total": len(stale),
        "reviews": reviews[:4], "reviews_total": len(reviews), "chats": chats, "failing": failing,
        "topics": [t for t in topics if t["count"]],
        "modes": [{"id": m["id"], "name": m["name"]} for m in cfg["modes"]], "active_mode": mode["id"],
        "chat_levels": chat_levels(cfg),
        "tiles": {
            "overdue": sum(a["due"] < today_iso for a in actions),
            "week": sum(today_iso <= a["due"] <= week_iso for a in actions) + sum(bool(c["due"] and c["due"] <= week_iso) for c in chats["signals"]),
            "reply": sum(c["reply"] > 0 for c in chats["signals"]),
            "chat_due": sum(bool(c["due"]) for c in chats["signals"]),
            "news": len(main),
        },
        "stats": {"sources": total_sources, "ok_sources": total_sources - len(failing),
                  "scanned": counts, "shown": len(main), "inbox": inbox},
        "last_run": db.kv_get(conn, "last_run"), "marks": db.kv_get(conn, "run_marks", {}),
    }


def by_topic_total(rest, topic_id):
    return next((g["total"] for g in rest if g["id"] == topic_id), 0)


def auto_brief(view):
    """Сводка без ИИ: короткий честный пересчёт того, что на экране."""
    overdue = [a for a in view["actions"] if a["due"] and a["due"] < date.today().isoformat()]
    soon = [a for a in view["actions"] if a not in overdue]
    parts = []
    if overdue:
        parts.append(f"Просрочено дел: {len(overdue)}.")
    if soon:
        parts.append(f"Ближайшее: {soon[0]['title']}.")
    stats = view["stats"]
    parts.append(f"Из {stats['scanned']} материалов за трое суток на экране {stats['shown']}.")
    if overdue:
        headline = f"Просрочено: {overdue[0]['title']}"
    else:
        headline = view["main"][0]["title"] if view["main"] else "Новостей по вашим темам нет"
    return {"author": "auto", "headline": headline, "summary": " ".join(parts), "sections": []}


def week_view(conn, cfg):
    """Обзор недели: что сделано, что зависло, кто ждёт ответа и чем вы занимались."""
    today = date.today()
    now = datetime.now(timezone.utc)
    week_ago = (today - timedelta(days=7)).isoformat()
    done = [title for title, day in db.kv_get(conn, "done_log", {}).items() if day >= week_ago]
    tasks = [db.row_to_item(r) for r in conn.execute("SELECT * FROM items WHERE kind='task' AND state='new'").fetchall()]
    overdue = sorted((t for t in tasks if t["due"] and t["due"] < today.isoformat() and t["tier"] != "stale"), key=lambda t: t["due"])
    status = Counter(t["meta"].get("status", " ") for t in tasks if t["meta"].get("agenda"))
    chats = chat_view(conn, (now - timedelta(hours=72)).isoformat(), today, cfg)
    waiting = [c for c in chats["signals"] if c["reply"]]
    compiled = compile_topics(cfg)
    activity = recent_activity(conn, compiled, days=7).most_common(3)
    names = {t["id"]: t["name"] for t in compiled}
    found = conn.execute("SELECT COUNT(*) c FROM chat_deadlines WHERE status='added' AND created>=?", ((now - timedelta(days=7)).isoformat(),)).fetchone()["c"]
    return {
        "done": done[-WEEK_SHOWN:][::-1], "done_total": len(done),
        "overdue": [{"title": t["title"], "due": t["due"]} for t in overdue[:WEEK_SHOWN]], "overdue_total": len(overdue),
        "in_work": status.get("/", 0), "waiting_for": status.get("w", 0),
        "unanswered": [{"name": c["name"], "preview": c["preview"]} for c in waiting[:WEEK_SHOWN]], "unanswered_total": len(waiting),
        "topics": [{"name": names[t], "notes": n} for t, n in activity if t in names], "from_chats": found,
    }


def analytics_view(conn, cfg):
    """Куда идёт поток новостей и куда идёт ваше внимание, по темам за 14 дней."""
    now = datetime.now(timezone.utc)
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    since = (now - timedelta(days=14)).isoformat()
    series = {t["id"]: Counter() for t in cfg["topics"]}
    important = Counter()
    for row in conn.execute(
        "SELECT topics, tier, published FROM items WHERE kind IN ('news','post','message') AND published>=?", (since,)
    ).fetchall():
        day = datetime.fromisoformat(row["published"]).astimezone().date().isoformat()
        for topic in json.loads(row["topics"]):
            if topic in series:
                series[topic][day] += 1
                if row["tier"] in ("high", "mid"):
                    important[topic] += 1
    compiled = compile_topics(cfg)
    activity = recent_activity(conn, compiled)
    last7, prev7 = days[7:], days[:7]
    rows = []
    for topic in cfg["topics"]:
        counts = series[topic["id"]]
        rows.append({
            "id": topic["id"], "name": topic["name"], "days": [counts[d] for d in days],
            "week": sum(counts[d] for d in last7), "prev": sum(counts[d] for d in prev7),
            "important": important[topic["id"]], "notes": activity.get(topic["id"], 0),
        })
    sources = []
    for row in conn.execute(
        """SELECT s.name, s.type, s.last_ok, s.last_error, s.last_count,
                  COUNT(i.id) AS total, COALESCE(SUM(i.tier IN ('high','mid')),0) AS useful,
                  COALESCE(SUM(i.feedback<0),0) AS rejected
           FROM sources s LEFT JOIN items i ON i.source_name=s.name AND i.published>=? AND i.kind!='task'
           GROUP BY s.key ORDER BY useful DESC, total DESC""", (since,)).fetchall():
        sources.append(dict(row))
    zones = [dict(r) for r in conn.execute(
        "SELECT zone, COUNT(*) n FROM notes WHERE mtime>=? GROUP BY zone ORDER BY n DESC", (since,)).fetchall()]
    return {"days": days, "topics": rows, "sources": sources, "zones": zones, "week": week_view(conn, cfg),
            "tracked_since": conn.execute("SELECT MIN(collected) m FROM items WHERE kind!='task'").fetchone()["m"]}
