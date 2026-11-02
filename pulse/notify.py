"""Уведомления Windows: Пульс сам сообщает о важном. Текст остаётся на этом компьютере."""
import os
import subprocess
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from . import db

WAIT_HOURS = 24
SHOWN_PER_RUN = 3
REMEMBERED = 300
PORT = int(os.environ.get("PULSE_PORT", 8765))
SCRIPT = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] > $null;"
    "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; $xml.LoadXml($env:PULSE_TOAST);"
    "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml;"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($toast)"
)


def toast_xml(title, text):
    return (f'<toast activationType="protocol" launch="http://127.0.0.1:{PORT}/"><visual><binding template="ToastGeneric">'
            f"<text>{escape(title)}</text><text>{escape(text)}</text></binding></visual></toast>")


def show(title, text):
    """Текст идёт через окружение: так в команду не попадёт ничего из сообщений."""
    if os.name != "nt":
        return
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT], env={**os.environ, "PULSE_TOAST": toast_xml(title, text)},
                   creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, timeout=20, check=False)


def is_quiet(hour, options):
    start, end = options.get("quiet_from", 23), options.get("quiet_to", 9)
    return hour >= start or hour < end if start > end else start <= hour < end


def alerts(view, today, now):
    """Срок завтра и люди, которые ждут ответа больше суток."""
    found = []
    tomorrow = (today + timedelta(days=1)).isoformat()
    for task in view["actions"]:
        if task["due"] == tomorrow:
            found.append((f"due:{task['uid']}:{task['due']}", "Срок завтра", task["title"]))
    for chat in view["chats"]["signals"]:
        since = chat.get("waiting_since")
        important = chat["level"] == "всегда" or chat["kind"] == "личный"
        if chat["reply"] and since and important and now - datetime.fromisoformat(since) >= timedelta(hours=WAIT_HOURS):
            found.append((f"reply:{chat['name']}:{since}", "Ждут ответа больше суток", chat["name"]))
    return found


def check(conn, cfg, view, today):
    """Показывает новое и запоминает показанное. Возвращает, сколько было нового."""
    options = cfg.get("notify", {})
    moment = datetime.now().astimezone()
    if not options.get("enabled", True) or is_quiet(moment.hour, options):
        return 0
    shown = db.kv_get(conn, "notified", [])
    fresh = [a for a in alerts(view, today, datetime.now(timezone.utc)) if a[0] not in shown]
    for _, title, text in fresh[:SHOWN_PER_RUN]:
        show(title, text)
    if len(fresh) > SHOWN_PER_RUN:
        show("Пульс", f"И ещё {len(fresh) - SHOWN_PER_RUN}")
    db.kv_set(conn, "notified", (shown + [a[0] for a in fresh])[-REMEMBERED:])
    return len(fresh)
