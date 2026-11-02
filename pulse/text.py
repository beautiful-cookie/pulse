"""Русский текст без внешних библиотек: основы слов, ключевые слова, сроки."""
import html
import re
from datetime import date, timedelta

WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.I)
TAG_RE = re.compile(r"<[^>]+>")
WIKILINK_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]")
MDLINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")

RU_ENDINGS = (
    "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "иях", "ией",
    "ах", "ях", "ов", "ев", "ей", "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ые", "ие",
    "ую", "юю", "ом", "ем", "ам", "ям", "ия", "ие", "ию", "ии",
    "а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й",
)
STOP = set(
    "и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне "
    "было вот от меня еще нет о из ему теперь когда даже ну ли если уже или ни быть был него до вас "
    "нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их "
    "чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой "
    "совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при "
    "наконец два об другой хоть после над больше тот через эти нас про всего них какая много разве три "
    "эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно "
    "всю между это также однако году года лет новый новая новые стал стала стали заявил заявила "
    "the a an and or of to in on for with from by at is are was were be been this that it as its "
    "new how why what who will can has have had not but into about over after more than their".split()
)
MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "мая": 5, "июн": 6, "июл": 7,
    "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4, "суббот": 5, "воскресень": 6,
}
UNIT_AFTER = r"\s*(?:ч\b|час|мин|балл|%|лет|раз|руб|тыс|млн|млрд|стр|человек|дн|недел|месяц|процент|:|\.\d|,\d)"


def strip_html(text):
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", text or ""))).strip()


def strip_markdown(text):
    text = WIKILINK_RE.sub(r"\1", text)
    text = MDLINK_RE.sub(r"\1", text)
    return re.sub(r"\*\*|__|`|==", "", text).strip()


def norm(word):
    return word.lower().replace("ё", "е")


def stem(word):
    word = norm(word)
    if not re.search("[а-я]", word):
        return word[:-1] if len(word) > 4 and word.endswith("s") else word
    for end in RU_ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= 3:
            return word[: -len(end)]
    return word


def stems(text):
    """Значимые основы текста, без стоп-слов и чисел."""
    return [stem(w) for w in WORD_RE.findall(text or "") if norm(w) not in STOP and len(w) > 2 and not w.isdigit()]


def keyword_regex(keyword):
    """Ключевое слово в регулярное выражение с учётом русских окончаний.

    КАПС короткой длины (ИИ, ЦБ, AI) ищется точно, с учётом регистра.
    Звёздочка на конце значит «любое продолжение».
    """
    keyword = keyword.strip()
    if len(keyword) <= 4 and keyword.upper() == keyword and "*" not in keyword:
        return re.compile(rf"(?<![\w-]){re.escape(keyword)}(?![\w])")
    parts = []
    for word in keyword.split():
        if word.endswith("*"):
            parts.append(re.escape(norm(word[:-1])) + r"\w*")
        elif re.search("[а-яё]", word, re.I):
            base = re.sub(r"[аеёиоуыэюяйь]+$", "", norm(word), flags=re.I)
            base = base if len(base) >= 3 else norm(word)
            parts.append(re.escape(base) + r"[а-я]{0,4}")
        else:
            parts.append(re.escape(norm(word)) + r"[a-z]{0,3}")
    return re.compile(r"(?<![\w])" + r"[\s-]+".join(parts) + r"(?![\w])", re.I)


def extract_due(text, anchor):
    """Срок из текста относительно даты написания.

    Возвращает (дата, относительный ли срок) или (None, False). «Относительный» значит,
    что слова вроде «сегодня» имели смысл только в день, когда их написали.
    """
    low = norm(text)

    match = re.search(r"(?<![\d.])(\d{1,2})\.(\d{2})(?:\.(\d{2,4}))?(?![\d])", low)
    if match:
        day, month = int(match[1]), int(match[2])
        year = int(match[3]) if match[3] else anchor.year
        year = year + 2000 if year < 100 else year
        result = _make_date(year, month, day, anchor, bool(match[3]))
        if result:
            return result, False

    match = re.search(r"(?<!\d)(\d{1,2})\s+(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)", low)
    if match:
        key = match[2]
        month = 5 if key.startswith("ма") and key != "март" else MONTHS[key]
        result = _make_date(anchor.year, month, int(match[1]), anchor, False)
        if result:
            return result, False

    match = re.search(rf"(?:\bдо|\bк|дедлайн|срок)\s+(\d{{1,2}})(?!\d)(?!{UNIT_AFTER})", low)
    if match:
        day = int(match[1])
        if 1 <= day <= 31:
            month, year = anchor.month, anchor.year
            if day < anchor.day:
                month, year = (1, year + 1) if month == 12 else (month + 1, year)
            result = _make_date(year, month, day, anchor, True)
            if result:
                return result, False

    if re.search(r"\bпослезавтра\b", low):
        return anchor + timedelta(days=2), True
    if re.search(r"\bзавтра\b", low):
        return anchor + timedelta(days=1), True
    if re.search(r"\bсегодня\b", low):
        return anchor, True

    match = re.search(r"\b(?:в|во|к|ко|до|на)\s+(понедельник|вторник|сред|четверг|пятниц|суббот|воскресень)", low)
    if match:
        delta = (WEEKDAYS[match[1]] - anchor.weekday()) % 7 or 7
        return anchor + timedelta(days=delta), True

    return None, False


def _make_date(year, month, day, anchor, year_fixed):
    try:
        result = date(year, month, day)
    except ValueError:
        return None
    if not year_fixed and (anchor - result).days > 60:
        try:
            result = date(year + 1, month, day)
        except ValueError:
            return None
    return result


def first_line_title(text, limit=110):
    line = next((l.strip() for l in (text or "").splitlines() if l.strip()), "")
    if len(line) <= limit:
        return line
    cut = line[:limit]
    return cut[: cut.rfind(" ")].rstrip(" ,;:—-") + "…" if " " in cut else cut + "…"
