"""Пути и настройки «Пульса». Темы и источники по умолчанию взяты из
«20 Области/Новости/Обзор.md»: те же темы, первоисточники и чёрный список."""
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("PULSE_HOME", ROOT))  # демо живёт в своей папке и не видит настоящих чатов
DATA = HOME / "data"
CONFIG_PATH = HOME / "config.json"
DB_PATH = DATA / "pulse.db"
BRIEF_PATH = DATA / "brief.json"
EXPORT_PATH = DATA / "for_claude.md"
TG_CREDS_PATH = DATA / "telegram.json"
TG_SESSION_PATH = DATA / "telegram"
TG_DIALOGS_PATH = DATA / "tg_dialogs.json"
TG_QR_PAGE_PATH = DATA / "telegram_qr.html"

_lock = threading.Lock()


def gn(query, lang="ru"):
    """Лента поиска Google News: так любая тема получает новости без ручного подбора RSS."""
    from urllib.parse import quote
    region = {"ru": "hl=ru&gl=RU&ceid=RU:ru", "en": "hl=en-US&gl=US&ceid=US:en"}[lang]
    return f"https://news.google.com/rss/search?q={quote(query + ' when:3d')}&{region}"


DEFAULT_CONFIG = {
    "vault": r"C:\Users\User\Documents\Obsidian Vault",
    "collect_interval_min": 60,
    "budget_main": 7,
    "topic_cap": 3,
    "news_window_days": 4,
    "me": {"names": ["Максим", "Хадеев"]},
    "privacy": {"claude_reads_chats": False},
    "local_ai": {"url": "http://127.0.0.1:1234/v1", "external": False, "max_chats_per_run": 15},
    "active_mode": "all",
    "modes": [
        {"id": "all", "name": "Всё", "budget": 7, "chats": "all", "topics": {}},
        {"id": "study", "name": "Учёба", "budget": 5, "chats": "all", "topics": {
            "study": 1.8, "neuro": 1.5, "psych": 1.5, "consciousness": 1.2, "career": 1.2,
            "startups": 0.7, "russia": 0.5, "world": 0.5, "culture": 0.3}},
        {"id": "projects", "name": "Проекты", "budget": 5, "chats": "all", "topics": {
            "startups": 1.8, "ai": 1.5, "neuro": 1.3, "career": 1.3, "study": 0.7, "world": 0.5, "culture": 0.3}},
        {"id": "rest", "name": "Отдых", "budget": 3, "chats": "important", "topics": {
            "culture": 1.5, "world": 1.0, "startups": 0.5, "career": 0.5, "study": 0.3}},
    ],
    "remote": {"enabled": False, "key": ""},
    "deadlines": {"auto_add": True, "max_per_run": 5},
    "claude": {"enabled": True, "model": "haiku", "digest_model": "sonnet"},
    "blacklist": [
        "тренды года", "топ-10", "как заработать", "бесплатный вебинар",
        "инфобизнес", "марафон желаний", "купон", "промокод",
        "Publisher Correction", "Author Correction",
    ],
    "urgent_terms": [
        "дедлайн", "сдать", "сдача", "экзамен", "зач[её]т", "перенос", "перенесли",
        "отмен", "тест", "домашк", "контрольн", "срочно", "пересдач", "явка",
    ],
    "topics": [
        {"id": "consciousness", "name": "Философия сознания", "weight": 1.4, "keywords": [
            "сознание", "квалиа", "панпсихизм", "панквалитизм", "трудная проблема",
            "феноменальн*", "интегрированная информация", "consciousness", "qualia",
            "panpsychism", "IIT", "global workspace", "Chalmers", "Чалмерс", "Tononi",
            "Тонони", "Schwitzgebel", "Friston", "Фристон", "McFadden",
        ], "exclude": ["потерял* сознание", "потеряла сознание", "без сознания", "в сознание", "в сознании", "lost consciousness"]},
        {"id": "neuro", "name": "Нейронаука и нейротех", "weight": 1.4, "keywords": [
            "нейронаук*", "нейротехнолог*", "нейроинтерфейс*", "нейромодуляц*", "мозг",
            "нейрон*", "BCI", "brain-computer", "brain implant", "Neuralink", "Synchron",
            "neuroscience", "neural circuit", "fMRI", "фМРТ", "ЭЭГ", "EEG", "диптех",
        ], "exclude": []},
        {"id": "psych", "name": "Психология и когнитивная наука", "weight": 1.2, "keywords": [
            "психолог*", "когнитивн*", "репликац*", "психотерап*",
            "психоделик*", "psychology", "cognitive", "replication", "decision-making",
            "psilocybin", "псилоцибин",
        ], "exclude": ["психолог рассказал*", "психолог назвал*", "психолог объяснил*"]},
        {"id": "ai", "name": "ИИ и инструменты", "weight": 1.2, "keywords": [
            "ИИ", "AI", "нейросет*", "языковая модель", "LLM", "Claude", "Anthropic",
            "OpenAI", "GPT", "Gemini", "DeepMind", "DeepSeek", "Mistral", "AGI",
            "AI safety", "model card", "agentic",
        ], "exclude": []},
        {"id": "startups", "name": "Стартапы и предпринимательство", "weight": 1.0, "keywords": [
            "стартап*", "венчур*", "посевн*", "startup", "seed round", "Series A",
            "Y Combinator", "кастдев", "deeptech", "deep tech", "акселератор*",
        ], "exclude": []},
        {"id": "russia", "name": "Россия: политика и экономика", "weight": 0.8, "keywords": [
            "ЦБ", "Центробанк", "ключевая ставка", "ключевую ставку", "Госдума", "законопроект",
            "Набиуллина", "инфляц*", "рубл*", "бюджет",
        ], "exclude": []},
        {"id": "world", "name": "Мир", "weight": 0.6, "keywords": [
            "Трамп", "ООН", "НАТО", "Евросоюз", "переговор*", "перемири*", "Иран",
            "Китай", "Ормузск*", "Украин*",
        ], "exclude": []},
        {"id": "culture", "name": "Культура и интернет", "weight": 0.4, "keywords": [
            "мем*", "виральн*", "блогер*", "сериал*", "TikTok", "Twitch",
        ], "exclude": []},
        {"id": "career", "name": "Возможности: стажировки и гранты", "weight": 1.3, "keywords": [
            "стажировк*", "ваканси*", "грант*", "летняя школа", "стипенди*", "студенческая олимпиада",
            "ШАД", "хакатон*", "internship", "fellowship", "PhD position",
        ], "exclude": ["президентский грант на*"]},
        {"id": "study", "name": "ВШЭ и учёба", "weight": 1.0, "activity_boost": False, "keywords": [
            "ВШЭ", "НИУ ВШЭ", "LMS", "SmartLMS", "майнор*", "экзамен*",
            "пересдач*", "семинар*", "лекци*",
        ], "exclude": []},
    ],
    "feeds": [
        {"name": "Nature Neuroscience", "url": "https://www.nature.com/neuro.rss", "topic": "neuro", "primary": True},
        {"name": "Nature Human Behaviour", "url": "https://www.nature.com/nathumbehav.rss", "topic": "psych", "primary": True},
        {"name": "bioRxiv: нейронаука", "url": "https://connect.biorxiv.org/biorxiv_xml.php?subject=neuroscience", "topic": "neuro", "primary": True, "filter": True},
        {"name": "arXiv q-bio.NC", "url": "https://rss.arxiv.org/rss/q-bio.NC", "topic": "neuro", "primary": True, "filter": True},
        {"name": "arXiv cs.AI: о сознании", "url": "https://rss.arxiv.org/rss/cs.AI", "primary": True, "filter": True, "only_topics": ["consciousness"]},
        {"name": "The Transmitter", "url": "https://www.thetransmitter.org/feed/", "topic": "neuro"},
        {"name": "Quanta Magazine", "url": "https://www.quantamagazine.org/feed/"},
        {"name": "Aeon", "url": "https://aeon.co/feed.rss", "filter": True},
        {"name": "The Splintered Mind (Швицгебель)", "url": "https://schwitzsplinters.blogspot.com/feeds/posts/default?alt=rss", "topic": "consciousness", "primary": True},
        {"name": "OpenAI", "url": "https://openai.com/news/rss.xml", "topic": "ai", "primary": True},
        {"name": "Google DeepMind", "url": "https://deepmind.google/blog/rss.xml", "topic": "ai", "primary": True},
        {"name": "Hacker News: лучшее", "url": "https://hnrss.org/best", "filter": True},
        {"name": "Хабр: ИИ", "url": "https://habr.com/ru/rss/hubs/artificial_intelligence/articles/?fl=ru", "topic": "ai", "filter": True},
        {"name": "N+1", "url": "https://nplus1.ru/rss", "filter": True},
        {"name": "vc.ru", "url": "https://vc.ru/rss", "filter": True},
        {"name": "Коммерсантъ", "url": "https://www.kommersant.ru/RSS/news.xml", "primary": True, "filter": True},
        {"name": "Интерфакс", "url": "https://www.interfax.ru/rss.asp", "primary": True, "filter": True},
        {"name": "Русская служба BBC", "url": "https://feeds.bbci.co.uk/russian/rss.xml", "topic": "world", "filter": True},
        {"name": "Meduza", "url": "https://meduza.io/rss/all", "topic": "world", "filter": True},
        {"name": "Поиск: Anthropic", "url": gn("Anthropic Claude", "en"), "topic": "ai"},
        {"name": "Поиск: нейротехнологии", "url": gn("нейротехнологии OR нейроинтерфейс OR диптех"), "topic": "neuro"},
        {"name": "Поиск: consciousness", "url": gn("consciousness science OR panpsychism OR qualia", "en"), "topic": "consciousness"},
        {"name": "Поиск: psychology", "url": gn("psychology study OR cognitive science OR replication psychology", "en"), "topic": "psych"},
        {"name": "Поиск: психология", "url": gn("психологи исследование когнитивная психология"), "topic": "psych"},
        {"name": "Поиск: стажировки", "url": gn("стажировка студентов психология OR нейронаука OR аналитика"), "topic": "career"},
        {"name": "Поиск: ВШЭ", "url": gn("НИУ ВШЭ студенты"), "topic": "study"},
    ],
    "tg_public": [],
    "tg_private": [],
    "obsidian": {
        "include": ["00 Система/Инбокс", "10 Проекты", "20 Области", "30 Ресурсы", "Старт.md"],
        "exclude": ["20 Области/Новости/Дайджесты", "20 Области/Новости/Сводки", "00 Система/Шаблоны", "40 Архив"],
        "agenda_notes": ["00 Система/Инбокс/Актуальное.md"],
        "journal": "20 Области/Новости/Журнал углублений.md",
        "registry": "00 Система/Повторение/Реестр.md",
        "stale_days": 45,
    },
}


LEVELS = {"всегда": 1.5, "обычно": 1.0, "тише": 0.4, "никогда": 0.0}
DEFAULT_LEVEL = "обычно"


def chat_levels(cfg):
    """Важность чатов и каналов по названию."""
    return {c["name"]: c.get("level", DEFAULT_LEVEL) for c in cfg.get("tg_private", [])}


def muted_chats(cfg):
    return {name for name, level in chat_levels(cfg).items() if level == "никогда"}


def active_mode(cfg):
    modes = cfg.get("modes") or DEFAULT_CONFIG["modes"]
    return next((m for m in modes if m["id"] == cfg.get("active_mode")), modes[0])


def load_config():
    with _lock:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
        else:
            cfg = json.loads(json.dumps(DEFAULT_CONFIG))
            _write(cfg)
    for key, value in DEFAULT_CONFIG.items():
        cfg.setdefault(key, value)
    return cfg


def save_config(cfg):
    with _lock:
        _write(cfg)


def _write(cfg):
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(CONFIG_PATH)
