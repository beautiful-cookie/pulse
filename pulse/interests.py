"""Что вам интересно: за кем следить и какие темы важны. Задаётся в настройках или словами
в окне «Спросить»: «Следи за .intuition», «Мне интересна философия сознания»."""
import re
from datetime import datetime, timedelta, timezone

from .config import save_config
from .text import stems

NAME_WORD = 4
KNOWN_DAYS = 30
WATCH = re.compile(r"^\s*(?:обращай внимание на|следи за|отслеживай|присматривай за|запомни,?\s*что мне интересн\w*|мне интересн\w*)\s*:?\s*(.+)$", re.I)
FORGET = re.compile(r"^\s*(?:не следи за|перестань следить за|больше не следи за|забудь(?:\s+про)?|мне больше не интересн\w*|мне не интересн\w*)\s*:?\s*(.+)$", re.I)
SHOW = re.compile(r"что ты (?:обо мне )?(?:знаешь|помнишь)|за (?:кем|чем) (?:ты )?следишь|что мне интересно\??\s*$", re.I)
FILLER = set(stems("что чем чём пишет пишут пишется говорит говорят канал канале каналы чат чате чаты человек сообщения посты новое"))


def profile(cfg):
    prefs = cfg.get("interests") or {}
    return {"about": list(prefs.get("about", [])), "watch": list(prefs.get("watch", []))}


def name_words(text):
    return {w for w in stems(text) if len(w) >= NAME_WORD}


def known_names(conn, cfg):
    """Выбранные чаты и каналы и авторы сообщений за месяц: за ними можно следить."""
    names = {c["name"] for c in cfg["tg_private"]} | {c.get("name") or c["username"] for c in cfg["tg_public"]}
    since = (datetime.now(timezone.utc) - timedelta(days=KNOWN_DAYS)).isoformat()
    names |= {r[0] for r in conn.execute(
        "SELECT DISTINCT author FROM items WHERE kind IN ('message','post') AND author!='' AND published>=?", (since,)).fetchall()}
    return sorted(n for n in names if n)


def match_names(part, names):
    """Названия, в которых есть все слова части фразы: «пишет Антон Кузнецов» находит канал Кузнецова."""
    words = name_words(part) - FILLER
    if not words:
        return []
    # Все слова должны быть в названии: «философия сознания» тема, а не канал со словом «философия»
    # Начало слова вместо основы: «Телемским» и «Телемский» расходятся окончанием, которого нет в словаре
    covered = lambda name: all(any(w.startswith(n) or n.startswith(w) for n in name_words(name)) for w in words)
    return [name for name in names if covered(name)]


def split_parts(text):
    parts = re.split(r",|;|\n|\s+и\s+|\s+а также\s+", text)
    return [p.strip(" .!?«»\"'") for p in parts if p.strip(" .!?«»\"'")]


def describe(prefs):
    watch = ", ".join(prefs["watch"]) or "пока ни за кем"
    about = ", ".join(prefs["about"]) or "тем пока нет"
    return f"Слежу за: {watch}.\nИнтересные вам темы: {about}.\nСказать мне можно так: «Следи за …», «Мне интересна …», «Не следи за …»."


def same(a, b):
    return a.strip().lower() == b.strip().lower()


def command(conn, cfg, question):
    """Просьба запомнить или забыть интерес. None, если это обычный вопрос."""
    if SHOW.search(question):
        return describe(profile(cfg))
    found = WATCH.match(question) or FORGET.match(question)
    if not found or question.rstrip().endswith("?"):  # «Мне интересно, писали ли…?» это вопрос
        return None
    forgetting = bool(FORGET.match(question))
    prefs = profile(cfg)
    names = known_names(conn, cfg) + prefs["watch"]
    people, topics = [], []
    for part in split_parts(found[1]):
        matched = match_names(part, names)
        if matched:
            people += [n for n in matched if not any(same(n, p) for p in people)]
        elif name_words(part) - FILLER:
            topics.append(part)
    if forgetting:
        prefs["watch"] = [w for w in prefs["watch"] if not any(same(w, p) for p in people)]
        prefs["about"] = [a for a in prefs["about"] if not any(same(a, t) for t in topics)]
    else:
        prefs["watch"] += [p for p in people if not any(same(p, w) for w in prefs["watch"])]
        prefs["about"] += [t for t in topics if not any(same(t, a) for a in prefs["about"])]
    cfg["interests"] = prefs
    save_config(cfg)
    if not people and not topics:
        return "Не понял, о ком или о чём речь. Напишите название канала, чата или имя, например «Следи за .intuition»."
    verb = "Больше не слежу за" if forgetting else "Запомнил. Слежу за"
    lines = [f"{verb}: {', '.join(people)}."] if people else []
    if topics:
        lines.append(f"{'Убрал темы' if forgetting else 'Интересные темы'}: {', '.join(topics)}.")
    return "\n".join(lines)
