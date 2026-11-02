"""Экзамен локальной модели на вымышленных сообщениях: python -X utf8 tests/model_exam.py

Настоящие чаты сюда не попадают. Показывает точность, полноту, скорость.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pulse import assistant, deadlines, local_ai, summaries
from pulse.config import DATA, load_config

REPORT = DATA / "model_exam.txt"
_report = REPORT.open("w", encoding="utf-8")


def say(*parts):
    """Прогресс виден сразу на экране и сохраняется в отчёт."""
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    _report.write(line + "\n")
    _report.flush()

SHOULD_ADD = [
    "Ребята, дедлайн по эссе 6 ноября, сдаём в LMS до 23:59",
    "Напоминаю: защита проектов в четверг, презентации прислать до среды",
    "Экзамен по статистике перенесли на 12 ноября, аудитория 305",
    "Анкету на стажировку надо заполнить до пятницы, иначе не возьмут",
    "Домашка по методам нейронауки: сдать до 10.11",
    "Оплатить взнос за конференцию нужно до 15 ноября",
    "Заявки на грант принимаются до 20 ноября, успейте подать",
    "Контрольная по нейрофизиологии в понедельник, принесите бланки",
]
SHOULD_SKIP = [
    "Когда дедлайн по эссе, кто-нибудь знает?",
    "Экзамен прошёл нормально, всем спасибо!",
    "Дедлайн по жизни у меня уже прошёл, ахах",
    "Сдал вчера домашку, наконец-то",
    "До пятницы буду в отъезде, пишите в почту",
    "Посмотрел сериал про экзамены, очень смешно",
    "Тест на беременность Кате в пятницу расскажем, шучу",
    "Кто идёт в бар в пятницу?",
    "До среды отвечаю только в почте, телефон в ремонте",
]
ARTICLE_TITLE = "Сон после обучения улучшает память, но не у всех"
ARTICLE = """Исследователи из трёх университетов проверили, как дневной сон влияет на запоминание слов.
В опыте участвовали 412 студентов. Половина спала 60 минут после заучивания, половина бодрствовала.
Через неделю группа со сном вспоминала на 14% больше слов. Эффект был сильнее у тех, кто обычно спит меньше 7 часов.
Авторы заранее зарегистрировали гипотезы. Участники были только студентами 18-25 лет, поэтому переносить вывод на другой возраст нельзя.
Авторы не измеряли фазы сна, так что механизм остаётся неизвестным."""


def main():
    cfg = load_config()
    say("Запускаю модель на вымышленных сообщениях. Загрузка занимает до 3 минут.")
    with local_ai.running_model(cfg) as (model_cfg, model):
        say("модель:", model)
        if not model:
            say("Модель не запустилась. Пришлите этот экран в чат.")
            return
        say(f"Проверяю {len(SHOULD_ADD) + len(SHOULD_SKIP)} сообщений и пересказ чата.")
        started = time.monotonic()
        hits, misses, false_alarms = 0, [], []
        for text in SHOULD_ADD + SHOULD_SKIP:
            message = {"body": text, "source_name": "Поток"}
            first = time.monotonic()
            answer = deadlines.decide_with_model(model_cfg, model, message)
            seconds = time.monotonic() - first
            say(f"{seconds:5.1f} с | {'ДА ' if answer else 'нет'} | {text[:52]} -> {answer}")
            if text in SHOULD_ADD:
                hits += bool(answer)
                if not answer:
                    misses.append(text)
            elif answer:
                false_alarms.append(text)
        total = len(SHOULD_ADD) + len(SHOULD_SKIP)
        say(f"\nнайдено {hits} из {len(SHOULD_ADD)} | ложных {len(false_alarms)} из {len(SHOULD_SKIP)} | "
            f"в среднем {(time.monotonic() - started) / total:.1f} с на сообщение")
        chat = [{"author": "Анна", "body": "Семинар в среду перенесли на 12:00"},
                {"author": "Олег", "body": "А эссе тогда до какого числа?"},
                {"author": "Анна", "body": "Эссе сдаём до 6 ноября, объём 5 страниц"}]
        started = time.monotonic()
        say("\nпересказ чата:", local_ai.summarize(model_cfg, model, "Поток", chat),
            f"| {time.monotonic() - started:.1f} с")
        started = time.monotonic()
        user = f"Заголовок: {ARTICLE_TITLE}\n<article>\n{ARTICLE}\n</article>"
        answer = local_ai.ask(model_cfg, model, summaries.SUMMARY_PROMPT, user, max_tokens=400)
        say(f"\nсаммари статьи, {time.monotonic() - started:.1f} с:")
        result = summaries.parse_summary(answer)
        say("суть:", result["gist"])
        for point in result["points"]:
            say(" -", point)
        say("оговорки:", result["limits"])
        say("годится:", summaries.is_usable(result))
        data = ("[Чат «Поток», Анна, 02.11] Эссе сдаём до 6 ноября\n[Дело, срок 2026-11-07] Заполнить анкету\n"
                "[Чат «Лекторий», Игорь, 01.11] Лекция в четверг в 18:00, аудитория 305")
        for question in ("Когда сдавать эссе?", "Что важного в чатах?", "Где пройдёт лекция?"):
            started = time.monotonic()
            raw = local_ai.ask(model_cfg, model, assistant.SYSTEM_PROMPT, f"<data>\n{data}\n</data>\nВопрос: {question}", max_tokens=assistant.ANSWER_TOKENS)
            try:
                reply = assistant.final_answer(raw)
            except ValueError as error:
                reply = f"ОТКАЗ: {error}"
            say(f"\nвопрос: {question} | {time.monotonic() - started:.1f} с\nответ: {reply}\nсырое начало: {raw[:120]!r}")
    say(f"\nГотово. Отчёт: {REPORT}")


try:
    main()
except Exception as error:
    say("Ошибка:", type(error).__name__, error)
