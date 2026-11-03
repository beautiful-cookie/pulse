"""Telegram через ваш аккаунт, только чтение.

Здесь намеренно нет ни отправки, ни редактирования, ни удаления, и сообщения
не помечаются прочитанными. Ключи и сессия лежат только в папке data/.
"""
import asyncio
import getpass
import json
import re
import webbrowser
from datetime import datetime, timedelta, timezone

from . import db
from .config import DATA, TG_CREDS_PATH, TG_DIALOGS_PATH, TG_FOLDERS_PATH, TG_QR_PAGE_PATH, TG_SESSION_PATH
from .promises import is_promise
from .text import first_line_title

BACKFILL_DAYS = 3
PER_CHAT_LIMIT = 300
REPLY_LOOKUP_DEPTH = 300
QUICK_DIALOGS = 100  # диалоги идут от свежих к старым: новое всегда в начале списка
QR_REFRESH_SECONDS = 25  # не берём срок из ответа: он зависит от часов компьютера
QR_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta http-equiv="refresh" content="4">
<title>Вход в Telegram для Пульса</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #fff; color: #0b0b0b; font: 16px/1.5 system-ui, "Segoe UI", sans-serif; }
  main { max-width: 420px; padding: 16px; text-align: center; }
  h1 { font-size: 22px; margin: 0 0 12px; }
  svg { width: min(360px, 90vw); height: auto; display: block; margin: 0 auto 12px; }
  p { margin: 0; color: #52514e; }
</style></head><body><main>
  <h1>Отсканируйте код в Telegram</h1>
  {svg}
  <p>На телефоне: Настройки → Устройства → Подключить устройство. Код обновляется сам. После входа окно можно закрыть.</p>
</main></body></html>
"""


def is_connected():
    return TG_CREDS_PATH.exists() and TG_SESSION_PATH.with_suffix(".session").exists()


def make_client():
    from telethon import TelegramClient
    creds = json.loads(TG_CREDS_PATH.read_text("utf-8"))
    return TelegramClient(str(TG_SESSION_PATH), creds["api_id"], creds["api_hash"])


def clean(value):
    return value.strip().strip("'\"").strip()


def parse_api_id(value):
    value = clean(value)
    if not re.fullmatch(r"\d{4,12}", value):
        raise ValueError("api_id состоит только из цифр. Скопируйте поле App api_id.")
    return int(value)


def parse_api_hash(value):
    value = clean(value)
    if not re.fullmatch(r"[0-9a-fA-F]{32}", value):
        raise ValueError(f"api_hash это 32 символа из цифр и букв a-f, а сейчас символов: {len(value)}. Скопируйте поле App api_hash.")
    return value


def ask(prompt, parse, read=None):
    """Спрашивает, пока значение не пройдёт проверку: ошибка не должна обрывать вход."""
    while True:
        try:
            return parse((read or input)(prompt))
        except ValueError as error:
            print(error)


def parse_password(value):
    # Скрытый ввод Windows принимает Ctrl+V за один символ, а не за вставку
    if "\x16" in value:
        raise ValueError("Вставка здесь не работает, наберите пароль вручную.")
    return value


CODE_ROUTES = {
    "SentCodeTypeApp": "в приложение Telegram: чат «Telegram» с синей галочкой, проверьте и архив",
    "SentCodeTypeSms": "в SMS",
    "SentCodeTypeSmsWord": "в SMS, кодом будет слово",
    "SentCodeTypeSmsPhrase": "в SMS, кодом будет фраза",
    "SentCodeTypeFirebaseSms": "в SMS",
    "SentCodeTypeFragmentSms": "на анонимный номер Fragment: смотрите на fragment.com",
    "SentCodeTypeCall": "голосовым звонком",
    "SentCodeTypeFlashCall": "звонком: код находится в номере звонящего",
    "SentCodeTypeMissedCall": "звонком: код находится в номере звонящего",
    "SentCodeTypeEmailCode": "на почту, привязанную к аккаунту",
    "SentCodeTypeSetUpEmailRequired": "никуда: Telegram просит сначала привязать почту в настройках приложения",
}
NEXT_ROUTES = {
    "CodeTypeSms": "в SMS", "CodeTypeCall": "голосовым звонком", "CodeTypeFlashCall": "звонком",
    "CodeTypeMissedCall": "звонком", "CodeTypeFragmentSms": "через Fragment",
}


def describe_delivery(sent):
    """Куда Telegram отправил код и как можно получить его иначе."""
    kind = type(sent.type).__name__
    lines = [f"Telegram отправил код: {CODE_ROUTES.get(kind, kind)}."]
    if sent.next_type:
        route = NEXT_ROUTES.get(type(sent.next_type).__name__, "другим способом")
        wait = f" через {sent.timeout} с" if sent.timeout else ""
        lines.append(f"Если не пришёл, нажмите Enter{wait}: Telegram отправит его {route}.")
    return "\n".join(lines)


async def resend_code(client, phone, sent):
    """Повторная отправка. Отказ Telegram не обрывает вход: старый код ещё может прийти."""
    from telethon.errors import FloodWaitError, RPCError
    if not sent.next_type:
        print("Другого способа Telegram сейчас не предлагает. Подождите код или запустите вход позже.")
        return sent
    try:
        sent = await client.send_code_request(phone)
    except FloodWaitError as error:
        print(f"Telegram просит подождать {error.seconds} с. Не запускайте вход повторно, это удлиняет ожидание.")
        return sent
    except RPCError as error:
        print(f"Повторная отправка не удалась: {error}")
        return sent
    print(describe_delivery(sent))
    return sent


async def enter_password(client):
    from telethon.errors import PasswordHashInvalidError
    while True:
        password = ask("Облачный пароль Telegram (символы не отображаются): ", parse_password, getpass.getpass)
        try:
            await client.sign_in(password=password)
            return
        except PasswordHashInvalidError:
            print("Пароль неверный, попробуйте ещё раз.")


def write_qr_page(url):
    """Страница сама перечитывается, поэтому в браузере всегда свежий код."""
    import qrcode
    from qrcode.image.svg import SvgPathImage
    svg = qrcode.make(url, image_factory=SvgPathImage, box_size=12, border=4).to_string(encoding="unicode")
    TG_QR_PAGE_PATH.write_text(QR_PAGE.replace("{svg}", svg), "utf-8")


async def sign_in_qr(client):
    """Вход сканированием: код из чата не нужен. Страница с токеном живёт только на время входа."""
    from telethon.errors import SessionPasswordNeededError
    qr = await client.qr_login()
    opened = False
    try:
        while True:
            write_qr_page(qr.url)
            if not opened:
                webbrowser.open(TG_QR_PAGE_PATH.as_uri())
                print("QR-код открыт в браузере. Отсканируйте его телефоном:")
                print("Telegram → Настройки → Устройства → Подключить устройство.")
                opened = True
            try:
                await qr.wait(timeout=QR_REFRESH_SECONDS)
                return
            except asyncio.TimeoutError:
                await qr.recreate()
            except SessionPasswordNeededError:
                await enter_password(client)
                return
    finally:
        TG_QR_PAGE_PATH.unlink(missing_ok=True)


async def sign_in(client):
    from telethon.errors import (PhoneCodeExpiredError, PhoneCodeInvalidError, PhoneNumberInvalidError,
                                 SessionPasswordNeededError)
    while True:
        phone = input("Телефон в формате +7...: ").strip()
        try:
            sent = await client.send_code_request(phone)
            break
        except PhoneNumberInvalidError:
            print("Telegram не принял номер. Введите его с кодом страны, например +79991234567.")
    print(describe_delivery(sent))
    while True:
        code = (await asyncio.to_thread(input, "Код (пустой Enter отправит его заново): ")).strip()
        if not code:
            sent = await resend_code(client, phone, sent)
            continue
        try:
            await client.sign_in(phone, code)
            return
        except PhoneCodeInvalidError:
            print("Код неверный, введите ещё раз.")
        except PhoneCodeExpiredError:
            print("Код устарел, запрашиваю новый.")
            sent = await resend_code(client, phone, sent)
        except SessionPasswordNeededError:
            await enter_password(client)
            return


async def authorize(api_id, api_hash):
    from telethon import TelegramClient
    client = TelegramClient(str(TG_SESSION_PATH), api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            choice = input("Способ входа. Enter: QR-код с телефона. 2: код в чате Telegram. Ваш выбор: ").strip()
            await (sign_in(client) if choice == "2" else sign_in_qr(client))
        me = await client.get_me()
        print(f"Вход выполнен: {me.first_name}.")
        await save_dialogs(client)
    finally:
        await client.disconnect()


def login():
    """Запускается один раз в терминале. Все значения вводит сам владелец аккаунта."""
    from telethon.errors import ApiIdInvalidError, RPCError
    DATA.mkdir(exist_ok=True)
    print("Вход в Telegram для Пульса. Ключи: https://my.telegram.org → API development tools.")
    print("Вставка: правая кнопка мыши или Ctrl+V. Значения будут видны на экране, это нормально.\n")
    while True:
        api_id = ask("api_id: ", parse_api_id)
        api_hash = ask("api_hash: ", parse_api_hash)
        try:
            asyncio.run(authorize(api_id, api_hash))
            break
        except ApiIdInvalidError:
            print("\nTelegram не принял эту пару. Проверьте оба значения на my.telegram.org и введите заново.\n")
        except RPCError as error:
            print(f"\nTelegram вернул ошибку: {error}\nПопробуйте ещё раз позже.")
            return
        except OSError as error:
            print(f"\nНет связи с Telegram: {error}\nПроверьте интернет или VPN и запустите вход снова.")
            return
    # Ключи сохраняем только после успешного входа, иначе Пульс сочтёт Telegram подключённым
    TG_CREDS_PATH.write_text(json.dumps({"api_id": api_id, "api_hash": api_hash}), "utf-8")
    print("Готово. Запустите start.bat и выберите чаты: Настройки → Telegram.")


def chat_kind(dialog):
    if dialog.is_user:
        return "бот" if getattr(dialog.entity, "bot", False) else "личный"
    if dialog.is_channel and not dialog.is_group:
        return "канал"
    return "группа"


async def read_tops(client, limit):
    """Последнее сообщение диалогов одним запросом: так видно, в каких чатах есть новое."""
    tops = {}
    async for dialog in client.iter_dialogs(limit=limit):
        tops[str(dialog.id)] = dialog.message.id if dialog.message else 0
    return tops


FOLDER_RULES = ("contacts", "non_contacts", "groups", "broadcasts", "bots", "exclude_archived")


async def read_folders(client):
    """Папки Telegram: название, явный состав и правила вроде «все группы». Только чтение."""
    from telethon import functions, utils
    result = await client(functions.messages.GetDialogFiltersRequest())
    folders = []
    for item in getattr(result, "filters", result):
        if not hasattr(item, "include_peers"):
            continue  # «Все чаты»: у неё нет состава
        title = getattr(item.title, "text", item.title)  # в новых версиях название приходит с разметкой
        peers = list(item.include_peers) + list(getattr(item, "pinned_peers", []))
        folders.append({"title": str(title), "include": {utils.get_peer_id(p) for p in peers},
                        "exclude": {utils.get_peer_id(p) for p in getattr(item, "exclude_peers", [])},
                        "rules": {rule for rule in FOLDER_RULES if getattr(item, rule, False)}})
    return folders


def in_folder(folder, dialog):
    """Чат в папке, если указан в ней явно или подходит под её правила, как в самом Telegram."""
    if dialog["id"] in folder["exclude"]:
        return False
    if dialog["id"] in folder["include"]:
        return True
    rules = folder["rules"]
    if "exclude_archived" in rules and dialog["archived"]:
        return False
    kind = dialog["kind"]
    if kind == "личный":
        return ("contacts" in rules and dialog["contact"]) or ("non_contacts" in rules and not dialog["contact"])
    return {"группа": "groups", "канал": "broadcasts", "бот": "bots"}[kind] in rules


async def save_dialogs(client):
    """Все диалоги, включая архив, и папки, в которых они лежат."""
    dialogs = []
    tops = {}
    async for dialog in client.iter_dialogs():
        tops[str(dialog.id)] = dialog.message.id if dialog.message else 0
        dialogs.append({
            "id": dialog.id, "name": dialog.name or "Без названия", "kind": chat_kind(dialog),
            "unread": dialog.unread_count,
            "last": dialog.date.isoformat() if dialog.date else None,
            "archived": bool(dialog.archived), "contact": bool(getattr(dialog.entity, "contact", False)),
        })
    try:
        folders = await read_folders(client)
    except Exception:  # без папок список всё равно нужен
        folders = []
    for dialog in dialogs:
        dialog["folders"] = [f["title"] for f in folders if in_folder(f, dialog)]
    TG_DIALOGS_PATH.write_text(json.dumps(dialogs, ensure_ascii=False), "utf-8")
    TG_FOLDERS_PATH.write_text(json.dumps([f["title"] for f in folders], ensure_ascii=False), "utf-8")
    return tops


async def refresh_dialogs_async(client):
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("сессия недействительна, нужен повторный вход")
        await save_dialogs(client)
    finally:
        await client.disconnect()


def refresh_dialogs():
    """Кнопка «Обновить список» в настройках: не ждать ночного полного обновления."""
    if not is_connected():
        raise ValueError("Telegram не подключён")
    asyncio.run(refresh_dialogs_async(make_client()))
    return {"dialogs": len(load_dialogs()), "folders": len(load_folders())}


def load_dialogs():
    return json.loads(TG_DIALOGS_PATH.read_text("utf-8")) if TG_DIALOGS_PATH.exists() else []


def load_folders():
    return json.loads(TG_FOLDERS_PATH.read_text("utf-8")) if TG_FOLDERS_PATH.exists() else []


def message_url(chat_id, entity, message_id):
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    text = str(chat_id)
    if text.startswith("-100"):
        return f"https://t.me/c/{text[4:]}/{message_id}"
    return ""


def message_to_item(chat, entity, message, sender_name, outgoing=False):
    """Переводит сообщение Telethon в элемент Пульса."""
    text = (message.message or "").strip()
    is_channel = chat.get("kind") == "канал"  # каналы это новости, а группы и личные чаты приватны
    return {
        "uid": f"tg-{chat['id']}-{message.id}", "kind": "outgoing" if outgoing else "post" if is_channel else "message",
        "source_type": "tg_public" if is_channel else "tg_private",
        "source_name": chat["name"], "title": first_line_title(text), "body": text[:2000],
        "url": message_url(chat["id"], entity, message.id), "author": sender_name,
        "published": message.date.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "meta": {
            "chat_id": chat["id"], "chat_kind": chat.get("kind", "группа"),
            "mentioned": bool(getattr(message, "mentioned", False)),
            "is_reply": bool(getattr(message, "is_reply", False)),
            "chat_weight": chat.get("weight", 1.0),
        },
    }


def sender_label(sender):
    if sender is None:
        return ""
    name = getattr(sender, "title", None) or " ".join(
        p for p in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if p
    )
    return name or getattr(sender, "username", "") or ""


async def my_last_message(client, entity, private):
    """Ваше последнее сообщение в чате.

    В личных чатах Telegram не умеет искать по отправителю: Telethon проверяет сам
    и с limit=1 сдаётся на первом чужом сообщении. Поэтому там смотрим историю.
    """
    wanted = {"limit": REPLY_LOOKUP_DEPTH} if private else {"from_user": "me", "limit": 1}
    async for message in client.iter_messages(entity, **wanted):
        if message.out:
            return message.id
    return 0


async def fetch_chat(client, chat, last_id, needs_lookup=False):
    entity = await client.get_entity(chat["id"])
    if getattr(entity, "bot", False):
        chat = {**chat, "kind": "бот"}
    cutoff = datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)
    items = []
    newest = last_id or 0
    my_last = 0  # после вашего ответа более ранние сообщения уже не ждут реакции
    async for message in client.iter_messages(entity, min_id=last_id or 0, limit=PER_CHAT_LIMIT):
        newest = max(newest, message.id)  # тихий чат тоже получает отметку, иначе его перечитывают каждый раз
        if message.date < cutoff:
            break
        text = (message.message or "").strip()
        if message.out:
            my_last = max(my_last, message.id)
            if chat.get("kind") != "канал" and is_promise(text):
                items.append(message_to_item(chat, entity, message, "", outgoing=True))
            continue
        if not text:
            continue
        sender = await message.get_sender()
        items.append(message_to_item(chat, entity, message, sender_label(sender)))
    # Сбор идёт от прошлой точки: ответ, отправленный раньше неё, здесь не увидеть
    if needs_lookup and chat.get("kind") != "канал":
        private = chat.get("kind") in ("личный", "бот")
        my_last = max(my_last, await my_last_message(client, entity, private))
        return items, newest, my_last
    return items, newest, my_last or None


def has_news(chat_id, tops, cursors, asked):
    """Быстрый проход читает только чаты, где последнее сообщение новее отметки."""
    key = str(chat_id)
    if key not in cursors or key not in asked:
        return True  # чат только что добавили
    return tops.get(key, 0) > cursors[key]


async def collect_async(client, chats, cursors, asked=(), full=False):
    results = []
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("сессия недействительна, нужен повторный вход")
        tops = await save_dialogs(client) if full else await read_tops(client, QUICK_DIALOGS)
        for chat in chats:
            if not full and not has_news(chat["id"], tops, cursors, asked):
                continue
            try:
                last_id = None if full else cursors.get(str(chat["id"]))
                items, newest, my_last = await fetch_chat(client, chat, last_id, full or str(chat["id"]) not in asked)
                results.append((chat, items, newest, my_last, None))
            except Exception as error:
                results.append((chat, [], None, None, f"{type(error).__name__}: {str(error)[:160]}"))
    finally:
        await client.disconnect()
    return results


def collect(conn, cfg, full=False):
    """Быстрый проход смотрит одну страницу диалогов, полный перечитывает все чаты за три дня."""
    if not is_connected():
        return 0
    chats = cfg["tg_private"]
    cursors = db.kv_get(conn, "tg_cursors", {})
    my_last_seen = db.kv_get(conn, "tg_my_last", {})
    asked = set(db.kv_get(conn, "tg_replies_asked", []))
    try:
        results = asyncio.run(collect_async(make_client(), chats, cursors, asked, full))
    except Exception as error:
        db.record_source(conn, "tg_private", "Telegram: аккаунт", "tg_private", error=f"{type(error).__name__}: {str(error)[:160]}")
        return 0
    total = 0
    for chat, items, newest, my_last, error in results:
        for item in items:
            db.upsert_item(conn, item)
        if newest:
            cursors[str(chat["id"])] = max(newest, cursors.get(str(chat["id"]), 0))
        if my_last is not None:  # ноль тоже ответ: значит, вы здесь ещё не писали
            my_last_seen[str(chat["id"])] = max(my_last, my_last_seen.get(str(chat["id"]), 0))
            asked.add(str(chat["id"]))
        db.record_source(conn, f"tg:{chat['id']}", chat["name"], "tg_private", count=len(items), error=error)
        total += len(items)
    db.kv_set(conn, "tg_cursors", cursors)
    db.kv_set(conn, "tg_my_last", my_last_seen)
    db.kv_set(conn, "tg_replies_asked", sorted(asked))
    db.record_source(conn, "tg_private", "Telegram: аккаунт", "tg_private", count=total)
    return total
